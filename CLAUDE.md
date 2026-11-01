# casa-certa

Compares Airbnb houses for a family trip. Design: `docs/superpowers/specs/2026-10-31-casa-certa-design.md`.

## Conventions

- **All code-related text is English** (identifiers, comments, docstrings, errors, tests, commits, docs). Portuguese only for user-facing output and Airbnb data.
- **Tooling:** `uv` for packages, `ruff` (format + lint, 120 cols), `mypy --strict` on the package (tests may omit annotations).
- **Style:** clean, readable code that is easy to test. Small functions, typed Pydantic models at boundaries, dependencies passed in rather than imported globally. Never fight Python's built-in interfaces (dunder methods, context managers, iterators, dataclasses, `Protocol`) when they fit.
- **Async everywhere**, including SQLAlchemy (async engine + `aiosqlite`).
- **No JSON columns** in the database; everything is typed columns and child tables.
- **Secrets** live in `.env` (gitignored). Never print or commit them.
- **Personal data stays out of the repository (it is public):** `trip.yaml` (the user's real listings, dates and preferences) is gitignored, only `trip.example.yaml` is tracked. Test fixtures must be anonymized (no real host or reviewer names, photos, profile ids or exact coordinates). Do not commit the `.db` or `.xlsx` outputs.
- **Commits** follow Conventional Commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`, `build:`...); a commit-msg hook enforces it. Hooks run ruff (with `--fix`), ruff format and mypy on commit and pytest on push. Do not bypass them with `--no-verify`.
- **LLM models:** `deepseek-chat` for every task for now. `deepseek-reasoner` is deferred; tasks move to it per setting (`ANALYSIS_MODEL`) only if the cheap model proves insufficient.
- **Cost control:** do not re-fetch stored data unless `--refresh`. Be careful with Apify/DeepSeek calls in tests: use fakes; live tests are marked `@pytest.mark.live` and skipped by default.
- **Sources:** Apify actors sit behind `PriceSource`/`ReviewSource` adapters chosen by settings (`PRICE_SOURCES`, `REVIEW_SOURCES`, `SEARCH_SOURCES`); search results are screened by `SCREEN_MODEL` (only clear must-have failures are dropped). Never call Airbnb's private API directly (it was blocked as circumvention).
- **Site:** the published page is always encrypted (`SITE_PASSPHRASE` in `.env`); `site/` is gitignored and goes only to the `gh-pages` branch via `make publish`. Never commit a decrypted page.

## Commands

```
make help         # list every target
make install      # uv sync + git hooks (pre-commit, commit-msg, pre-push)
make check        # format check, ruff, mypy, tests (what CI would verify)
make format lint  # format and apply safe lint fixes
make test         # tests; make test-live runs the live smoke test (spends money)
make test-live-sources  # cross-check the cheap Apify actors (spends a few cents)
make search       # search the places in trip.yaml and store the houses found (spends money)
make analyse      # analyse the urls plus the last search and write the spreadsheet
make test-live-search   # one real search of one place (spends about a cent)
make dry-run      # show what a run would fetch and cost
make site         # build the encrypted static site from stored data (no network)
make publish      # publish the built site to the gh-pages branch
make passphrase   # print a new random passphrase for SITE_PASSPHRASE
```
