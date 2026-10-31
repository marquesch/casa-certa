# Cheaper Apify Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Quote the price per group size (9 to 13 guests) with a cheap actor and fetch reviews from a chain of cheap actors with fallbacks, cutting the cost per house from about $0.154 to about $0.008 while making the price table exact.

**Architecture:** Pluggable `PriceSource` / `ReviewSource` adapters (one per Apify actor, pure `build_input` and `parse`), wrapped in ordered fallback chains selected by settings. The pipeline quotes one price per (house, dates, group size) and caches each. Everything stays testable with `FakeRunner`; no test spends money.

**Tech Stack:** unchanged (Python 3.11+, uv, ruff 120 cols, mypy strict, pytest, SQLAlchemy async + Alembic, Pydantic, Pydantic AI, apify-client).

Spec: `docs/superpowers/specs/2026-10-31-cheaper-sources-design.md` (read it first). Conventions: `CLAUDE.md`. Existing plan for context: `docs/superpowers/plans/2026-10-31-casa-certa.md`.

**Commit trailer:** every commit ends with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` (second `-m`). Commits are Conventional Commits; hooks run ruff, mypy and a message check, so never use `--no-verify`.

**Checks before every commit** (must end clean): `make format lint check`.

**Fixtures already in `tests/fixtures/`** (real, trimmed actor outputs from the experiments):
- `kaix_46397403.json`: list with one item `{id: "46397403", url, name, personCapacity, overallRating, overallReviewCount, reviews: [15 x {id, comments, commentV2, language, createdAt, rating, response, ...}]}`.
- `steadyscrape_46397403.json`: 15 review items `{listingId: "46397403", reviewId, createdAt, rating, language, text, hostResponse, ...}`.
- `thelastscraper_price_11_guests.json`: two items `{listing_id, url, price: {...}, capacity: {guests}}` for houses 46397403 (total 3675, `total_display_original` null) and 52584351 (total 3691, `total_display_original` 3985), both `is_available: true`, `dates_match_query: true`.
- Existing: `apify_rooms_46397403.json`, `apify_rooms_52584351.json` (tri_angle price items), `apify_reviews_46397403.json` (tri_angle review items), `room_*.html`.

**Real measurements the tests encode** (`thelastscraper`, dates 2026-12-05 to 2026-12-09): Itapoá (52584351, capacity 16) totals by guests 9/10/11/12/13 = 2947/3319/3691/4063/4435 (originals 3241/3613/3985/4357/4729); Albatroz (46397403, capacity 12) is 3675 for 9 to 12 guests and unavailable for 13.

## File structure

```
casa_certa/
  config.py                      TripConfig loses adults/children; Settings gains price_sources, review_sources
  pricing.py                     SizeQuote, price_table over per-size quotes
  i18n.py                        labels for the new columns
  db/models.py                   PriceQuote: guests, nullable total, source; House.quote_for/quotes_by_guests
  db/repository.py               save_quote(house, trip, guests, price)
  export/xlsx.py                 per-person columns at min/max size; full table per size
  pipeline.py                    per-size quotes; takes PriceSource/ReviewSource; SourceBatch notes -> warnings
  cli.py                         builds the chains from Settings; plan output
  sources/
    apify.py                     ActorRunner, ApifyActorRunner, parsers, PriceData/ReviewData (AirbnbApify is removed in Task 2)
    protocols.py                 PriceSource, ReviewSource, SourceBatch            (Task 2)
    price_sources.py             ThelastscraperPrice, TriAnglePrice                (Task 2)
    review_sources.py            KaixReviews, SteadyscrapeReviews, TriAngleReviews (Task 2)
    fallback.py                  FallbackPriceSource, FallbackReviewSource         (Task 2)
    registry.py                  build_price_source / build_review_source          (Task 2)
alembic/versions/<new>.py        price_quote migration
tests/                           updated + test_price_sources.py, test_review_sources.py, test_fallback.py, test_registry.py, test_live_sources.py
Makefile, README.md, trip.yaml
```

---

### Task 1: Exact price per group size (still on the current tri_angle actor)

Goal: the data model, pricing, spreadsheet and pipeline handle one quote per group size. The price actor stays `tri_angle` for now; only its input changes. At the end the whole suite passes with the new behavior.

**Files:** modify `casa_certa/config.py`, `casa_certa/sources/apify.py`, `casa_certa/db/models.py`, `casa_certa/db/repository.py`, `casa_certa/pricing.py`, `casa_certa/i18n.py`, `casa_certa/export/xlsx.py`, `casa_certa/pipeline.py`, `casa_certa/cli.py`, `trip.yaml`, and their tests (`tests/fakes.py`, `tests/conftest.py`, `tests/test_config.py`, `tests/test_apify.py`, `tests/test_repository.py`, `tests/test_pricing.py`, `tests/test_xlsx.py`, `tests/test_pipeline.py`, `tests/test_cli.py`, `tests/test_migrations.py`); create a new Alembic revision.

Work test-first. Implement exactly this behavior:

- [ ] **Step 1: Config.** `TripConfig`: remove `adults` and `children`; keep `infants: int = Field(default=0, ge=0)`. Docstring/comment: `min_people` to `max_people` count paying guests (adults + children), never infants; infants are a fixed number passed unchanged to every quote. `tests/fakes.py::make_trip` drops `adults`/`children` and sets `infants=1`. `trip.yaml`: remove `adults` and `children`, set `infants: 1`, set `review_cap: 30` (a local uncommitted edit of 10 exists; overwrite it). Update `tests/test_config.py` (the YAML loader test must no longer pass adults/children).

- [ ] **Step 2: `PriceData`** (`sources/apify.py`): `total_cents: int | None` (None = not available for those dates and guest count), `original_total_cents: int | None = None`, `source: str = ""`. `parse_price_item`: an item with `isAvailable is False` now returns `PriceData(total_cents=None, source="tri_angle")` instead of raising; a missing price on an otherwise available item still raises `PriceParseError`; successful parses set `source="tri_angle"`. Update the matching tests (the old "rejects unavailable" test becomes "returns an unavailable quote").

- [ ] **Step 3: `AirbnbApify.fetch_prices(room_ids, trip, guests)`**: one guest count per call; `run_input` uses `adults=guests`, `children=0`, `infants=trip.infants`. Results mapping unchanged (per house value or `ApifyError`). Update its tests (input assertions, new signature).

- [ ] **Step 4: Database.** `PriceQuote`: replace `adults` and `children` by `guests: int`, make `total_cents` nullable, add `source: str | None` (`String(40)`); unique key becomes `(house_id, check_in, check_out, guests, infants)`. `House.quote_for(trip, guests) -> PriceQuote | None` (matches check_in, check_out, guests, infants) and `House.quotes_by_guests(trip) -> dict[int, PriceQuote]`. `HouseRepository.save_quote(house, trip, guests, price)` upserts (stores unavailable quotes with `total_cents=None`; sets `source`). New Alembic revision (use `make migration m="price quotes per group size"` then fix by hand): add `guests`, backfill `UPDATE price_quote SET guests = adults + children`, drop `adults`/`children`, make `total_cents` nullable, add `source`, replace the unique constraint; use batch mode (SQLite) and make `downgrade` mirror it as best as possible. `tests/test_migrations.py` still compares ORM metadata with the migrated schema; add a test that upgrading a database that already holds an old-shape quote row (insert one at the previous revision with raw SQL, then upgrade) keeps it with `guests = adults + children`. Update `test_repository.py`.

- [ ] **Step 5: Pricing.** In `pricing.py` add `@dataclass(frozen=True) class SizeQuote: total_cents: int | None; original_total_cents: int | None = None` and rewrite `price_table(quotes: Mapping[int, SizeQuote], trip) -> list[PriceRow]`: one row per `trip.group_sizes()`; `PriceRow` gains `original_total_cents`; a size with no quote or an unavailable quote has `total_cents=None` and `per_person_cents=None`; otherwise `per_person_cents = ceil(total / people)`. No capacity argument anymore. Keep `format_brl` and `cents_to_reais`. Tests use the real Itapoá and Albatroz numbers from the header above (build `SizeQuote`s in the test): Itapoá per person at 9 = `ceil(294700/9)` cents, at 13 = `ceil(443500/13)`; Albatroz size 13 -> None; a size missing from the mapping -> None.

- [ ] **Step 6: Spreadsheet.** `i18n.Labels`: add `per_person_at: str` (pt-BR `"Por pessoa ({people})"`, en `"Per person ({people})"`), remove now-unused labels (`total_price` if unused, `guests` only if unused). Comparison sheet: replace the single total column with two columns, the per-person price at `trip.min_people` and at `trip.max_people` (numbers with the `"R$" #,##0.00` format, "n/d" text when unavailable), headers `Por pessoa (9)` and `Por pessoa (13)`; put the date range in a second header line or a cell comment so the basis is visible (keep it simple: a note row under the table, e.g. `05/12–09/12, hóspedes pagantes; bebês não contam`, localized through a label). Details sheet: the price section shows the full table from `price_table(house.quotes_by_guests(trip) -> SizeQuotes)` with columns `Pessoas | Total | Por pessoa` and, when any size has an original price, an extra `Preço original` column. Update `tests/test_xlsx.py` (numbers, formats, "n/d" cases, header texts, the freeze panes and failure rows still work).

- [ ] **Step 7: Pipeline.** `RunPlan.fetch_prices` becomes `dict[str, list[int]]` (room id -> group sizes still missing). In `plan()`: unknown house or `refresh` -> all `trip.group_sizes()`; known house -> sizes with no stored quote (`house.quotes_by_guests(trip)`), skipping sizes above `house.facts.person_capacity` when it is known. `RunPlan.is_empty`/`estimated_cost_usd` updated (`estimated_cost_usd` = quotes x `PRICE_COST_USD` + reviews part as today). Phase 3 (price fetch): after the facts are stored, drop sizes above the stored capacity, then for each size call `apify.fetch_prices(rooms_needing_that_size, trip, size)`, at most 3 sizes concurrently (`asyncio.Semaphore(3)` + `asyncio.gather`). A batch `ApifyError` becomes an error for each house/size in it. Phase 4: `repo.save_quote(house, trip, size, price)` for each successful result (including unavailable ones); each error adds a warning like `could not fetch the price for 11 guests: ...`. Update `format_plan` in `cli.py`: `prices to fetch: N quotes for M houses`. Tests: only missing sizes are fetched; capacity filter (a house with capacity 12 never gets a 13-guest request after its facts are stored; assert the fake runner's recorded `adults` values); an unavailable answer is stored and not asked again on the next run; new dates refetch all sizes; infants passed through unchanged; first run issues exactly one actor run per size; all existing pipeline tests adapted (the fake price actor must answer by the requested `adults`: build items from `apify_rooms_*.json` and override the `price` fields so Itapoá returns its real per-size totals and Albatroz returns unavailable for 13).

- [ ] **Step 8: Run `make format lint check`, then commit** `feat: quote the price for every group size` (single commit).

---

### Task 2: Source layer: adapters, fallback chains, registry

Goal: replace `AirbnbApify` with `PriceSource` / `ReviewSource` adapters per actor, wrapped by fallbacks. The pipeline takes the two sources; behavior is otherwise unchanged. Settings wiring is Task 3.

**Files:** create `casa_certa/sources/protocols.py`, `price_sources.py`, `review_sources.py`, `fallback.py`, `registry.py`; modify `casa_certa/sources/apify.py` (remove `AirbnbApify`, extend `ActorRunner`), `casa_certa/pipeline.py`, `tests/fakes.py`, `tests/test_pipeline.py`, `tests/test_apify.py`; create `tests/test_price_sources.py`, `tests/test_review_sources.py`, `tests/test_fallback.py`, `tests/test_registry.py`.

- [ ] **Step 1: Protocols and batch type** (`sources/protocols.py`), exactly:

```python
from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar

from casa_certa.config import TripConfig
from casa_certa.sources.apify import ApifyError, PriceData, ReviewData

T = TypeVar("T")


@dataclass
class SourceBatch(Generic[T]):
    """Per-house answers from a source (a value or an ApifyError) plus human-readable notes (e.g. fallback used)."""

    results: dict[str, T | ApifyError]
    notes: dict[str, str] = field(default_factory=dict)


class PriceSource(Protocol):
    name: str

    async def fetch_prices(self, room_ids: list[str], trip: TripConfig, guests: int) -> SourceBatch[PriceData]: ...

    def estimate_usd(self, quotes: int) -> float: ...


class ReviewSource(Protocol):
    name: str

    async def fetch_reviews(
        self, room_ids: list[str], trip: TripConfig, *, expect_reviews: frozenset[str] = frozenset()
    ) -> SourceBatch[list[ReviewData]]: ...

    def estimate_usd(self, houses: int, review_cap: int) -> float: ...
```

- [ ] **Step 2: `ActorRunner` gains a cost ceiling.** `run_actor(self, actor_id, run_input, max_items=None, max_total_charge_usd=None)`; `ApifyActorRunner` passes `max_total_charge_usd=Decimal(str(value))` to `call` when given (check apify-client's parameter type; `Decimal | None`). `FakeRunner` records `max_total_charge_usd` in a new `max_charge_seen` list (same pattern as `max_items_seen`). Test both.

- [ ] **Step 3: Price adapters** (`sources/price_sources.py`), test against fixtures first.

`ThelastscraperPrice(runner)`: `name = "thelastscraper"`, `ACTOR = "thelastscraper/airbnb-listing-details"`, `QUOTE_COST_USD = 0.0015`.
  - `build_input(room_ids, trip, guests)` returns `{"where": [listing_url(r) for r in room_ids], "checkIn": iso, "checkOut": iso, "adults": guests, "children": 0, "infants": trip.infants, "currency": "BRL", "language": <first two letters of trip.language>, "outputFormat": "full_json"}`.
  - `parse_item(item) -> PriceData`: `price = item["price"]`; `dates_match_query is False` -> `PriceParseError`; `is_available is False` -> `PriceData(total_cents=None, source=NAME)`; missing `total` -> `PriceParseError`; else `total_cents = int(Decimal(str(total)) * 100)` rounded half up, `original_total_cents` from `total_display_original` when not null, `source=NAME`.
  - `fetch_prices`: one `run_actor(ACTOR, run_input, max_items=len(room_ids), max_total_charge_usd=3 * self.estimate_usd(len(room_ids)))`; results keyed by `str(item["listing_id"])`; every requested id without an item maps to `ApifyError("No price result for listing ...")`; a per-item parse error maps to that error; a whole-run `ApifyError` becomes the same error for every requested id (return, do not raise). `estimate_usd(quotes) = quotes * 0.0015`.
`TriAnglePrice(runner)`: `name = "tri_angle"`; the logic that was in `AirbnbApify.fetch_prices` (actor `tri_angle/airbnb-rooms-urls-scraper`, `adults=guests`, `children=0`, `infants=trip.infants`), same return conventions as above; `estimate_usd(quotes) = quotes * 0.004`.
Tests: `parse_item` on the fixture (3675 / 3691 with original 3985 / source set); unavailable item (copy the fixture dict in the test and set `is_available` False); `dates_match_query` False; a missing result; whole-run failure; recorded `run_input` and `max_items`/`max_total_charge_usd`; `estimate_usd`.

- [ ] **Step 4: Review adapters** (`sources/review_sources.py`). A shared helper builds a `ReviewData` from plain fields: `review_data(id, text, created_at, rating, language, response)` (use `html_to_text`, naive UTC conversion exactly as `parse_review_item` does, raise `ReviewParseError` on bad input); `parse_review_item` for tri_angle items keeps working through it.
  - `KaixReviews(runner)`: `name = "kaix"`, `ACTOR = "kaix/airbnb-listing-scraper"`. `build_input` = `{"listings": [urls], "includeReviews": True, "maxReviewsPerListing": trip.review_cap, "reviewsSortBy": "MOST_RECENT"}`. One item per listing: `str(item["id"])` is the room id, `item["reviews"]` the list; each review maps `id`, `comments`, `createdAt`, `rating`, `language`, `response` (use the string when `response` is a str, else None). Bad reviews are skipped; the others kept. `max_items = len(room_ids)`. `estimate_usd(houses, cap) = 0.00005 + houses * (0.0001 + cap * 0.00002)`.
  - `SteadyscrapeReviews(runner)`: `name = "steadyscrape"`, `ACTOR = "steadyscrape/airbnb-reviews-scraper"`. Input `{"listings": [urls], "maxReviewsPerListing": cap, "sortBy": "MOST_RECENT"}`. One item per review: `str(item["listingId"])`, `reviewId`, `text`, `createdAt`, `rating`, `language`, `hostResponse`. `max_items = len(room_ids) * cap`. `estimate_usd = houses * cap * 0.0005`.
  - `TriAngleReviews(runner)`: `name = "tri_angle"`; the logic of `AirbnbApify.fetch_reviews` (actor `tri_angle/airbnb-reviews-scraper`, `startUrl` regex mapping, `maxReviewsPerListing`, `sortBy: most-recent`); `estimate_usd = 0.0001 + houses * cap * 0.005`.
  - All three: houses without reviews map to `[]`; `review_cap == 0` or no rooms makes no call and returns all `[]`; a whole-run `ApifyError` becomes that error for every requested house; every call passes `max_total_charge_usd = 3 * estimate`. Tests per adapter against its fixture (for kaix and steadyscrape: 15 reviews, ids equal the tri_angle fixture ids `apify_reviews_46397403.json` for the first four, text equal after whitespace normalization), plus cap 0, no-reviews house, bad review skipped, whole-run failure, recorded input and ceilings, `estimate_usd`.

- [ ] **Step 5: Fallback chains** (`sources/fallback.py`). `FallbackPriceSource(sources: Sequence[PriceSource])` and `FallbackReviewSource(sources: Sequence[ReviewSource])`, `name = "+".join(...)`, `estimate_usd` delegating to the first source. Algorithm (per call): `pending = list(room_ids)`; for each source in order, call it for `pending` only; for each room decide success/failure: failure if the result is an `ApifyError` or missing, or (reviews only) the result is `[]` while the room is in `expect_reviews` (error text: `returned no reviews although the listing has some`); an unavailable `PriceData` (`total_cents=None`) is a valid answer. Successes are stored; if the answering source is not the first, add `notes[room] = f"{kind} from {source.name} because {first_failure_reason}"`; failures stay pending with the reason remembered. After the last source, each still-pending room gets `ApifyError(f"All {kind} sources failed: <name>: <reason>; ...")`. A source that raises `ApifyError` for the whole call counts as failed for all pending rooms. Tests: first source answers everything (second never called); first fails entirely (all from second, notes set); partial failure (only failed rooms go to the next source, assert the recorded `room_ids` of the second call); the `expect_reviews` rule both ways (empty list for an expected room falls back; for a non-expected room it is accepted); unavailable price accepted without fallback; all sources fail (error mentions every source); `estimate_usd` delegates. Use small in-memory fake sources.

- [ ] **Step 6: Registry** (`sources/registry.py`): `PRICE_SOURCE_NAMES = ("thelastscraper", "tri_angle")`, `REVIEW_SOURCE_NAMES = ("kaix", "steadyscrape", "tri_angle")`, `class SourceConfigError(ValueError)`, `build_price_source(names: Sequence[str], runner: ActorRunner) -> PriceSource` and `build_review_source(...)`: validate every name (unknown -> `SourceConfigError` listing the valid names; empty list -> error); one name returns the adapter itself, several return the fallback chain. Tests for all branches.

- [ ] **Step 7: Pipeline switch.** `Pipeline.__init__` takes `price_source: PriceSource` and `review_source: ReviewSource` instead of `apify`; phase 3 uses `price_source.fetch_prices(rooms, trip, size)` (a `SourceBatch`) and `review_source.fetch_reviews(rooms, trip)`; batch `notes` become entries in `warnings[room_id]`. Delete `AirbnbApify` and its now-dead helpers/constants; keep `PRICE_COST_USD`/`REVIEW_COST_USD` only if still used by `RunPlan`. Update `tests/test_pipeline.py::make_world` to build `TriAnglePrice` and `TriAngleReviews` around the `FakeRunner` (behavior unchanged), `tests/test_apify.py` (remove `AirbnbApify` tests that moved to the adapter tests; keep parsers/runner tests), and any other import.

- [ ] **Step 8: `make format lint check`, commit** `feat: add pluggable price and review sources with fallbacks`.

---

### Task 3: Settings, CLI wiring, cost estimate and review guard

**Files:** modify `casa_certa/config.py`, `casa_certa/cli.py`, `casa_certa/pipeline.py`, `tests/test_config.py`, `tests/test_cli.py`, `tests/test_pipeline.py`, `.env.example`.

- [ ] **Step 1: Settings.** `Settings.price_sources: list[str] = ["thelastscraper"]` and `review_sources: list[str] = ["kaix", "steadyscrape", "tri_angle"]`, read from `PRICE_SOURCES` / `REVIEW_SOURCES` as comma-separated strings (verify how the installed pydantic-settings decodes lists; use `Annotated[list[str], NoDecode]` with a `field_validator(mode="before")` that splits on commas and strips blanks). Tests: defaults, comma parsing with spaces, empty string -> error from the registry, env override via `monkeypatch.setenv`. `.env.example`: add both variables commented with the defaults.

- [ ] **Step 2: CLI wiring.** `cli._run` builds `ApifyActorRunner`, then `build_price_source(settings.price_sources, runner)` and `build_review_source(settings.review_sources, runner)` and passes them to `Pipeline`; a `SourceConfigError` prints `error: ...` and returns exit code 2 (it already is a `ValueError`, so `main` handles it; add a test through `main`).

- [ ] **Step 3: Cost estimate from the sources.** `RunPlan.estimated_cost_usd` becomes a stored field computed in `Pipeline.plan()` as `price_source.estimate_usd(total_quotes) + review_source.estimate_usd(len(fetch_reviews), trip.review_cap)`; remove the module constants `PRICE_COST_USD`/`REVIEW_COST_USD` if unused. `format_plan` prints `Estimated Apify cost: ~$X (more if fallback sources are needed)`. Update tests (the pipeline plan test must expect the first sources' rates: price `thelastscraper` 5 sizes per house x $0.0015 and reviews `kaix`, when built through the registry; the `make_world` helper may take source names to build chains via the registry with the FakeRunner).

- [ ] **Step 4: Review guard.** In phase 3 the pipeline passes `expect_reviews=frozenset(room_id for rooms in play whose stored facts have review_count > 0)` (read the facts through a short session; unknown count -> not expected). Tests: a first source returning `[]` for a house whose facts show reviews triggers the fallback and a note; a house with `review_count == 0` is accepted with `[]` and the fallback is not called.

- [ ] **Step 5: Pipeline end-to-end with a failing first source.** Test in `test_pipeline.py`: price chain `[failing, tri_angle]` and reviews chain `[failing, steadyscrape]` built over a `FakeRunner` that raises `ApifyError` for the first actors: the run succeeds, data is stored, and `report.warnings` contains the fallback notes for each house; the next run fetches nothing.

- [ ] **Step 6: `make format lint check`, commit** `feat: choose price and review sources from settings`.

---

### Task 4: Live cross-check, docs and cleanup

**Files:** create `tests/test_live_sources.py`; modify `Makefile`, `README.md`, `CLAUDE.md`.

- [ ] **Step 1: Live cross-check test** (`@pytest.mark.live`, skipped by default; costs a few cents). Using `Settings()` and `ApifyActorRunner`, load `trip.yaml` through `YamlConfigSource`, take at most the first 3 houses and `review_cap = 15`. (a) Reviews: run `KaixReviews` and `SteadyscrapeReviews` on those houses; per house, assert that the review ids of the two sources are equal as sets and that normalized text matches for every shared id; print a small comparison table (house, count per source). (b) Price: for group size 11 run `ThelastscraperPrice` and `TriAnglePrice`; assert equal `total_cents` and equal availability per house; print the table. Do NOT run this test inside the plan execution; it is for the user (document it).

- [ ] **Step 2: Makefile.** Add `test-live-sources: ## Cross-check the cheap sources against the reference actors (spends a few cents)` running `uv run pytest -m live tests/test_live_sources.py -s`; add it to `.PHONY`.

- [ ] **Step 3: Docs.** `README.md`: a "Sources and cost" section (defaults, `PRICE_SOURCES` / `REVIEW_SOURCES`, how to switch to `tri_angle` for prices, expected cost: about $0.008 per house, $0.16 for 20, five price quotes per house, what `make test-live-sources` does), group sizes mean paying guests with `infants` fixed in `trip.yaml`, the comparison-sheet columns. `CLAUDE.md`: add `make test-live-sources` to Commands and a bullet that Apify actors are behind `PriceSource`/`ReviewSource` adapters chosen by settings. Remove dead code left by earlier tasks (`rg` for unused names such as `AirbnbApify`, `PRICE_COST_USD`, `unavailable_amenities` only if truly unused; do not remove public helpers still covered by tests).

- [ ] **Step 4: Final verification.** `make check` clean; `make dry-run` with the real `.env` prints the plan with the new per-quote line and a cost near `$0.02` for the two trip houses (5 price quotes each + reviews), without spending; `git status` clean except intended files. Commit `docs: document sources, costs and the live cross-check`.

---

## Out of scope (recorded in the spec)

A direct (non-Apify) route; the search actor; changing the analysis; per-house review caps.
