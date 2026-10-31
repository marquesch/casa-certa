# Static comparison site: design

Extends `2026-10-31-casa-certa-design.md`. Publishes the comparison as a mobile-first static website on GitHub Pages,
encrypted with a shared passphrase, with client-side sorting and filtering.

## Goals and non-goals

- Anyone in the group can open one URL on a phone, type a shared passphrase and see every house: which ones have all
  the must-haves, which ones do not, and every criterion at a glance.
- Static files only: no backend, no accounts, no build step in the browser, no JS framework.
- The data is fed by running the app locally and publishing the result. Nothing generated is committed to `master`.
- Not in this version: photos, editing from the site, shareable filter URLs, a WhatsApp trigger for publishing, Google
  Sheets sync.

## Why encrypted

GitHub Pages on the Free plan only serves public repositories, and a Pages site is reachable by anyone with the URL
even when the repository is private. The published data (trip dates, group size, prices, review quotes) must not be
world-readable, and git history cannot take it back. The page is therefore encrypted at build time with a shared
passphrase and decrypted in the browser. This is not authentication and there is no backend: it keeps crawlers and
strangers out, nothing more.

Known limits, accepted: the ciphertext is public, so a weak passphrase can be brute-forced offline (hence the strength
rule below); there is no revocation (changing the passphrase and republishing is the only remedy); the site's
existence, size and publish times are visible; the unlock script is served by GitHub.

## Data change: distance to the beach

The analysis currently returns only a verdict for the "close to the beach" aspect. The filter needs a number, so the
analysis also extracts it.

- `HouseAnalysis` (the model output) gains `beach_distance_m: int | None` and `beach_distance_evidence:
  EvidenceOut | None`. The distance is the walking distance in meters to the nearest beach, `0` for beachfront
  ("pé na areia", "frente ao mar"), kilometers converted to meters, `null` when neither the listing nor the reviews give
  or clearly imply one. Reviews override the listing, as for every other fact. A non-null distance needs an evidence
  quote.
- The `analysis` table gains nullable columns `beach_distance_m` (integer), `beach_distance_source` (the existing
  evidence-source enum) and `beach_distance_quote` (text), through an Alembic migration (batch mode, no JSON columns).
- `analysis_instructions` documents the field, so the prompt hash changes and every stored house is re-analyzed once on
  the next `make run`. Listing facts and reviews stay cached: only DeepSeek is called (a few cents for ~16 houses).
  Until then the site shows the distance as unknown for houses analyzed before this change.
- The distance is an LLM reading of text, so the UI labels it "estimado" and the details show its quote.

## Commands

| Command | What it does | Cost |
|---|---|---|
| `casa-certa site trip.yaml [--output-dir site]` | Reads the stored data and writes the encrypted `site/index.html` and `site/.nojekyll` | none, no network |
| `casa-certa publish [--dir site] [--yes]` | Force-pushes the directory as one commit to the `gh-pages` branch of `origin` | none |
| `casa-certa passphrase` | Prints a new random passphrase | none |

`make site`, `make publish` and `make passphrase` wrap them (`TRIP=trip.yaml` as for the other targets). `site/` is
gitignored.

The passphrase comes from `SITE_PASSPHRASE` in `.env` (a `SecretStr` in `Settings`, required only by `site`).

## Modules

New package `casa_certa/web/`, one job per file:

- `model.py`: turns the stored `House` rows plus the `TripConfig` into plain view models (sections, cards, price
  rows). No HTML, no I/O.
- `render.py`: renders the view models to the plaintext page with Jinja2 (`templates/page.html.j2`, CSS inline,
  filter script inline). Labels come from `i18n.py`.
- `crypto.py`: encrypts the page and fills the unlock shell.
- `publish.py`: the git steps of `publish`.
- `static/unlock.html` and `static/unlock.js`: the shell with the passphrase form and the decrypt script.
- `static/filters.js`: the pure filtering and sorting functions (inlined into the page by `render.py`).

New dependencies: `jinja2`, `cryptography`. The new `Labels` fields (site texts) are added for every language already
in `i18n.py`.

`cli.py` gets the three subcommands. `site` reads the houses with the existing `HouseRepository.all()` and filters to the
trip's listings in `build_site`; it never starts the pipeline.

## The page

Header: trip dates, group range, "Atualizado em dd/mm" (the newest `fetched_at` among the shown prices) and a note that
prices are for paying guests and infants are not counted.

Two sections, whose membership is fixed at build time and does not change with the filters:

1. "Têm todos os essenciais": every must-have aspect is `MET`.
2. "Faltam essenciais": all other houses. Each card lists the must-haves that are not met and why: `❌ sem
   ar-condicionado`, `⚠️ parcial`, `❓ não confirmado`.

A listing in the trip file that has no stored house or no analysis gets a small "sem análise" card at the bottom of the
second section (name = listing id, Airbnb link). Nothing is silently dropped.

Card (collapsed): name, rating and review count, city, distance to the beach (or "n/d"), per-person price at the
selected group size, and one row per aspect with icon and label (★ on must-haves). Status is never conveyed by colour
alone.

Card (expanded with `<details>`, no JS): summary and confidence note, pros and cons, each verdict's reason with its
quotes, the beach-distance quote, the full price table (one row per group size, original price only when discounted),
the family's `extra` notes and the Airbnb link.

Layout: one column on a phone, a two or three column grid from a wider breakpoint. System fonts, inline CSS, no
external requests, `<meta name="robots" content="noindex">`.

## Filtering and sorting

A collapsible "Filtros" panel (a button on phones, always open on wide screens):

- Group size, 9 to 13 (the trip's range), default the largest. Price filter and price sort use this size.
- Price per person: min and max number fields in reais (no slider).
- City: one checkbox per city found in the data; none checked means all.
- Distance to the beach: any, up to 100 m, up to 300 m, up to 1 km. Unknown distance only matches "any".
- Aspects: one checkbox per aspect (★ for must-haves). Checked means "only houses where it is `MET`"; partial and
  unknown do not match.
- Sort: price per person ascending (default), rating descending, distance to the beach ascending.
- "Limpar filtros" resets everything.

Behaviour: filters apply inside both sections. Section headings show live counts ("Têm todos os essenciais (4 de 7)").
A section with no match shows a short message. Houses without a price for the selected size are hidden while a price
bound is set and otherwise sort last. Sorting ties break by the other criteria: price ties by rating, rating ties by
price, distance ties by price; unknown values sort last. Unanalysed ("sem análise") cards sort last in every mode
(`data-pending="1"`).

Implementation: each card carries `data-*` attributes (`data-city`, `data-rating`, `data-reviews`, `data-beach`,
`data-price9` ... `data-price13` in cents per person, `data-met` = space-separated indexes of `MET` aspects). The
filter state is a plain object, `filters.js` exposes pure functions (`matches(card, state)`, `compare(a, b, state)`)
and a thin DOM layer applies them by toggling `hidden` and reordering nodes. The server-side order is already the
default sort.

Per-person prices are rounded up to whole cents of the total, exactly as in the spreadsheet (`price_table`).

## Encryption and the unlock shell

- Build: PBKDF2-HMAC-SHA256, 600 000 iterations, random 16-byte salt, 32-byte key; AES-256-GCM with a random 12-byte
  IV. Payload = base64(salt || iv || ciphertext || tag), stored in the shell with the iteration count. A new salt and
  IV on every publish.
- Shell (`index.html`, unencrypted): a passphrase field, a "lembrar neste aparelho" checkbox, a button and about 40
  lines of WebCrypto. A wrong passphrase or a modified payload fails the GCM check and shows "senha incorreta". On
  success the script replaces the document with the decrypted page using `document.open/write/close`, so the page's
  scripts run.
- "Lembrar neste aparelho" stores the passphrase in that browser's `localStorage` and unlocks automatically next time;
  if it no longer decrypts (a new passphrase was published), the form is shown again. Off by default.
- Strength rule: `site` refuses to build when `SITE_PASSPHRASE` is missing, does not have either five words of 3+
  letters (separated by `-` or spaces) with 20+ characters, or 40+ characters, and in both cases 12+ distinct
  characters (so `the cat dog red sun` and 30 times `x` are refused). This is a guard against obviously weak
  choices, not proof of strength; the generator redraws the rare phrase that would fail it. `casa-certa passphrase`
  prints five random words drawn with `secrets` from a vendored word list of at least 2048 words (about 55 bits).
- No custom cryptography: Python `cryptography` primitives to encrypt, browser WebCrypto to decrypt.

## Publishing

`publish` creates a temporary directory outside the repository, runs `git init`, copies the site files, commits
("chore: publish site") and force-pushes `HEAD` to `gh-pages` of the URL given by `git remote get-url origin`. It
never touches the working tree, `master` or any local branch. It asks for confirmation unless `--yes`, and prints the
Pages URL. Because every publish is one orphan commit, old prices and old ciphertexts do not accumulate in history.

One-time manual step: repository Settings, Pages, deploy from branch `gh-pages`, root.

## Testing

No network and no paid calls.

- Analysis: schema and prompt tests for the new field; repository round trip of the three new columns; migration test
  as in `test_migrations.py`; the existing prompt-hash tests updated.
- `web/model.py`: section membership (partial, unknown and unmet all go to section 2), sort order by default (cheapest
  per person at the largest size, unavailable last, rating tie-break), city parsing ("City, State, Brasil" gives
  "City"; missing location gives no city (shown as n/d)), failed or unanalyzed listings, the not-available price case.
- `web/render.py`: rendering a fixture house gives a page with all aspects, the `data-*` attributes and the details;
  the page contains no secret and no `.env` value.
- `web/crypto.py`: round trip, wrong passphrase, tampered payload, weak-passphrase refusal, distinct output on each
  build.
- `web/publish.py`: against a temporary bare repository used as `origin`; asserts one commit on `gh-pages`, that the
  working tree and `master` are untouched, and that a second publish replaces the history.
- JS: `filters.js` tested with Node's built-in `node --test` on plain objects (price bounds, unavailable prices,
  city, distance bands, aspect checkboxes, every sort). A pytest wrapper runs them and is skipped when `node` is
  missing.
- End to end: a test builds a real encrypted page, runs the unlock script in Node (which has WebCrypto) against a stub
  `document`, and asserts that the stub received the decrypted HTML.

## Decisions taken with the user

- Public repository and public Pages URL, so the content is encrypted with a shared passphrase (option B).
- Houses are ordered by cheapest per person at the largest group size by default.
- Distance to the beach is extracted by the analysis (nullable, estimated, with its quote).
- The site lives on the `gh-pages` branch of this repository. Verbatim review quotes are included in the expanded
  details (they are encrypted).
