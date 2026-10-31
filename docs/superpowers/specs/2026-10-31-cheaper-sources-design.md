# Cheaper Apify sources: design

Extends `2026-10-31-casa-certa-design.md`. Replaces the single `AirbnbApify` gateway (two hard-wired `tri_angle`
actors) with pluggable price and review sources, defaulting to much cheaper actors that return the same data.

## Why

At the current defaults a house costs about $0.154 (30 reviews at $0.005 each, plus $0.004 for the price). Reviews are
97% of that. A comparison of Apify Store actors on the same house showed identical review data at a fraction of the cost.

Measured on house 46397403 (15 most recent reviews) and houses 46397403 + 52584351 (price), dates 5-9 Dec 2026,
11 guests, BRL. Costs are what Apify billed, not list prices.

| Step | Actor | Billed | Result vs `tri_angle` baseline |
|---|---|---|---|
| Reviews | `tri_angle/airbnb-reviews-scraper` (current) | $0.0751 for 15 | baseline |
| Reviews | `kaix/airbnb-listing-scraper` | $0.00045 for 15 (+ listing facts) | 15/15 same ids, text, ratings |
| Reviews | `steadyscrape/airbnb-reviews-scraper` | $0.0075 for 15 | 15/15 identical |
| Price | `tri_angle/airbnb-rooms-urls-scraper` (current) | $0.004 per listing | R$ 3.675 and R$ 3.691 (both confirmed correct on the Airbnb page) |
| Price | `thelastscraper/airbnb-listing-details` | $0.0015 per listing | identical: 3.675; 3.691 (original 3.985) |
| Either | `automation_craft/airbnb-scraper` | $0.0165 for one house | rejected on cost ($0.004 per listing plus a $0.005 run fee, even with details off). Its Itapoá price (R$ 3.319) equals the 10-guest price, so it appears to ignore children |
| Price | `curious_coder/airbnb-scraper` | n/a | rejected: no BRL support |

**The price depends on the number of guests** (measured with `thelastscraper`, 5-9 Dec, 4 nights):

| Guests | Casa do Albatroz (capacity 12) | Itapoá (capacity 16) |
|---|---|---|
| 9 | R$ 3.675 | R$ 2.947 (original 3.241) |
| 10 | R$ 3.675 | R$ 3.319 (original 3.613) |
| 11 | R$ 3.675 | R$ 3.691 (original 3.985) |
| 12 | R$ 3.675 | R$ 4.063 (original 4.357) |
| 13 | unavailable (over capacity) | R$ 4.435 (original 4.729) |

Itapoá adds R$ 372 per extra guest. Dividing one quote by the group size (the first version's approach) made Itapoá look
cheaper per person as the group grew (R$ 284 at 13 against a real R$ 341). The price step therefore quotes **each group
size** separately. Two actor runs never share a guest configuration, but `thelastscraper` bills per listing only, so five
sizes cost 5 x $0.0015 = $0.0075 per house.

Other findings:
- Reviews and price both come from extra GraphQL calls that a plain page request does not contain. A direct call to
  Airbnb's private API was explicitly blocked by the permission layer and is **out of scope** (see below).
- The Apify SDK (`apify-client`) and plain HTTP are billed identically: Apify bills by resource (actor events, compute,
  dataset reads and writes), not by client. Keep `apify-client`.
- The account is on Apify's Free plan with $5/month of usage credit.

## Goals and non-goals

Goals: cut the cost per house by about 20x while making the price table exact for every group size; keep every actor swappable by
configuration; make a bad or changed actor visible instead of silent.

Non-goals: a direct (non-Apify) reviews/price route; the search actor for WhatsApp requests; changing the analysis.

## Interfaces

```python
class PriceSource(Protocol):
    name: str
    async def fetch_prices(
        self, room_ids: list[str], trip: TripConfig, guests: int
    ) -> dict[str, PriceData | ApifyError]: ...
    def estimate_usd(self, quotes: int) -> float: ...

class ReviewSource(Protocol):
    name: str
    async def fetch_reviews(
        self, room_ids: list[str], trip: TripConfig, *, expect_reviews: frozenset[str] = frozenset()
    ) -> dict[str, list[ReviewData] | ApifyError]: ...
    def estimate_usd(self, houses: int, review_cap: int) -> float: ...
```

- Results are per house: a value or an `ApifyError`. A whole-batch failure becomes the same error for every house in it.
- `fetch_prices` quotes ONE guest count (`guests` adults, 0 children, `trip.infants` infants; infants do not change the
  price). `PriceData` gains `source: str` and `total_cents` becomes `int | None`: `None` means the listing is not
  available for those dates and that guest count (a normal answer, not an error, so it is cached).
- The pipeline calls the source once per missing group size (up to 3 runs at a time, below the free plan's limit of 5
  concurrent actor jobs). Sizes above a house's capacity are never requested.
- One adapter per actor, in `casa_certa/sources/`: `thelastscraper` (price), `kaix`, `steadyscrape`, `tri_angle`
  (price and reviews; the existing logic moves here). Each adapter has an actor id, a pure `build_input(room_ids, trip)`,
  a pure `parse(items)` and its own cost rates. A shared `ActorSource` base runs the actor through the existing
  `ActorRunner` and maps items to houses.
- `ActorRunner.run_actor` also takes `max_total_charge_usd`, set to about 3x the adapter's own estimate, so a
  misbehaving actor cannot spend more than a small multiple of the expected cost. `max_items` and the 10-minute run
  timeout stay.

## Chains and fallback

- `FallbackPriceSource` and `FallbackReviewSource` wrap an ordered list. The first source handles every house; houses
  that came back as errors go to the next source, and so on. Houses that succeed are never re-requested.
- An empty review list is an error only for rooms in `expect_reviews` (houses whose page shows at least one review).
  This protects against a source silently returning nothing; a house that truly has no reviews never triggers a paid
  retry.
- Order is configuration in `.env` (comma separated, validated against a registry at startup):
  ```
  PRICE_SOURCES=thelastscraper
  REVIEW_SOURCES=kaix,steadyscrape,tri_angle
  ```
  Price defaults to `thelastscraper` only. `tri_angle` stays registered for prices and is one setting away
  (`PRICE_SOURCES=thelastscraper,tri_angle` to add it as a fallback, or `tri_angle` to replace it) if
  `thelastscraper` proves unreliable. An unknown name fails at startup listing the valid names.
- When a source other than the first answered, the pipeline records a warning
  (`price from X because Y failed: <error>`), keyed by the room id.

## Data model

`price_quote` changes in one Alembic migration: `adults` and `children` are replaced by `guests` (paying guests, i.e. the
group size), `total_cents` becomes nullable (null = unavailable), and `source` (string, nullable) is added. The unique key
becomes (house, check_in, check_out, guests, infants). One row per house, dates and group size.
Reviews are not tagged (they merge by Airbnb review id regardless of source).

## Pipeline and config

- `TripConfig`: `adults` and `children` are removed; `infants` (default 0) stays and is passed unchanged to every quote.
  The group sizes `min_people` to `max_people` (default 9 to 13) count paying guests, never infants. `trip.yaml` loses
  `adults` and `children`.
- `Pipeline` takes a `PriceSource` and a `ReviewSource` instead of `AirbnbApify`. The cache-first logic is unchanged but
  works per (house, group size): a size is fetched only if no quote is stored for it (including stored "unavailable"
  quotes); new dates or `--refresh` refetch every size. The plan lists quotes to fetch; for a house not yet stored its
  capacity is unknown, so the plan counts all sizes (an upper bound) and the real run skips sizes above the capacity.
- `pricing.price_table` stops dividing one quote: each row uses the stored quote of its group size (total, original
  price, per person rounded up to the cent); a size with no quote, or an unavailable one, shows "n/d".
- Spreadsheet: the details sheet shows the full table with the real total per size. The comparison sheet replaces the
  single "Total" column with the per-person price at the smallest and the largest group size (for example "Por pessoa (9)"
  and "Por pessoa (13)"), so houses with per-guest fees compare fairly at both ends of the range.
- The pipeline computes `expect_reviews` from the stored page facts (`review_count > 0`) and passes it to the review
  source.
- `RunPlan.estimated_cost_usd` is the sum of the first sources' estimates (quotes x rate for prices), a lower bound when fallbacks fire; the CLI
  plan output says so ("~$0.03, more if fallback sources are needed").
- `trip.yaml` `review_cap` goes back to 30 (about $0.002 per house); cost no longer justifies 10.

## Error handling

- A source that cannot parse one house's data reports an `ApifyError` for that house only.
- One malformed review is skipped and the rest are kept (already true today; applies to all review adapters).
- A failure of every source in a chain leaves the house stored without that data and adds a warning, exactly like a
  single-source failure today.

## Testing

- Per adapter: `build_input` and `parse` against trimmed real outputs saved from the experiments above as fixtures.
- Fallback chains: first source fails entirely; partial failure per house; the `expect_reviews` rule (empty list for a
  house with reviews falls back, for a house without reviews it does not); every source failing; unknown source name;
  settings parsing of the comma-separated lists.
- Pipeline: a failing first source yields a warning and the fallback's data, with no extra calls for houses that
  succeeded; `RunPlan` estimate uses the first sources' rates; stored quotes carry `source`.
- Pricing: a fixture with the real Itapoá quotes above (9 to 13 guests, with originals) and the flat Albatroz quotes
  (13 unavailable) checks per-person values, the "n/d" cases and that the table no longer divides a single quote.
- Pipeline: only missing sizes are fetched; sizes above a stored capacity are skipped; an "unavailable" answer is stored
  and not asked again; new dates refetch all sizes; infants are passed through unchanged.
- Migration test continues to compare ORM metadata and the migrated schema (including the new `price_quote` shape).
- A live cross-check (marked `live`, skipped by default, `make test-live-sources`, a few cents): `kaix` against
  `steadyscrape` on the trip's houses (review ids and text) and `thelastscraper` against `tri_angle` on price. It is the
  validation to run before releasing v1 and whenever an actor seems to have changed.

## Expected effect

Per house at 30 reviews: about $0.154 today (one price quote), about $0.0082 after (five exact price quotes $0.0075 plus
reviews $0.0007). For 20 houses about $3.08 becomes about $0.16, with a table that is exact for every group size.
At 100 reviews per house the saving is larger: $10.08 becomes about $0.17 for 20 houses.

## Risks and follow-ups

- `kaix` (about 8,000 runs) and `thelastscraper` (about 26 runs) are far less used than `tri_angle`. Mitigation: the
  fallback chain for reviews, `tri_angle` one setting away for prices, the live cross-check, and the `source` column.
- Actors can change their output or pricing silently: re-run the live cross-check periodically; `max_total_charge_usd`
  bounds the damage from a price change.
- Extra-guest fees are now captured because every group size is quoted. Service fees and taxes are not shown by the actor (`fees_total` and `taxes_total` come back null); the totals match what Airbnb displays for the verified houses.
- Out of scope, recorded for later: a direct reviews/price source (blocked by the permission layer when attempted; the
  interfaces above leave room for it), and the `tri_angle/airbnb-scraper` search actor for WhatsApp requests.
