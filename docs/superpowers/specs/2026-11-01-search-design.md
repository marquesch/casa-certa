# Search for new houses: design

Extends `2026-10-31-casa-certa-design.md` (the "Later: search by request" row) and reuses the source-adapter pattern of
`2026-10-31-cheaper-sources-design.md`.

## Why

Today the user collects Airbnb URLs by hand and `casa-certa run` compares them. Finding the candidates is the slow part.
The tool should find them from the same `trip.yaml` (places, dates, group size, aspects) and then compare them with the
pipeline that already exists.

## Goals

- Search Airbnb for houses in one or more free-text places (`Itapoá, SC`, `Jardim Botânico, Curitiba`), for the trip dates and
  group size, screen them cheaply against the `must_have` aspects using only the free listing data, and fully analyse every house
  that is not clearly failing a must-have, with the existing analysis and the existing cache.
- One command, one yaml. The places are optional: a yaml with only `urls` keeps working exactly as today.
- Searching and analysing can be done together or separately, and the user is asked what to do when no flag is given.
- Report what each analysed house cost and the running total of the run.

## Non-goals and anti-goals

- No beach filter or beach flag on the CLI or in search. "Próximo à praia" is an ordinary `must_have` aspect judged by the analysis.
  This also works for non-beach trips (Curitiba near Jardim Botânico).
- No manual shortlist step: the user does not pick from the search results. Every house that passes the automatic screen
  (see Screening) is analysed.
- No coordinate or distance filter and no `near:` option in this version (decided: it cannot express several beaches in several
  cities). It may be added later if the screen proves too loose.
- Never call Airbnb's private API directly (it was blocked as circumvention). Search goes through an Apify actor behind an adapter.
- Do not rewrite or reformat the user's `trip.yaml` from code. Found houses are stored in the database, not written to the yaml.
- Anti-goal: re-spending money on a house that is already stored. A failure that makes cached houses get re-analysed is a defect.

## Configuration

`TripConfig` gets an optional `search` block. `urls` stops being required, but at least one of `urls` or `search` must be present
(a validation error otherwise). Existing fields are reused: `check_in`, `check_out`, `max_people`, `aspects`.

```yaml
search:
  locations:                  # free text, one search each
    - Itapoá, SC
    - Jardim Botânico, Curitiba
  max_results: 40             # per location; bounds the cost
  max_price_per_night: 1500   # optional, BRL
  min_rating: 4.5             # optional
```

Search requests capacity of at least `max_people` paying guests (infants are passed as in the price quotes). Only fields Airbnb search
can filter on go to the actor (location, dates, guests, optional price and rating). Aspects are not sent to the search: they are judged
by the analysis afterwards.

Because `trip.yaml` is the user's personal, gitignored file, the implementation must add the new `search:` block (with the user's
places left for them to fill in or a sensible commented default) to the existing `trip.yaml` **without changing any other
line**, and must document every field with comments in the tracked `trip.example.yaml`. (The user wrote `trip.yaml.example`;
the repository convention in `CLAUDE.md` is `trip.example.yaml`, which already exists, so that file is used. Builder's call, flagged.)

## Command line

`casa-certa run trip.yaml [--search] [--analyse] [--refresh] [--dry-run] [--yes] [--output PATH]`

| Flags | Behaviour |
|---|---|
| `--search` | Search the places, print the houses found, store them as "the last search". Analyses nothing. |
| `--analyse` | Analyse the yaml `urls` plus the houses of the last search, skipping those already analysed. |
| `--search --analyse` | Both in one run, with no questions at all, the cost confirmation included (it implies `--yes`). The plan and costs are still printed and logged. |
| no flags | Interactive: if the yaml has a `search:` block, ask "Search?"; then ask "Analyse everything found?" (the cost is shown before the second answer). Without a `search:` block only the analyse question applies, as the old `run` did, including its cost confirmation. |

`--refresh`, `--dry-run` (prints what would be searched/fetched and the estimated cost, spends nothing) and `--yes` keep their meanings.
`--dry-run` with `--search` does not call the search actor; it prints the search it would run. An `--analyse` with nothing to analyse
(no urls, no stored search) is an error that says to run `--search` first.

## Search source

A third adapter next to the price and review sources:

```python
class SearchSource(Protocol):
    name: str
    async def search(self, query: SearchQuery) -> SourceBatch[...]  # builder shapes the exact return type
    def estimate_usd(self, results: int) -> float: ...
```

`SearchQuery` is a Pydantic model (location, check_in, check_out, guests, infants, max_results, optional price/rating limits). The
result per hit is a Pydantic model with at least the Airbnb room id and URL. Chosen by a new setting `SEARCH_SOURCES` (ordered
fallback chain, like `PRICE_SOURCES`), registered in `sources/registry.py`. The first candidate actor is `tri_angle/airbnb-scraper`
(~$0.004 per listing, earmarked in the original spec). Whether it honours location text, dates, guests and capacity is **not verified**:
the builder must check it with a live cross-check test marked `@pytest.mark.live` (like `make test-live-sources`, a few cents) before
making it the default, and may pick a different Apify actor if it does not. Tests use fakes.

A search that fails for one place is reported and does not stop the other places (same rule as one failed house).

## Last search (storage)

Typed tables, no JSON columns (new Alembic migration):

- `search_run`: id, created_at, check_in, check_out, guests. Only the latest run is kept; a new `--search` replaces it.
- `search_hit`: search_run_id, location (the text the user wrote), airbnb_id, url, title (nullable), position. Unique on (run, airbnb_id).

A house found by two places appears once. `--analyse` reads the hits of the latest run.

## Screening (the funnel)

Order for houses that come from a search: search, free page fetch, **screen**, then the paid steps (reviews, price quotes, naming,
analysis) only for houses that pass.

- **Input:** only free listing data: title, description, location name, highlights, amenities with availability, coordinates.
  No reviews, no Apify.
- **What it judges:** every `must_have` aspect, using its `name` and `details`. Nothing is hard-coded to beaches, so the same
  mechanism covers "near Jardim Botânico" or any other must-have.
- **Output** (Pydantic, per must-have): status `meets` / `unclear` / `fails`, a short reason, and a quote from the listing.
- **Decision (user's):** a house is dropped **only if at least one must-have is `fails`**. `unclear` passes and goes to the full
  analysis, which reads the reviews. Reason: hosts exaggerate ("pé na areia") and the real distance is often only in the reviews.
- **Model:** a new agent `HouseScreener` next to `HouseNamer`/`HouseAnalyst`, model from a new setting `SCREEN_MODEL`
  (`deepseek-chat` today). Estimated at well under a cent for several houses.
- **Scope:** only houses found by the search are screened. Houses the user listed in `urls` are never screened out (the user chose them).
  If a yaml has no must-have aspects, the screen is skipped and everything passes.
- **Auditability:** a screened-out house is never silent. It is logged with its reason and shown in the run output as
  "screened out: <reason>". To rescue one, the user adds its URL to `urls`.
- **Cache:** the result is stored and keyed by a hash of the must-have aspects (name + details). A stored result is not recomputed
  (and, for a screened-out house, the page is not fetched again) unless `--refresh` or the must-haves changed. Storage is typed
  tables, no JSON: `screen_result` (airbnb_id unique, url, title, passed, input_hash, model, created_at) and `screen_verdict`
  (screen_result_id, aspect_name, status enum, reason, quote). Screened-out houses get no `house` row, so they are never named.
  Passing houses continue into the normal `house` tables. The builder decides how a passed result and the page facts relate to
  avoid fetching the page twice in one run.

## Analyse step and cache

The analyse step builds its list of rooms from the yaml `urls` plus the latest `search_hit`s, deduplicated by room id, and hands them to
the existing pipeline. The pipeline's cache rules are unchanged: stored facts/reviews/prices/analysis are not fetched again unless
`--refresh`; the input hash decides about re-analysis. The pipeline today takes its room ids from `trip.urls`; it should instead take the
resolved list of rooms (smallest change: build the list outside and pass it in). Hits that are already stored are skipped before the plan
is shown, so the plan only lists new work.

The spreadsheet and the static site keep working from the stored houses (`site` reads all stored houses; the xlsx is built from the run's
houses). Open question for the builder, not for the user: the xlsx should contain the houses of this run (yaml urls and hits), including
cached ones, as `run` does today.

## Cost logging

After each house finishes (analysed, or failed), log one line on stderr through the existing `logging` setup, for example:

`[12/80] Casa da Piscina: Apify ~$0.0105, DeepSeek ~$0.0009, run total $0.38 (Apify $0.35, DeepSeek $0.03)`

- Apify cost per house is the share of its actor runs. If the actor run reports the billed amount (the run object's usage fields),
  use it; otherwise use the adapter's estimate. A run covering several houses is split evenly among them, and the line marks it as an estimate
  (`~`) whenever it was split or estimated.
- DeepSeek cost is computed from the token counts of the naming and analysis calls and per-million-token prices kept as settings with
  documented defaults (prices change, so they are not hard-coded in logic).
- The search itself logs its own line (per place: hits found, cost). A final summary prints the totals. Cached houses cost $0 and are
  logged as `cached`, not omitted, so the counter is honest.
- A screened-out house also gets a line (`screened out: <reason>, DeepSeek ~$0.0003`), and its cost counts in the DeepSeek total.
  The final summary reports: found, already stored, screened out, analysed, failed.
- Cost tracking sits in a small collaborator passed into the pipeline (a `CostTracker`), not in globals, so tests can assert on it.

## Error handling

Same philosophy as the rest: one failed house or place never stops the run; failures are listed at the end and the exit code is non-zero.
Typed errors for search (`SearchError`, a subclass of `ApifyError`). Invalid yaml (no `urls` and no `search`, empty `locations`, bad limits)
is a `ConfigError` with a clear message.

## Testing

- Config: `search` optional, `urls` optional, neither present is rejected, limits validated, old yaml still loads.
- CLI: flag matrix (`--search`, `--analyse`, both, none with and without a `search:` block, prompts answered yes/no, `--dry-run`),
  asserting no spending in dry-run and no prompts with both flags.
- Search adapter on a saved, anonymized fixture; fake `ActorRunner`; failure of one place.
- Storage: replacing the last search, dedup across places, migration test.
- Pipeline: cached hits are skipped; a second run fetches nothing; `--refresh` fetches again.
- Screener: only `fails` drops a house (`unclear` passes); houses from `urls` are never screened; no must-haves means no screening;
  stored results are not recomputed and a changed must-have re-screens; `TestModel`/`FunctionModel` asserting the prompt contains the
  listing text and the must-have details and no review text.
- Cost tracker: per-house lines, running total, evenly split shared runs, `cached` lines.
- Live search cross-check marked `live`, skipped by default.

## Left to the builder

The exact `SearchSource` return type; the final search actor (after the live check); module names; the shape of the interactive prompts
(wording in English for code, Portuguese is not needed for CLI prompts today); the DeepSeek price defaults.

## Calls I made (the user has not confirmed these)

1. `locations` (free text) instead of `cities`, so a neighbourhood search works.
2. Last search stored in the database, not a temp file.
3. `trip.example.yaml` stays the template name.
4. `--search --analyse` skips the cost confirmation, as the user asked, but still prints and logs the plan.
5. Even split of shared Apify runs across houses.
6. `max_results` per location, default 40, as the cost brake the user did not ask for explicitly.
7. The screen applies only to searched houses, never to the user's own `urls`, and is skipped when there are no must-haves.
8. Screened-out houses are stored (typed tables) so they are not re-screened, but get no `house` row.
