# casa-certa: design

Compares a pre-filtered list of Airbnb houses for a family trip and produces a spreadsheet with pros/cons per house, aspect verdicts, and a price table per group size.

## Goals (first version)

Input: a config file with Airbnb URLs, trip dates, group sizes, and the aspects that matter. Output: an `.xlsx` with

- **Comparison sheet:** house nickname, rating, review count, one symbol per aspect (✅ / ⚠️ / ❌ / ❓), one-line verdict.
- **Details sheet:** pros, cons, per-aspect evidence (quoted reviews), and a pricing table (total and per person) for each group size from 9 to 13 people. Houses that cannot hold a group size show "n/a".

Out of scope for now (designed for, not built): Google Sheets sync, WhatsApp bot (group replies, @mentions), search from a WhatsApp request, renaming and `extra` notes from chat, fuzzy house-name resolution, refresh-on-request from chat.

## Requirements

- **Aspects** are `must_have` or `nice_to_have`. Dealbreakers (a missing must-have) are flagged but rare and do not drive the layout.
- **Rating and review count** are shown side by side. There is no composite score. Thin or old evidence is flagged in words ("only 10 reviews, 4 from before 2025").
- **Reviews matter.** The listing is the baseline. Recent review comments can override it (beach distance, beach quality, A/C reality). A review that contradicts the listing must be quoted. Text only, no images.
- **User-added info** (`extra`) is accepted and shown as such, but never overrides what the listing says; it is labelled with its source.
- **House names:** descriptive, Portuguese ("Casa da Piscina"), unique (numeric suffix on clash), and stable across runs together with `extra`.
- **Language** of user-facing output (sheet text, names) is a setting; default `pt-BR`. **Currency** is BRL. **Dates** and group sizes are settings.
- **All code-related text is English** (identifiers, comments, docstrings, errors, tests, commits). Portuguese appears only in user-facing output and in Airbnb data.
- **Cost control:** never re-fetch something already stored unless told to (see Caching).

## Data sources

| Need | Source | Cost |
|---|---|---|
| Static facts (title, capacity, rooms, amenities with availability, description, ratings, host, rules, location) | Plain HTTP request to the listing page, parsing the embedded `data-deferred-state-0` JSON (`airbnb_page.py`) | free |
| Price for the dates | Apify `tri_angle/airbnb-rooms-urls-scraper` (`startUrls`, `checkIn`, `checkOut`, guests, `currency=BRL`) | ~$0.004 per listing |
| Review text | Apify `tri_angle/airbnb-reviews-scraper` (`maxReviewsPerListing`, `sortBy=most-recent`, `locale=pt-BR`) | ~$0.005 per review |
| Later: search by request | Apify `tri_angle/airbnb-scraper` | ~$0.004 per listing |

Findings that shaped this: Airbnb has no public API; the openbnb MCP respects `robots.txt` and returns no reviews or prices; the `axlymxp` Apify MCP's listing tool fails when dates are passed and caps reviews unpredictably. The room-URL scraper's price was verified against the Airbnb page (R$ 3.675 for house 46397403, 5–9 Dec). The plain-request page contains neither review text nor price.

The plain-request path reads pages that Airbnb's `robots.txt` asks automated clients not to fetch. The user accepted this risk for now. Mitigation: a random 2–5 s delay between houses, one retry on 429/5xx with a longer wait, a timeout, and an isolated module so the source can be swapped.

## Stack

Python 3.11+, async everywhere.

- `httpx.AsyncClient` for page requests; `apify-client` (`ApifyClientAsync`) for Apify.
- SQLAlchemy 2.0 async ORM + `aiosqlite`, typed `Mapped[...]` models, Alembic migrations. **No JSON columns.**
- Pydantic (models, settings) and Pydantic AI for the LLM layer, using DeepSeek through its OpenAI-compatible provider. Both the namer and the analyst use `deepseek-chat` for now; `deepseek-reasoner` is deferred by decision. The model per task is a setting (`cheap_model`, `analysis_model`), so tasks can move to the reasoning model later if the cheap one is not enough. Risk to check then: `deepseek-reasoner` support for native structured output varies by release; Pydantic AI's prompted output mode is the fallback.
- `openpyxl` for the spreadsheet, `argparse` for the CLI, `pytest` + `pytest-asyncio` for tests.
- Tooling: `uv` (packages), `ruff` (format + lint, 120 columns), `mypy --strict` on the package (tests may omit annotations), `pre-commit` (conventional-commit message check, `ruff check --fix`, `ruff format`, mypy on commit; pytest on push) and a `Makefile` of shortcuts (`make help`).
- Code style: clean and readable, easy to test. Small functions, typed Pydantic models at boundaries, dependencies passed in. Use Python's built-in interfaces (dunder methods, context managers, iterators, dataclasses, `Protocol`) where they fit instead of working around them.
- Secrets in `.env` (gitignored): `APIFY_TOKEN`, `DEEPSEEK_API_KEY`.

## Architecture

```
casa_certa/
  config.py          TripConfig, Aspect, Settings (.env)
  config_sources/    ConfigSource protocol; YamlConfigSource
  sources/
    airbnb_page.py   plain-request static facts (httpx async)
    apify.py         price + capped reviews via ApifyClientAsync
  db/                async engine/session, ORM models, Alembic
  ai/                Pydantic AI agents: namer and analyst (model per task is a setting)
  pipeline.py        orchestration
  pricing.py         total + per-person table, "n/a" over capacity
  export/xlsx.py     comparison + details sheets
  cli.py             casa-certa run trip.yaml [--refresh] [--dry-run] [--yes]
```

Boundaries: sources return plain Pydantic models; only the pipeline touches the DB; only `export` knows spreadsheets. Replacing Apify, or adding a Google Sheets exporter, touches one module.

### Config decoupling

`TripConfig` (Pydantic) is the only input contract the pipeline knows: dates, URLs, group sizes, aspects, language, review cap. `ConfigSource` has one method, `async load() -> TripConfig`. `YamlConfigSource` reads `trip.yaml`. A future `WhatsAppConfigSource` builds the same `TripConfig` from messages without touching the pipeline.

## Pipeline

1. Load `TripConfig`.
2. Plan: for each URL decide what is missing (see Caching) and print the plan with an estimated Apify cost; `--dry-run` stops here, `--yes` skips the confirmation.
3. For each missing item, fetch page facts (with delay), Apify price, capped reviews; persist.
4. Name new houses with the cheap model (existing houses keep their name and `extra`). Add a numeric suffix in code on clash.
5. Analyze each house (model set by `analysis_model`, `deepseek-chat` today) where the input hash changed; persist.
6. Compute the pricing table.
7. Write the `.xlsx`.

## Caching

- Default run: never re-fetch what is stored. A new URL is fetched because nothing is stored.
- `--refresh` re-fetches everything for all houses (future: one house by name, or "update <house>" from WhatsApp).
- Prices are stored per (house, dates, guests). New dates are a gap, not a refresh: only prices are fetched; facts and reviews are reused. The plan output shows this cost.
- Analysis is keyed by an input hash (facts, reviews, aspects, `extra`, language). It is re-run only when the hash changes.

## Data model

All typed columns; children are separate tables with foreign keys. No versioning: a refresh replaces a house's facts and child rows in one transaction; reviews upsert by Airbnb review id; price quotes upsert by their unique key; only the current analysis is kept. No raw payloads are stored.

| Table | Columns |
|---|---|
| `house` | id, `airbnb_id` (unique), `name` (unique), `extra` (text), created_at, updated_at |
| `house_facts` (1 per house) | title, property_type, person_capacity, bedrooms, beds, bathrooms, rating, review_count, description, location_name, latitude, longitude, is_guest_favorite, six category ratings (cleanliness, accuracy, checkin, communication, location, value), host_name, host_is_superhost, host_rating, host_review_count, host_years, fetched_at |
| `amenity` | house_id, group, name, available, note |
| `room` | house_id, name, beds_text, position |
| `highlight` | house_id, type, headline, body |
| `house_rule` | house_id, kind (`rule` / `safety`), type, title, description |
| `review` | house_id, `airbnb_review_id` (unique), rating, created_at, language, text, host_response |
| `price_quote` | house_id, check_in, check_out, adults, children, infants, `total_cents` (integer BRL centavos), nights, original_total_cents (nullable), fetched_at; unique on (house, dates, guests) |
| `analysis` | house_id, input_hash, summary, confidence_note, model, created_at |
| `aspect_verdict` | analysis_id, aspect_name, status (enum), reason |
| `evidence` | verdict_id, source (enum: listing, review, extra), quote |
| `analysis_point` | analysis_id, kind (`pro` / `con`), text, position |

Money is integer centavos; the parser turns `"R$ 3.675,00"` into `367500`. Enums are Python `Enum`s stored as strings with a check constraint.

## AI contract

```
HouseAnalysis
  aspects: list[AspectVerdict]   # aspect name, status (✅ ⚠️ ❌ ❓), reason,
                                 # evidence: list[Evidence(source: listing|review|extra, quote)]
  pros: list[str]
  cons: list[str]
  summary: str                   # one line for the comparison sheet
  confidence_note: str | None    # thin or old evidence, missing reviews
```

Prompt rules: the listing is the baseline; recent reviews can override it and the contradicting review is quoted; `extra` is accepted but labelled; ❓ means missing evidence; output in the configured language. `HouseName` is a tiny structured output from the cheap model (title, area, key features in; one short name out).

## Error handling

- One failed house never stops the run. It appears in the sheet as failed with a reason; the CLI prints a summary and exits non-zero if anything failed.
- Typed errors from sources (`PageFetchError`, `PageParseError`, `ApifyError`, `PriceParseError`); the pipeline catches these, not bare `Exception`.
- Partial data is allowed: a missing price shows "n/a" and the house is still analyzed; missing reviews means analysis on the listing alone, with a `confidence_note`.
- LLM output is validated by Pydantic AI with a small retry count.

## Testing

- Pure logic and parsers on saved fixtures: page extractor (existing), Apify price/review parsing, pricing math (9–13 people, capacity "n/a"), name suffixing, input hash.
- DB tests on in-memory SQLite: upserts, refresh replacing children, the cache rule.
- AI tests with Pydantic AI `TestModel` / `FunctionModel`, asserting prompt content (listing, reviews, `extra`, override rule).
- Apify tested through a fake client returning saved dataset items.
- End-to-end pipeline with fakes: first run fetches everything, second run fetches nothing, `--refresh` fetches again.
- Live smoke tests marked `@pytest.mark.live`, skipped by default.

## Existing code

`casa_certa/airbnb_page.py` and its tests exist (stdlib `urllib`). It will move to `httpx.AsyncClient` and `asyncio.sleep` for the inter-house delay as part of the implementation.
