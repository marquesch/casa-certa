# casa-certa

Compares Airbnb houses for a family trip: gathers each listing's facts, price and recent reviews, asks DeepSeek to
judge them against your aspects, and writes a spreadsheet (comparison + details with a price table per group size).

## Setup

```bash
make install           # uv sync + git hooks
cp .env.example .env   # then fill APIFY_TOKEN and DEEPSEEK_API_KEY
cp trip.example.yaml trip.yaml   # then put your own listings, dates and aspects in it
```

`.env` needs two secrets: `APIFY_TOKEN` (prices and reviews) and `DEEPSEEK_API_KEY` (naming and analysis).
Optional: `DATABASE_URL`; `CHEAP_MODEL` (naming) and `ANALYSIS_MODEL` (analysis), both defaulting to `deepseek-chat`;
`PAGE_DELAY_MIN` / `PAGE_DELAY_MAX` (seconds to wait between listing page requests, default 2 and 5).

## Use

Edit `trip.yaml` (Airbnb URLs, dates, group, aspects, `review_cap`). It is personal and gitignored: only
`trip.example.yaml` lives in the repository. Then:

```bash
make dry-run   # see what would be fetched and what it costs; fetches nothing
make run       # fetch what is missing, analyze, write casa_certa_<check-in>.xlsx
make refresh   # re-fetch everything from Airbnb (costs money)
```

Use `make run TRIP=other.yaml` for another trip file. The same commands directly:

```bash
uv run casa-certa run trip.yaml [--dry-run] [--refresh] [--yes] [--output out.xlsx]
```

- `--dry-run`: print the plan and the estimated Apify cost, then stop.
- `--refresh`: re-fetch facts, price and reviews even if stored.
- `--yes`: do not ask for confirmation before spending money.
- `--output`: spreadsheet path (default `casa_certa_<check-in>.xlsx`).

Stored data is never re-fetched unless you pass `--refresh`. Changing the dates only fetches the new prices; reviews
and listing facts stay cached. The analysis is re-run only when its inputs change.

## Notes, review cap and prices

- `notes:` in `trip.yaml` maps a listing URL or id to free text (for example what the owner told you). The note is
  given to the analysis; changing a note re-analyses only that house.
- `review_cap` is how many recent reviews are fetched per house. Raising it later does not fetch more reviews for
  houses that already have some, unless you use `--refresh`.
- Every group size from `min_people` to `max_people` is quoted separately (paying guests; `infants` is a fixed number
  passed unchanged and never counts as a guest), so extra-guest fees are included. The comparison sheet has the
  columns house, rating, reviews, one per aspect, dealbreakers, price per person at the smallest and at the largest
  size, confidence and summary; the details sheet has the full table. Prices are numeric
  cells (sortable); a note under the comparison table states the dates.
- `n/d` (`n/a` in English) means not available: no price was fetched for that group size, or the house is not available
  for it (for example it cannot hold that many people).

## Sources and cost

Apify actors sit behind swappable price and review sources, chosen in `.env` as comma-separated lists tried in order
(the first source handles every house; only houses it fails on go to the next):

| Setting | Default | Other valid names |
|---|---|---|
| `PRICE_SOURCES` | `thelastscraper` | `tri_angle` |
| `REVIEW_SOURCES` | `kaix,steadyscrape,tri_angle` | |

- Replace the price actor: `PRICE_SOURCES=tri_angle`. Keep the default and add a fallback:
  `PRICE_SOURCES=thelastscraper,tri_angle`. An unknown name fails at startup and lists the valid ones.
- A review source that returns no reviews for a house that has some counts as failed and the next one is tried. When
  a fallback answers, the run prints a warning saying why.
- Cost: about $0.008 per house at 30 reviews (reviews about $0.002, five price quotes at $0.0015). The dry-run for the
  16-house trip printed `~$0.131`. The figure is a lower bound when fallbacks fire. Each actor run also has a
  charge ceiling of about three times its estimate, so a misbehaving actor cannot spend much more.
- `make test-live-sources` runs each cheap actor next to its reference on at most 3 houses of `trip.yaml` (15 reviews;
  price for 11 guests), asserts they agree and prints a comparison table. It costs a few cents; run it if you suspect an
  actor changed.

## Website

`make site` builds an encrypted, mobile-first page from the stored data (no network, no cost) into `site/`, and
`make publish` force-pushes it as a single commit to the `gh-pages` branch of `origin`. The page is encrypted with a
shared passphrase because GitHub Pages is public: anyone with the link sees only ciphertext until they type it.

```bash
make passphrase          # print a random 5-word passphrase; put it in .env as SITE_PASSPHRASE
make run                 # fetch and analyze as usual
make site && make publish
```

One-time: in the repository settings, Pages, deploy from the `gh-pages` branch (root). Then share the URL and the
passphrase with the group. The page has two sections (houses with every must-have, and the others) and filters for
group size, price per person, city, distance to the beach and required aspects, plus sorting. There is no revocation:
to cut someone off, change `SITE_PASSPHRASE` and publish again. Never commit `site/`. Houses without an analysis
("sem análise") are listed last in the second section.

Security notes:

- The output of `make passphrase` is as sensitive as a password: anyone who has it and the URL can read the site.
- The page is decrypted on the device, in the browser; the server only ever holds ciphertext.
- "Lembrar neste aparelho" stores the passphrase in that browser's `localStorage`, on the `<user>.github.io` origin,
  which is shared with every other Pages site of the same GitHub user: those sites can read it. Leave it off on shared
  or untrusted devices.

## Development

```bash
make help        # list every target
make check       # format check, lint, mypy, tests
make test-live   # one real run on one house (spends a few cents)
make test-live-sources   # cross-check the cheap actors (spends a few cents)
```
