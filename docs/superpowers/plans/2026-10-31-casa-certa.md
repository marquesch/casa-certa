# casa-certa Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read a `trip.yaml` (Airbnb URLs, dates, aspects), gather facts/price/reviews cache-first, analyze each house with DeepSeek, and write an `.xlsx` with a comparison sheet and a details sheet (pros/cons, evidence, price table for 9–13 people).

**Architecture:** An async pipeline (`pipeline.py`) orchestrates small, separately testable modules: a config source, an Airbnb page extractor (free), an Apify gateway (price + capped reviews), an async SQLAlchemy repository (typed columns, no JSON), two Pydantic AI agents (namer and analyst, both on the cheap `deepseek-chat` model for now; each task's model is a setting) and an openpyxl exporter. Everything external is injected, so tests use fakes and never spend money.

**Tech Stack:** Python 3.11+, uv, ruff (120 cols), mypy --strict, httpx, apify-client (async), SQLAlchemy 2 async + aiosqlite + Alembic, Pydantic 2 + pydantic-settings, Pydantic AI (DeepSeek `deepseek-chat` via the OpenAI-compatible provider; `deepseek-reasoner` deferred), pre-commit, Makefile, openpyxl, pytest + pytest-asyncio.

Spec: `docs/superpowers/specs/2026-10-31-casa-certa-design.md`. Conventions: `CLAUDE.md` (English-only code, no JSON columns, never re-fetch without `--refresh`).

**Commit trailer:** every commit message ends with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` (pass it as a second `-m`).

**Checks used throughout** (`make format lint check`: format, lint with safe fixes, then verify format, lint, mypy and tests; it must end clean before every commit). The pre-commit hooks installed in Task 1 re-run ruff and mypy on every commit and reject non-conventional commit messages, so use `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`, `build:` etc.
```bash
make format lint check
```

## File structure

```
casa_certa/
  __init__.py
  enums.py                 AspectStatus, EvidenceSource, RuleKind, PointKind
  config.py                Aspect, TripConfig, DatabaseSettings, Settings
  i18n.py                  Labels per language for sheet text
  pricing.py               price_table, format_brl
  config_sources/
    __init__.py
    base.py                ConfigSource protocol, ConfigError
    yaml_source.py         YamlConfigSource
  sources/
    __init__.py
    airbnb_page.py         plain-request static facts (async httpx)   [moved from casa_certa/airbnb_page.py]
    apify.py               price + reviews via an injectable ActorRunner
  db/
    __init__.py
    models.py              ORM tables
    session.py             engine + session factory
    repository.py          HouseRepository
  ai/
    __init__.py
    schemas.py             HouseAnalysis, HouseName
    prompts.py             instructions, prompt builders, prompt_hash
    agents.py              make_deepseek_model, HouseNamer, HouseAnalyst, make_unique_name
  pipeline.py              RunPlan, RunReport, Pipeline
  export/
    __init__.py
    xlsx.py                build_workbook
  cli.py                   argparse entry point
alembic/                   async migrations
tests/
  conftest.py              shared fixtures
  fakes.py                 FakeRunner, mock http client, sample analysis
  fixtures/                saved pages and Apify items
  test_*.py                one file per module
trip.yaml                  the user's trip (edit this)
Makefile                   developer shortcuts (`make help`)
.pre-commit-config.yaml    commit hooks: conventional commits, ruff --fix, ruff format, mypy; pytest on push
```

---

### Task 1: Tooling baseline (pre-commit, Makefile, typing config) and the first commit

**Files:**
- Modify: `pyproject.toml`, `CLAUDE.md`
- Create: `.pre-commit-config.yaml`, `Makefile`
- Commit: everything already written (extractor, tests, `uv.lock`, `.gitignore`)

- [ ] **Step 1: Update `pyproject.toml`**

Replace the `[tool.mypy]` and `[tool.pytest.ini_options]` tables with:

```toml
[tool.mypy]
python_version = "3.11"
strict = true
plugins = ["pydantic.mypy"]
files = ["casa_certa", "tests"]

[[tool.mypy.overrides]]
module = "tests.*"
disallow_untyped_defs = false
disallow_incomplete_defs = false

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
markers = ["live: hits real services and costs money (skipped by default)"]
addopts = "-m 'not live'"
```

Make sure `[tool.ruff]` starts with this line (ruff 0.17 also formats Markdown, and the plan/spec contain intentionally unformatted code blocks):

```toml
extend-exclude = ["alembic/versions", "docs"]
```

If a `[[tool.mypy.overrides]]` block already exists at the bottom of the file, delete that old one so it is not duplicated. Keep `pre-commit` in the `dev` dependency group (`uv add --dev pre-commit` is a no-op if it is already there).

- [ ] **Step 2: Create `.pre-commit-config.yaml`**

```yaml
# Install once with `make install` (also installs the commit-msg and pre-push hooks).
default_install_hook_types: [pre-commit, commit-msg, pre-push]
default_stages: [pre-commit]
exclude: ^(tests/fixtures/|uv\.lock$|alembic/versions/)

repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
      - id: check-merge-conflict
      - id: check-added-large-files
        args: [--maxkb=500]
      - id: detect-private-key

  # Commit messages must follow Conventional Commits: "feat: add x", "fix(db): y", ...
  - repo: https://github.com/compilerla/conventional-pre-commit
    rev: v4.5.0
    hooks:
      - id: conventional-pre-commit
        stages: [commit-msg]
        args: [feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert]

  # Local hooks use `uv run`, so they always match the versions pinned in uv.lock.
  - repo: local
    hooks:
      - id: ruff-check
        name: ruff check --fix
        entry: uv run ruff check --fix
        language: system
        types_or: [python, pyi]
      - id: ruff-format
        name: ruff format
        entry: uv run ruff format
        language: system
        types_or: [python, pyi]
      - id: mypy
        name: mypy
        entry: uv run mypy
        language: system
        types: [python]
        pass_filenames: false
      - id: pytest
        name: pytest
        entry: uv run pytest -q
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-push]
```

The local hooks go through `uv run`, so they always use the versions pinned in `uv.lock`. Hooks skip `tests/fixtures/` (saved HTML/JSON with their own whitespace) and generated migrations. Tests run on `git push`, not on every commit, to keep commits fast.

- [ ] **Step 3: Create the `Makefile`**

The recipe lines must be indented with a real TAB character.

```make
.DEFAULT_GOAL := help
TRIP ?= trip.yaml

.PHONY: help install hooks format lint typecheck test test-live check dry-run run refresh migrate migration clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies and the git hooks
	uv sync
	uv run pre-commit install

hooks: ## Run every pre-commit hook on all files
	uv run pre-commit run --all-files

format: ## Format the code with ruff
	uv run ruff format .

lint: ## Lint with ruff and apply safe fixes
	uv run ruff check --fix .

typecheck: ## Static type check with mypy
	uv run mypy

test: ## Run the tests (live tests are skipped)
	uv run pytest -q

test-live: ## Run the live smoke test (spends a few cents)
	uv run pytest -m live tests/test_live.py -s

check: ## Everything CI would verify, without changing files
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run pytest -q

dry-run: ## Show what a run would fetch and cost (TRIP=trip.yaml)
	uv run casa-certa run $(TRIP) --dry-run

run: ## Analyze the houses and write the spreadsheet (TRIP=trip.yaml)
	uv run casa-certa run $(TRIP)

refresh: ## Re-fetch everything from Airbnb, costs money (TRIP=trip.yaml)
	uv run casa-certa run $(TRIP) --refresh

migrate: ## Apply database migrations
	uv run alembic upgrade head

migration: ## Create a migration: make migration m="add a column"
	$(if $(m),,$(error usage: make migration m="message"))
	uv run alembic revision --autogenerate -m "$(m)"

clean: ## Remove caches
	rm -rf .mypy_cache .pytest_cache .ruff_cache
	find . -path ./.venv -prune -o -name __pycache__ -type d -exec rm -rf {} +
```

- [ ] **Step 4: Update `CLAUDE.md`**

Replace the "Commands" section with:

````markdown
## Commands

```
make help         # list every target
make install      # uv sync + git hooks (pre-commit, commit-msg, pre-push)
make check        # format check, ruff, mypy, tests (what CI would verify)
make format lint  # format and apply safe lint fixes
make test         # tests; make test-live runs the live smoke test (spends money)
make dry-run      # show what a run would fetch and cost
```
````

and add this bullet to "Conventions":

```markdown
- **Commits** follow Conventional Commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`, `build:`...); a commit-msg hook enforces it. Hooks run ruff (with `--fix`), ruff format and mypy on commit and pytest on push. Do not bypass them with `--no-verify`.
- **LLM models:** `deepseek-chat` for every task for now. `deepseek-reasoner` is deferred; tasks move to it per setting (`ANALYSIS_MODEL`) only if the cheap model proves insufficient.
```

- [ ] **Step 5: Install the hooks and verify**

Run:

```bash
make install
make check
```
Expected: `uv sync` finishes, `pre-commit installed at .git/hooks/pre-commit`, `.git/hooks/commit-msg` and `.git/hooks/pre-push`; `make check` ends with `11 passed`.

Verify the commit-message hook with one bad and one good message:

```bash
printf 'added some stuff\n' > /tmp/bad-msg.txt
printf 'feat: add a thing\n' > /tmp/good-msg.txt
uv run pre-commit run conventional-pre-commit --hook-stage commit-msg --commit-msg-filename /tmp/bad-msg.txt   # must FAIL
uv run pre-commit run conventional-pre-commit --hook-stage commit-msg --commit-msg-filename /tmp/good-msg.txt  # must pass
rm /tmp/bad-msg.txt /tmp/good-msg.txt
```

- [ ] **Step 6: Commit the baseline**

```bash
git add .gitignore .pre-commit-config.yaml Makefile CLAUDE.md pyproject.toml uv.lock casa_certa tests docs
git commit -m "chore: add tooling baseline, pre-commit hooks and the page extractor" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
If a hook fixes files (ruff, end-of-file) the commit aborts: `git add -u` and run the same commit again.

---

### Task 2: Enums, config models and the YAML config source

**Files:**
- Create: `casa_certa/enums.py`, `casa_certa/config.py`, `casa_certa/config_sources/__init__.py`, `casa_certa/config_sources/base.py`, `casa_certa/config_sources/yaml_source.py`, `trip.yaml`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config.py`:

```python
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from casa_certa.config import Aspect, AspectKind, Settings, TripConfig
from casa_certa.config_sources.base import ConfigError
from casa_certa.config_sources.yaml_source import YamlConfigSource


def make_trip(**overrides: object) -> TripConfig:
    values: dict[str, object] = {
        "urls": ["https://www.airbnb.com.br/rooms/1"],
        "check_in": date(2026, 12, 5),
        "check_out": date(2026, 12, 9),
    }
    values.update(overrides)
    return TripConfig.model_validate(values)


def test_nights_and_group_sizes() -> None:
    trip = make_trip(min_people=9, max_people=13)
    assert trip.nights == 4
    assert list(trip.group_sizes()) == [9, 10, 11, 12, 13]


def test_check_out_must_be_after_check_in() -> None:
    with pytest.raises(ValidationError):
        make_trip(check_in=date(2026, 12, 9), check_out=date(2026, 12, 5))


def test_min_people_cannot_exceed_max_people() -> None:
    with pytest.raises(ValidationError):
        make_trip(min_people=14, max_people=13)


def test_aspect_kind_defaults_to_nice_to_have() -> None:
    assert Aspect(name="Pool").kind is AspectKind.NICE_TO_HAVE


def test_settings_can_be_built_without_env_file() -> None:
    settings = Settings(_env_file=None, apify_token="a", deepseek_api_key="b")  # type: ignore[arg-type]
    assert settings.apify_token.get_secret_value() == "a"
    assert settings.database_url.startswith("sqlite+aiosqlite")


async def test_yaml_source_loads_trip(tmp_path: Path) -> None:
    path = tmp_path / "trip.yaml"
    path.write_text(
        """
urls:
  - https://www.airbnb.com.br/rooms/46397403
check_in: 2026-12-05
check_out: 2026-12-09
adults: 10
children: 1
infants: 1
aspects:
  - name: Close to the beach
    kind: must_have
  - name: Pool
""",
        encoding="utf8",
    )
    trip = await YamlConfigSource(path).load()
    assert trip.adults == 10
    assert [a.kind for a in trip.aspects] == [AspectKind.MUST_HAVE, AspectKind.NICE_TO_HAVE]


async def test_yaml_source_reports_invalid_file(tmp_path: Path) -> None:
    path = tmp_path / "trip.yaml"
    path.write_text("urls: []\n", encoding="utf8")
    with pytest.raises(ConfigError):
        await YamlConfigSource(path).load()


async def test_yaml_source_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        await YamlConfigSource(tmp_path / "nope.yaml").load()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'casa_certa.config'`.

- [ ] **Step 3: Write the implementation**

Create `casa_certa/enums.py`:

```python
"""Enumerations shared by the database models and the AI output schemas."""

from enum import StrEnum


class AspectStatus(StrEnum):
    MET = "met"
    PARTIAL = "partial"
    UNMET = "unmet"
    UNKNOWN = "unknown"


class EvidenceSource(StrEnum):
    LISTING = "listing"
    REVIEW = "review"
    EXTRA = "extra"


class RuleKind(StrEnum):
    RULE = "rule"
    SAFETY = "safety"


class PointKind(StrEnum):
    PRO = "pro"
    CON = "con"
```

Create `casa_certa/config.py`:

```python
"""Trip configuration (what to compare) and application settings (secrets, models, database)."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///casa_certa.db"


class AspectKind(StrEnum):
    MUST_HAVE = "must_have"
    NICE_TO_HAVE = "nice_to_have"


class Aspect(BaseModel):
    name: str
    kind: AspectKind = AspectKind.NICE_TO_HAVE
    details: str = ""


class TripConfig(BaseModel):
    """Everything the pipeline needs to know about the trip. The only input contract of the pipeline."""

    urls: list[str] = Field(min_length=1)
    check_in: date
    check_out: date
    adults: int = Field(default=10, ge=1)
    children: int = Field(default=0, ge=0)
    infants: int = Field(default=0, ge=0)
    min_people: int = Field(default=9, ge=1)
    max_people: int = Field(default=13, ge=1)
    aspects: list[Aspect] = Field(default_factory=list)
    language: str = "pt-BR"
    review_cap: int = Field(default=30, ge=0)

    @model_validator(mode="after")
    def _validate_ranges(self) -> TripConfig:
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        if self.min_people > self.max_people:
            raise ValueError("min_people cannot exceed max_people")
        return self

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days

    def group_sizes(self) -> range:
        return range(self.min_people, self.max_people + 1)


class DatabaseSettings(BaseSettings):
    """Only the database URL; used by Alembic, which must not require API keys."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = DEFAULT_DATABASE_URL


class Settings(DatabaseSettings):
    apify_token: SecretStr
    deepseek_api_key: SecretStr
    cheap_model: str = "deepseek-chat"
    # Model for the review analysis. Cheap model for now; set ANALYSIS_MODEL=deepseek-reasoner later to move
    # this task to the reasoning model without code changes.
    analysis_model: str = "deepseek-chat"
    page_delay_min: float = 2.0
    page_delay_max: float = 5.0
```

Create `casa_certa/config_sources/__init__.py` (empty file).

Create `casa_certa/config_sources/base.py`:

```python
"""Where a TripConfig comes from. Today a YAML file; later possibly WhatsApp messages."""

from typing import Protocol

from casa_certa.config import TripConfig


class ConfigError(Exception):
    """The trip configuration could not be read or is invalid."""


class ConfigSource(Protocol):
    async def load(self) -> TripConfig: ...
```

Create `casa_certa/config_sources/yaml_source.py`:

```python
"""Load a TripConfig from a YAML file."""

import asyncio
from pathlib import Path

import yaml
from pydantic import ValidationError

from casa_certa.config import TripConfig
from casa_certa.config_sources.base import ConfigError


class YamlConfigSource:
    def __init__(self, path: Path) -> None:
        self._path = path

    async def load(self) -> TripConfig:
        try:
            text = await asyncio.to_thread(self._path.read_text, encoding="utf8")
        except OSError as e:
            raise ConfigError(f"Cannot read {self._path}: {e}") from e
        try:
            return TripConfig.model_validate(yaml.safe_load(text))
        except (yaml.YAMLError, ValidationError) as e:
            raise ConfigError(f"Invalid trip file {self._path}: {e}") from e
```

Create `trip.yaml` (the user edits the aspects):

```yaml
urls:
  - https://www.airbnb.com.br/rooms/46397403
  - https://www.airbnb.com.br/rooms/52584351
check_in: 2026-12-05
check_out: 2026-12-09
adults: 10
children: 1
infants: 1
min_people: 9
max_people: 13
language: pt-BR
review_cap: 30
aspects:
  - name: Perto da praia
    kind: must_have
    details: Ideal a poucos minutos a pé; considere o que dizem as avaliações.
  - name: Ar-condicionado em todos os quartos
    kind: must_have
  - name: Piscina
    kind: nice_to_have
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config.py -q`
Expected: 7 passed. Mypy runs in the checks step: if it reports an `# type: ignore` in this file (or `Settings()  # type: ignore[call-arg]` later in `cli.py` / `test_live.py`) as unused, delete that comment; if it instead reports a missing-argument error where there is no ignore, add the matching `# type: ignore[...]`. The pydantic mypy plugin decides which of the two happens.

- [ ] **Step 5: Run the checks and commit**

```bash
make format lint check
git add casa_certa tests trip.yaml
git commit -m "feat: add trip config, settings and YAML config source" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Make the page extractor async and Pydantic-based

**Files:**
- Move: `casa_certa/airbnb_page.py` → `casa_certa/sources/airbnb_page.py`
- Create: `casa_certa/sources/__init__.py`
- Modify: `casa_certa/sources/airbnb_page.py`, `tests/test_airbnb_page.py`

- [ ] **Step 1: Move the file and update test imports**

```bash
mkdir -p casa_certa/sources && touch casa_certa/sources/__init__.py
git mv casa_certa/airbnb_page.py casa_certa/sources/airbnb_page.py 2>/dev/null || mv casa_certa/airbnb_page.py casa_certa/sources/airbnb_page.py
sed -i 's/from casa_certa.airbnb_page import/from casa_certa.sources.airbnb_page import/; s/from casa_certa import airbnb_page/from casa_certa.sources import airbnb_page/' tests/test_airbnb_page.py
```

- [ ] **Step 2: Replace the obsolete tests with async ones**

In `tests/test_airbnb_page.py`:

1. In the import block replace `build_url` with `listing_url`, and add `fetch_html`, `fetch_listings`, `PageFetchError`:

```python
import httpx
import pytest

from casa_certa.sources.airbnb_page import (
    ListingFacts,
    PageFetchError,
    PageParseError,
    fetch_html,
    fetch_listings,
    html_to_text,
    listing_url,
    parse_listing_html,
    room_id_from_url,
)
```

2. Replace `test_build_url_only_includes_given_params` with:

```python
def test_listing_url() -> None:
    assert listing_url("1") == "https://www.airbnb.com.br/rooms/1"
```

3. Replace `test_to_dict_is_json_serializable` with:

```python
def test_model_dump_is_json_serializable(matinhos: ListingFacts) -> None:
    matinhos.model_dump_json()
```

4. Delete `test_fetch_listings_pauses_between_requests_and_isolates_errors` and append:

```python
def mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_html_retries_once_on_429() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429 if len(calls) == 1 else 200, text="ok")

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async with mock_client(handler) as client:
        text = await fetch_html("https://x/rooms/1", client=client, retry_wait=7, sleep=fake_sleep)
    assert text == "ok"
    assert sleeps == [7]


async def test_fetch_html_raises_after_second_failure() -> None:
    async def fake_sleep(seconds: float) -> None:
        return None

    async with mock_client(lambda request: httpx.Response(503)) as client:
        with pytest.raises(PageFetchError, match="HTTP 503"):
            await fetch_html("https://x/rooms/1", client=client, sleep=fake_sleep)


async def test_fetch_html_raises_on_blocked_status() -> None:
    async with mock_client(lambda request: httpx.Response(403)) as client:
        with pytest.raises(PageFetchError, match="HTTP 403"):
            await fetch_html("https://x/rooms/1", client=client)


async def test_fetch_listings_pauses_between_requests_and_isolates_errors() -> None:
    html = (FIXTURES / "room_46397403.html").read_text(encoding="utf8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/222"):
            return httpx.Response(403)
        return httpx.Response(200, text=html)

    pauses: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        pauses.append(seconds)

    async with mock_client(handler) as client:
        out = await fetch_listings(["111", "222", "333"], client=client, delay=(2, 5), sleep=fake_sleep)

    assert len(pauses) == 2  # no pause before the first request
    assert all(2 <= p <= 5 for p in pauses)
    assert isinstance(out["111"], ListingFacts)
    assert isinstance(out["222"], PageFetchError)
    assert isinstance(out["333"], ListingFacts)


async def test_fetch_listings_reports_bad_ids_as_errors() -> None:
    async with mock_client(lambda request: httpx.Response(200, text="")) as client:
        out = await fetch_listings(["not-a-listing"], client=client)
    assert isinstance(out["not-a-listing"], PageFetchError)
```

Add `from collections.abc import Callable` to the imports at the top of the test file.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_airbnb_page.py -q`
Expected: FAIL (`ImportError: cannot import name 'listing_url'`).

- [ ] **Step 4: Convert the models to Pydantic**

In `casa_certa/sources/airbnb_page.py`:

1. Replace the module docstring paragraph "No third-party dependencies (standard library only)." and the "Quick usage" lines with: `Async: use an httpx.AsyncClient from make_client().` (delete the two quick-usage lines).
2. Replace the whole import block with:

```python
import asyncio
import html as html_lib
import json
import random
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import BaseModel, Field
```

(`from __future__ import annotations` stays above it.)

3. Replace every model class (from `@dataclass class Amenity` to the end of `ListingFacts`, including `to_dict`) with:

```python
class Amenity(BaseModel):
    group: str
    name: str
    available: bool
    note: str = ""


class Room(BaseModel):
    name: str
    beds: list[str]


class Highlight(BaseModel):
    type: str
    headline: str
    body: str


class RuleItem(BaseModel):
    group: str
    type: str
    title: str
    description: str = ""


class Host(BaseModel):
    name: str | None = None
    is_superhost: bool | None = None
    is_verified: bool | None = None
    rating: float | None = None
    rating_count: int | None = None
    years_hosting: int | None = None
    response_rate: str | None = None
    response_time: str | None = None


class ListingFacts(BaseModel):
    listing_id: str
    title: str | None = None
    property_type: str | None = None
    space_type: str | None = None
    person_capacity: int | None = None
    overview: list[str] = Field(default_factory=list)  # as shown by Airbnb, e.g. ["12 hóspedes", "4 quartos"]
    bedrooms: int | None = None
    beds: int | None = None
    bathrooms: float | None = None
    rating: float | None = None
    review_count: int | None = None
    category_ratings: dict[str, float] = Field(default_factory=dict)
    is_guest_favorite: bool | None = None
    description: str | None = None
    amenities: list[Amenity] = Field(default_factory=list)
    rooms: list[Room] = Field(default_factory=list)
    highlights: list[Highlight] = Field(default_factory=list)
    location_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_is_exact: bool | None = None
    host: Host = Field(default_factory=Host)
    house_rules: list[RuleItem] = Field(default_factory=list)
    safety: list[RuleItem] = Field(default_factory=list)

    def available_amenities(self) -> list[str]:
        return [a.name for a in self.amenities if a.available]

    def unavailable_amenities(self) -> list[str]:
        return [a.name for a in self.amenities if not a.available]
```

4. Delete `build_url` and replace it with:

```python
def listing_url(room_id: str, *, domain: str = DEFAULT_DOMAIN) -> str:
    return f"https://{domain}/rooms/{room_id}"
```

- [ ] **Step 5: Replace the network and API section**

Delete `fetch_html`, `fetch_listing`, `fetch_listings` and `main` (and the `if __name__ == "__main__"` block), plus the now-unused `DEFAULT_USER_AGENT`/`DEFAULT_ACCEPT_LANGUAGE` usages inside the old `fetch_html`. Keep the two constants. Append at the end of the file:

```python
# --------------------------------------------------------------------------- network

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
Sleep = Callable[[float], Awaitable[None]]


def make_client(*, timeout: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept-Language": DEFAULT_ACCEPT_LANGUAGE,
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=timeout,
        follow_redirects=True,
    )


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    try:
        return await client.get(url)
    except httpx.HTTPError as e:
        raise PageFetchError(f"Network failure while fetching {url}: {e}") from e


async def fetch_html(
    url: str,
    *,
    client: httpx.AsyncClient,
    retry_wait: float = 10.0,
    sleep: Sleep = asyncio.sleep,
) -> str:
    """GET a page, retrying once (after `retry_wait` seconds) on 429/5xx."""
    response = await _get(client, url)
    if response.status_code in RETRYABLE_STATUSES:
        await sleep(retry_wait)
        response = await _get(client, url)
    if response.status_code != 200:
        raise PageFetchError(f"HTTP {response.status_code} while fetching {url}")
    return response.text


async def fetch_listing(
    url_or_id: str,
    *,
    client: httpx.AsyncClient,
    retry_wait: float = 10.0,
    sleep: Sleep = asyncio.sleep,
) -> ListingFacts:
    room_id = room_id_from_url(url_or_id)
    page_html = await fetch_html(listing_url(room_id), client=client, retry_wait=retry_wait, sleep=sleep)
    return parse_listing_html(page_html)


async def fetch_listings(
    urls_or_ids: list[str],
    *,
    client: httpx.AsyncClient,
    delay: tuple[float, float] = (2.0, 5.0),
    sleep: Sleep = asyncio.sleep,
) -> dict[str, ListingFacts | AirbnbPageError]:
    """Fetch several listings sequentially with a random pause between requests.

    A failure on one listing does not stop the others: the error is returned in place of the result.
    """
    results: dict[str, ListingFacts | AirbnbPageError] = {}
    for index, item in enumerate(urls_or_ids):
        if index > 0:
            await sleep(random.uniform(*delay))
        try:
            results[item] = await fetch_listing(item, client=client, sleep=sleep)
        except AirbnbPageError as e:
            results[item] = e
        except ValueError as e:  # not a listing url or id
            results[item] = PageFetchError(str(e))
    return results
```

Also delete the now-unused `import sys` / `time` / `urllib` leftovers if any remain.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_airbnb_page.py -q`
Expected: all pass (the pre-existing parsing tests still pass: construction with keyword arguments is unchanged).

- [ ] **Step 7: Run the checks and commit**

```bash
make format lint check
git add casa_certa tests
git commit -m "refactor: make the page extractor async with httpx and Pydantic models" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Apify gateway (price + capped reviews)

**Files:**
- Create: `casa_certa/sources/apify.py`, `tests/fakes.py`
- Test: `tests/test_apify.py`

Saved fixtures already exist: `tests/fixtures/apify_rooms_46397403.json`, `apify_rooms_52584351.json`, `apify_reviews_46397403.json`.

- [ ] **Step 1: Write the shared fake runner**

Create `tests/fakes.py`:

```python
"""Test doubles shared across test modules."""

from typing import Any


class FakeRunner:
    """An ActorRunner that returns canned dataset items per actor id and records every call."""

    def __init__(self, outputs: dict[str, list[dict[str, Any]]]) -> None:
        self.outputs = outputs
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def run_actor(self, actor_id: str, run_input: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((actor_id, run_input))
        return self.outputs.get(actor_id, [])

    def calls_to(self, actor_id: str) -> list[dict[str, Any]]:
        return [run_input for called, run_input in self.calls if called == actor_id]
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_apify.py`:

```python
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from casa_certa.config import TripConfig
from casa_certa.sources.apify import (
    PRICE_ACTOR,
    REVIEWS_ACTOR,
    AirbnbApify,
    ApifyError,
    PriceParseError,
    parse_brl_cents,
    parse_price_item,
    parse_review_item,
)
from tests.fakes import FakeRunner

FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name: str) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads((FIXTURES / name).read_text(encoding="utf8"))
    return data


def make_trip(**overrides: Any) -> TripConfig:
    values: dict[str, Any] = {
        "urls": ["https://www.airbnb.com.br/rooms/46397403"],
        "check_in": date(2026, 12, 5),
        "check_out": date(2026, 12, 9),
        "adults": 10,
        "children": 1,
        "infants": 1,
    }
    values.update(overrides)
    return TripConfig.model_validate(values)


@pytest.mark.parametrize(
    ("text", "cents"),
    [("R$ 3.675", 367500), ("R$ 6.431,69", 643169), ("R$ 918,75", 91875), ("R$ 9.377,00", 937700), ("R$ 5", 500)],
)
def test_parse_brl_cents(text: str, cents: int) -> None:
    assert parse_brl_cents(text) == cents


def test_parse_brl_cents_rejects_garbage() -> None:
    with pytest.raises(PriceParseError):
        parse_brl_cents("free")


def test_parse_price_item_plain_price() -> None:
    price = parse_price_item(load_json("apify_rooms_46397403.json")[0])
    assert price.total_cents == 367500
    assert price.original_total_cents is None


def test_parse_price_item_with_discount() -> None:
    price = parse_price_item(load_json("apify_rooms_52584351.json")[0])
    assert price.total_cents == 369100
    assert price.original_total_cents == 398500


def test_parse_price_item_without_price_raises() -> None:
    with pytest.raises(PriceParseError):
        parse_price_item({"id": "1", "price": None})


def test_parse_review_item() -> None:
    review = parse_review_item(load_json("apify_reviews_46397403.json")[0])
    assert review.airbnb_review_id == "1794188945820921552"
    assert review.rating == 5
    assert review.created_at.year == 2026
    assert review.created_at.tzinfo is None
    assert "<br" not in review.text
    assert review.host_response is None


async def test_fetch_prices_builds_input_and_maps_results_by_id() -> None:
    runner = FakeRunner({PRICE_ACTOR: load_json("apify_rooms_46397403.json")})
    apify = AirbnbApify(runner)

    prices = await apify.fetch_prices(["46397403", "999"], make_trip())

    run_input = runner.calls_to(PRICE_ACTOR)[0]
    assert run_input["checkIn"] == "2026-12-05"
    assert run_input["checkOut"] == "2026-12-09"
    assert (run_input["adults"], run_input["children"], run_input["infants"]) == (10, 1, 1)
    assert run_input["currency"] == "BRL"
    assert [u["url"] for u in run_input["startUrls"]] == [
        "https://www.airbnb.com.br/rooms/46397403",
        "https://www.airbnb.com.br/rooms/999",
    ]
    assert not isinstance(prices["46397403"], ApifyError)
    assert isinstance(prices["999"], ApifyError)  # the scraper returned nothing for it


async def test_fetch_reviews_respects_cap_and_defaults_to_empty() -> None:
    runner = FakeRunner({REVIEWS_ACTOR: load_json("apify_reviews_46397403.json")})
    apify = AirbnbApify(runner)

    reviews = await apify.fetch_reviews(["46397403", "999"], make_trip(review_cap=30))

    run_input = runner.calls_to(REVIEWS_ACTOR)[0]
    assert run_input["maxReviewsPerListing"] == 30
    assert run_input["sortBy"] == "most-recent"
    assert len(reviews["46397403"]) == 4
    assert reviews["999"] == []


async def test_fetch_reviews_with_zero_cap_makes_no_call() -> None:
    runner = FakeRunner({})
    reviews = await AirbnbApify(runner).fetch_reviews(["1"], make_trip(review_cap=0))
    assert reviews == {"1": []}
    assert runner.calls == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_apify.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'casa_certa.sources.apify'`.

- [ ] **Step 4: Write the implementation**

Create `casa_certa/sources/apify.py`:

```python
"""Price and review text from Apify actors.

The actual actor execution is hidden behind `ActorRunner`, so tests can inject a fake and the code
stays free of network access.
"""

import re
from datetime import datetime
from typing import Any, Protocol

from apify_client import ApifyClientAsync
from pydantic import BaseModel

from casa_certa.config import TripConfig
from casa_certa.sources.airbnb_page import html_to_text, listing_url

PRICE_ACTOR = "tri_angle/airbnb-rooms-urls-scraper"
REVIEWS_ACTOR = "tri_angle/airbnb-reviews-scraper"

# Rough Apify prices (USD) used only for the cost estimate shown before a run.
PRICE_COST_USD = 0.004
REVIEW_COST_USD = 0.005

_BRL_RE = re.compile(r"R\$\s*([\d.]+(?:,\d{1,2})?)")


class ApifyError(Exception):
    """An Apify actor run failed or returned nothing usable."""


class PriceParseError(ApifyError):
    """The scraper item has no usable price (for example: unavailable on those dates)."""


class PriceData(BaseModel):
    total_cents: int
    original_total_cents: int | None = None


class ReviewData(BaseModel):
    airbnb_review_id: str
    rating: int | None
    created_at: datetime  # naive UTC
    language: str | None
    text: str
    host_response: str | None


class ActorRunner(Protocol):
    async def run_actor(self, actor_id: str, run_input: dict[str, Any]) -> list[dict[str, Any]]: ...


class ApifyActorRunner:
    """Runs an actor on Apify and returns its dataset items."""

    def __init__(self, client: ApifyClientAsync) -> None:
        self._client = client

    async def run_actor(self, actor_id: str, run_input: dict[str, Any]) -> list[dict[str, Any]]:
        run = await self._client.actor(actor_id).call(run_input=run_input)
        if run is None:
            raise ApifyError(f"Actor {actor_id} did not start")
        status = getattr(run.status, "value", run.status)
        if status != "SUCCEEDED" or run.default_dataset_id is None:
            raise ApifyError(f"Actor {actor_id} finished with status {status}")
        page = await self._client.dataset(run.default_dataset_id).list_items()
        return list(page.items)


# --------------------------------------------------------------------------- parsing


def parse_brl_cents(text: str) -> int:
    """'R$ 6.431,69' -> 643169; 'R$ 3.675' -> 367500."""
    match = _BRL_RE.search(text)
    if match is None:
        raise PriceParseError(f"Cannot read a BRL amount from {text!r}")
    whole, _, fraction = match.group(1).replace(".", "").partition(",")
    return int(whole) * 100 + int((fraction + "00")[:2])


def parse_price_item(item: dict[str, Any]) -> PriceData:
    price = item.get("price") or {}
    shown = price.get("price")
    if not shown:
        raise PriceParseError(f"No price for listing {item.get('id')} (unavailable on these dates?)")
    original = price.get("originalPrice")
    return PriceData(
        total_cents=parse_brl_cents(shown),
        original_total_cents=parse_brl_cents(original) if original else None,
    )


def parse_review_item(item: dict[str, Any]) -> ReviewData:
    response = item.get("response")
    return ReviewData(
        airbnb_review_id=str(item["id"]),
        rating=item.get("rating"),
        created_at=datetime.fromisoformat(item["createdAt"]).replace(tzinfo=None),
        language=item.get("language"),
        text=html_to_text(item.get("text")) or "",
        host_response=html_to_text(response) if isinstance(response, str) else None,
    )


# --------------------------------------------------------------------------- gateway


class AirbnbApify:
    def __init__(self, runner: ActorRunner) -> None:
        self._runner = runner

    async def fetch_prices(self, room_ids: list[str], trip: TripConfig) -> dict[str, PriceData | ApifyError]:
        """One actor run for all houses. Houses without a usable result map to an ApifyError."""
        run_input: dict[str, Any] = {
            "startUrls": [{"url": listing_url(room_id)} for room_id in room_ids],
            "checkIn": trip.check_in.isoformat(),
            "checkOut": trip.check_out.isoformat(),
            "adults": trip.adults,
            "children": trip.children,
            "infants": trip.infants,
            "currency": "BRL",
            "locale": trip.language,
        }
        items = await self._runner.run_actor(PRICE_ACTOR, run_input)
        results: dict[str, PriceData | ApifyError] = {
            room_id: ApifyError(f"No price result for listing {room_id}") for room_id in room_ids
        }
        for item in items:
            room_id = str(item.get("id"))
            if room_id not in results:
                continue
            try:
                results[room_id] = parse_price_item(item)
            except ApifyError as e:
                results[room_id] = e
        return results

    async def fetch_reviews(self, room_ids: list[str], trip: TripConfig) -> dict[str, list[ReviewData]]:
        """The most recent `trip.review_cap` reviews per house; houses without reviews map to []."""
        results: dict[str, list[ReviewData]] = {room_id: [] for room_id in room_ids}
        if trip.review_cap == 0 or not room_ids:
            return results
        run_input: dict[str, Any] = {
            "startUrls": [{"url": listing_url(room_id)} for room_id in room_ids],
            "maxReviewsPerListing": trip.review_cap,
            "sortBy": "most-recent",
            "locale": trip.language,
        }
        for item in await self._runner.run_actor(REVIEWS_ACTOR, run_input):
            match = re.search(r"/rooms/(\d+)", str(item.get("startUrl", "")))
            if match and match.group(1) in results:
                results[match.group(1)].append(parse_review_item(item))
        return results
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_apify.py -q`
Expected: 13 passed.

- [ ] **Step 6: Run the checks and commit**

```bash
make format lint check
git add casa_certa tests
git commit -m "feat: add Apify gateway for price and capped reviews" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Pricing table and sheet labels

**Files:**
- Create: `casa_certa/pricing.py`, `casa_certa/i18n.py`
- Test: `tests/test_pricing.py`, `tests/test_i18n.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pricing.py`:

```python
from datetime import date

from casa_certa.config import TripConfig
from casa_certa.pricing import format_brl, price_table

TRIP = TripConfig(urls=["x"], check_in=date(2026, 12, 5), check_out=date(2026, 12, 9), min_people=9, max_people=13)


def test_format_brl() -> None:
    assert format_brl(367500) == "R$ 3.675,00"
    assert format_brl(643169) == "R$ 6.431,69"
    assert format_brl(5) == "R$ 0,05"


def test_price_table_divides_total_rounding_up_to_the_cent() -> None:
    rows = price_table(367500, capacity=16, trip=TRIP)
    assert [r.people for r in rows] == [9, 10, 11, 12, 13]
    assert all(r.total_cents == 367500 for r in rows)
    assert rows[0].per_person_cents == 40834  # 367500 / 9 = 40833.33 -> rounded up
    assert rows[1].per_person_cents == 36750


def test_price_table_marks_group_sizes_over_capacity_as_unavailable() -> None:
    rows = price_table(100000, capacity=11, trip=TRIP)
    assert [r.total_cents is None for r in rows] == [False, False, False, True, True]
    assert rows[3].per_person_cents is None


def test_price_table_with_unknown_capacity_allows_every_size() -> None:
    assert all(r.total_cents is not None for r in price_table(100000, capacity=None, trip=TRIP))
```

Create `tests/test_i18n.py`:

```python
from casa_certa.i18n import labels_for


def test_labels_for_exact_and_prefix_and_fallback() -> None:
    assert labels_for("pt-BR").house == "Casa"
    assert labels_for("pt").house == "Casa"
    assert labels_for("en").house == "House"
    assert labels_for("fr").house == "House"  # unknown languages fall back to English
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pricing.py tests/test_i18n.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

Create `casa_certa/pricing.py`:

```python
"""Price table: total and per-person cost for each group size."""

from pydantic import BaseModel

from casa_certa.config import TripConfig


class PriceRow(BaseModel):
    people: int
    total_cents: int | None  # None: the house cannot hold this many people
    per_person_cents: int | None


def format_brl(cents: int) -> str:
    reais, centavos = divmod(cents, 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{centavos:02d}"


def price_table(total_cents: int, capacity: int | None, trip: TripConfig) -> list[PriceRow]:
    """One row per group size. The per-person amount is rounded up so the group always covers the total."""
    rows: list[PriceRow] = []
    for people in trip.group_sizes():
        if capacity is not None and people > capacity:
            rows.append(PriceRow(people=people, total_cents=None, per_person_cents=None))
        else:
            per_person = -(-total_cents // people)
            rows.append(PriceRow(people=people, total_cents=total_cents, per_person_cents=per_person))
    return rows
```

Create `casa_certa/i18n.py`:

```python
"""Text used in the generated spreadsheet, per language."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Labels:
    comparison_sheet: str
    details_sheet: str
    house: str
    rating: str
    reviews: str
    dealbreakers: str
    total_price: str
    summary: str
    pros: str
    cons: str
    aspect: str
    status: str
    reason: str
    evidence: str
    confidence: str
    extra: str
    prices: str
    people: str
    total: str
    per_person: str
    not_available: str
    failed: str
    source_listing: str
    source_review: str
    source_extra: str


PT_BR = Labels(
    comparison_sheet="Comparativo",
    details_sheet="Detalhes",
    house="Casa",
    rating="Nota",
    reviews="Avaliações",
    dealbreakers="Eliminatórios",
    total_price="Total",
    summary="Resumo",
    pros="Pontos positivos",
    cons="Pontos negativos",
    aspect="Aspecto",
    status="Situação",
    reason="Motivo",
    evidence="Evidências",
    confidence="Confiança",
    extra="Notas extras",
    prices="Preços",
    people="Pessoas",
    total="Total",
    per_person="Por pessoa",
    not_available="n/d",
    failed="Falhou",
    source_listing="anúncio",
    source_review="avaliação",
    source_extra="nota extra",
)

EN = Labels(
    comparison_sheet="Comparison",
    details_sheet="Details",
    house="House",
    rating="Rating",
    reviews="Reviews",
    dealbreakers="Dealbreakers",
    total_price="Total",
    summary="Summary",
    pros="Pros",
    cons="Cons",
    aspect="Aspect",
    status="Status",
    reason="Reason",
    evidence="Evidence",
    confidence="Confidence",
    extra="Extra notes",
    prices="Prices",
    people="People",
    total="Total",
    per_person="Per person",
    not_available="n/a",
    failed="Failed",
    source_listing="listing",
    source_review="review",
    source_extra="extra note",
)

_LABELS = {"pt-BR": PT_BR, "en": EN}


def labels_for(language: str) -> Labels:
    """Exact match first, then by language prefix ('pt' -> 'pt-BR'), then English."""
    if language in _LABELS:
        return _LABELS[language]
    prefix = language.split("-")[0].lower()
    for key, labels in _LABELS.items():
        if key.split("-")[0].lower() == prefix:
            return labels
    return EN
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_pricing.py tests/test_i18n.py -q`
Expected: 5 passed.

- [ ] **Step 5: Run the checks and commit**

```bash
make format lint check
git add casa_certa tests
git commit -m "feat: add price table and spreadsheet labels" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: AI output schemas and the database layer

**Files:**
- Create: `casa_certa/ai/__init__.py`, `casa_certa/ai/schemas.py`, `casa_certa/db/__init__.py`, `casa_certa/db/models.py`, `casa_certa/db/session.py`, `casa_certa/db/repository.py`, `tests/conftest.py`, `alembic.ini` + `alembic/` (generated)
- Modify: `tests/fakes.py`, `tests/test_apify.py`
- Test: `tests/test_repository.py`, `tests/test_migrations.py`

- [ ] **Step 1: Add the AI schemas**

Create `casa_certa/ai/__init__.py` (empty) and `casa_certa/ai/schemas.py`:

```python
"""Structured outputs of the language models."""

from pydantic import BaseModel, Field

from casa_certa.enums import AspectStatus, EvidenceSource


class EvidenceOut(BaseModel):
    source: EvidenceSource
    quote: str = Field(description="Short verbatim quote from the listing, a guest review or the family's notes")


class VerdictOut(BaseModel):
    aspect: str = Field(description="Exactly the aspect name given in the request")
    status: AspectStatus
    reason: str
    evidence: list[EvidenceOut] = Field(default_factory=list)


class HouseAnalysis(BaseModel):
    aspects: list[VerdictOut]
    pros: list[str]
    cons: list[str]
    summary: str = Field(description="One-line verdict on the house")
    confidence_note: str | None = Field(default=None, description="Thin or old evidence, missing reviews, etc.")


class HouseName(BaseModel):
    name: str = Field(description="Short descriptive house name, 2 to 5 words")
```

- [ ] **Step 2: Extend the shared test helpers**

Replace `tests/fakes.py` with:

```python
"""Test doubles and helpers shared across test modules."""

import json
from datetime import date
from pathlib import Path
from typing import Any

from casa_certa.ai.schemas import EvidenceOut, HouseAnalysis, VerdictOut
from casa_certa.config import TripConfig
from casa_certa.enums import AspectStatus, EvidenceSource

FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name: str) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads((FIXTURES / name).read_text(encoding="utf8"))
    return data


def load_html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf8")


def make_trip(**overrides: Any) -> TripConfig:
    values: dict[str, Any] = {
        "urls": ["https://www.airbnb.com.br/rooms/46397403", "https://www.airbnb.com.br/rooms/52584351"],
        "check_in": date(2026, 12, 5),
        "check_out": date(2026, 12, 9),
        "adults": 10,
        "children": 1,
        "infants": 1,
        "aspects": [
            {"name": "Perto da praia", "kind": "must_have"},
            {"name": "Piscina", "kind": "nice_to_have"},
        ],
    }
    values.update(overrides)
    return TripConfig.model_validate(values)


def sample_analysis(aspect_names: list[str]) -> HouseAnalysis:
    return HouseAnalysis(
        aspects=[
            VerdictOut(
                aspect=name,
                status=AspectStatus.MET,
                reason="ok",
                evidence=[EvidenceOut(source=EvidenceSource.LISTING, quote="100 metros do mar")],
            )
            for name in aspect_names
        ],
        pros=["Piscina privativa"],
        cons=["Sem ar-condicionado"],
        summary="Boa casa para o grupo",
        confidence_note=None,
    )


class FakeRunner:
    """An ActorRunner that returns canned dataset items per actor id and records every call."""

    def __init__(self, outputs: dict[str, list[dict[str, Any]]]) -> None:
        self.outputs = outputs
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def run_actor(self, actor_id: str, run_input: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append((actor_id, run_input))
        return self.outputs.get(actor_id, [])

    def calls_to(self, actor_id: str) -> list[dict[str, Any]]:
        return [run_input for called, run_input in self.calls if called == actor_id]
```

In `tests/test_apify.py` delete the local `FIXTURES`, `load_json` and `make_trip` definitions and the now-unused imports (`json`, `date`, `Path`, `Any` only if unused), and import `from tests.fakes import FakeRunner, load_json, make_trip`. `make_trip(review_cap=30)` still works because overrides are merged.

- [ ] **Step 3: Write the ORM models**

Create `casa_certa/db/__init__.py` (empty) and `casa_certa/db/models.py`:

```python
"""ORM tables. Everything is a typed column; child data lives in child tables (no JSON columns)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from casa_certa.config import TripConfig
from casa_certa.enums import AspectStatus, EvidenceSource, PointKind, RuleKind


def utcnow() -> datetime:
    """Naive UTC timestamp; the database stores every datetime as naive UTC."""
    return datetime.now(UTC).replace(tzinfo=None)


def _enum_column(enum_class: type[StrEnum]) -> Enum:
    """Store enum values as short strings guarded by a check constraint."""
    return Enum(
        enum_class,
        native_enum=False,
        length=16,
        create_constraint=True,
        values_callable=lambda members: [member.value for member in members],
    )


class Base(DeclarativeBase):
    pass


class House(Base):
    __tablename__ = "house"

    id: Mapped[int] = mapped_column(primary_key=True)
    airbnb_id: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    extra: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    facts: Mapped[Facts | None] = relationship(cascade="all, delete-orphan", lazy="selectin")
    amenities: Mapped[list[Amenity]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="Amenity.id")
    rooms: Mapped[list[Room]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="Room.position")
    highlights: Mapped[list[Highlight]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="Highlight.id"
    )
    rules: Mapped[list[HouseRule]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="HouseRule.id")
    reviews: Mapped[list[Review]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="Review.id")
    quotes: Mapped[list[PriceQuote]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    analysis: Mapped[Analysis | None] = relationship(cascade="all, delete-orphan", lazy="selectin")

    def quote_for(self, trip: TripConfig) -> PriceQuote | None:
        """The stored price for exactly this trip's dates and guests, if any."""
        wanted = (trip.check_in, trip.check_out, trip.adults, trip.children, trip.infants)
        for quote in self.quotes:
            if (quote.check_in, quote.check_out, quote.adults, quote.children, quote.infants) == wanted:
                return quote
        return None


class Facts(Base):
    __tablename__ = "house_facts"

    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("house.id"), unique=True)
    title: Mapped[str | None] = mapped_column(Text)
    property_type: Mapped[str | None] = mapped_column(Text)
    person_capacity: Mapped[int | None] = mapped_column(Integer)
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    beds: Mapped[int | None] = mapped_column(Integer)
    bathrooms: Mapped[float | None] = mapped_column(Float)
    rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    location_name: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    is_guest_favorite: Mapped[bool | None] = mapped_column(Boolean)
    rating_cleanliness: Mapped[float | None] = mapped_column(Float)
    rating_accuracy: Mapped[float | None] = mapped_column(Float)
    rating_checkin: Mapped[float | None] = mapped_column(Float)
    rating_communication: Mapped[float | None] = mapped_column(Float)
    rating_location: Mapped[float | None] = mapped_column(Float)
    rating_value: Mapped[float | None] = mapped_column(Float)
    host_name: Mapped[str | None] = mapped_column(Text)
    host_is_superhost: Mapped[bool | None] = mapped_column(Boolean)
    host_rating: Mapped[float | None] = mapped_column(Float)
    host_review_count: Mapped[int | None] = mapped_column(Integer)
    host_years: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Amenity(Base):
    __tablename__ = "amenity"

    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("house.id"))
    group: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    available: Mapped[bool] = mapped_column(Boolean)
    note: Mapped[str] = mapped_column(Text, default="")


class Room(Base):
    __tablename__ = "room"

    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("house.id"))
    name: Mapped[str] = mapped_column(Text)
    beds_text: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer)


class Highlight(Base):
    __tablename__ = "highlight"

    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("house.id"))
    type: Mapped[str] = mapped_column(Text)
    headline: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)


class HouseRule(Base):
    __tablename__ = "house_rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("house.id"))
    kind: Mapped[RuleKind] = mapped_column(_enum_column(RuleKind))
    type: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")


class Review(Base):
    __tablename__ = "review"

    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("house.id"))
    airbnb_review_id: Mapped[str] = mapped_column(String(40), unique=True)
    rating: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    language: Mapped[str | None] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    host_response: Mapped[str | None] = mapped_column(Text)


class PriceQuote(Base):
    __tablename__ = "price_quote"
    __table_args__ = (UniqueConstraint("house_id", "check_in", "check_out", "adults", "children", "infants"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("house.id"))
    check_in: Mapped[date] = mapped_column(Date)
    check_out: Mapped[date] = mapped_column(Date)
    adults: Mapped[int] = mapped_column(Integer)
    children: Mapped[int] = mapped_column(Integer)
    infants: Mapped[int] = mapped_column(Integer)
    nights: Mapped[int] = mapped_column(Integer)
    total_cents: Mapped[int] = mapped_column(Integer)
    original_total_cents: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Analysis(Base):
    __tablename__ = "analysis"

    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("house.id"), unique=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    confidence_note: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    verdicts: Mapped[list[AspectVerdict]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="AspectVerdict.id"
    )
    points: Mapped[list[AnalysisPoint]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="AnalysisPoint.id"
    )


class AspectVerdict(Base):
    __tablename__ = "aspect_verdict"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analysis.id"))
    aspect_name: Mapped[str] = mapped_column(Text)
    status: Mapped[AspectStatus] = mapped_column(_enum_column(AspectStatus))
    reason: Mapped[str] = mapped_column(Text)

    evidence: Mapped[list[Evidence]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="Evidence.id"
    )


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    verdict_id: Mapped[int] = mapped_column(ForeignKey("aspect_verdict.id"))
    source: Mapped[EvidenceSource] = mapped_column(_enum_column(EvidenceSource))
    quote: Mapped[str] = mapped_column(Text)


class AnalysisPoint(Base):
    __tablename__ = "analysis_point"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analysis.id"))
    kind: Mapped[PointKind] = mapped_column(_enum_column(PointKind))
    text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
```

- [ ] **Step 4: Write the session factory**

Create `casa_certa/db/session.py`:

```python
"""Async engine and session factory."""

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def create_session_factory(url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(url)

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine, async_sessionmaker(engine, expire_on_commit=False)
```

- [ ] **Step 5: Write the shared fixtures**

Create `tests/conftest.py`:

```python
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from casa_certa.db.models import Base, House
from casa_certa.db.repository import HouseRepository
from casa_certa.db.session import create_session_factory
from casa_certa.sources.airbnb_page import ListingFacts, parse_listing_html
from casa_certa.sources.apify import parse_review_item
from tests.fakes import load_html, load_json


@pytest.fixture
def matinhos_facts() -> ListingFacts:
    return parse_listing_html(load_html("room_46397403.html"))


@pytest.fixture
def itapoa_facts() -> ListingFacts:
    return parse_listing_html(load_html("room_52584351.html"))


@pytest.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine, factory = create_session_factory(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


@pytest.fixture
async def session(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with session_factory() as db_session:
        yield db_session


@pytest.fixture
async def stored_house(session: AsyncSession, matinhos_facts: ListingFacts) -> House:
    """A house with facts, four reviews and an extra note, already committed."""
    repo = HouseRepository(session)
    house = await repo.add("46397403", "Casa do Albatroz")
    await repo.save_facts(house, matinhos_facts)
    await repo.save_reviews(house, [parse_review_item(item) for item in load_json("apify_reviews_46397403.json")])
    house.extra = "O dono confirmou que a praia fica a 3 minutos a pé."
    await session.commit()
    return house
```

- [ ] **Step 6: Write the failing repository tests**

Create `tests/test_repository.py`:

```python
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from casa_certa.db.models import Amenity, Facts, House
from casa_certa.db.repository import HouseRepository
from casa_certa.enums import AspectStatus, EvidenceSource, PointKind
from casa_certa.sources.airbnb_page import ListingFacts
from casa_certa.sources.apify import PriceData, ReviewData
from tests.fakes import make_trip, sample_analysis


async def test_add_and_get_house(session: AsyncSession) -> None:
    repo = HouseRepository(session)
    await repo.add("46397403", "Casa do Albatroz")
    await session.commit()

    house = await repo.get("46397403")
    assert house is not None
    assert house.name == "Casa do Albatroz"
    assert await repo.get("missing") is None
    assert await repo.names() == {"Casa do Albatroz"}


async def test_save_facts_creates_the_house_and_child_rows(session: AsyncSession, matinhos_facts: ListingFacts) -> None:
    repo = HouseRepository(session)
    house = await repo.add("46397403", "Casa do Albatroz")
    await repo.save_facts(house, matinhos_facts)
    await session.commit()

    loaded = await repo.get("46397403")
    assert loaded is not None
    assert loaded.facts is not None
    assert loaded.facts.person_capacity == 12
    assert loaded.facts.rating_cleanliness == 4.85
    assert loaded.facts.host_name == "Anfitriao A"
    assert any(a.name == "Ar-condicionado" and not a.available for a in loaded.amenities)
    assert len(loaded.rooms) == 5
    assert loaded.rooms[0].beds_text == "1 beliche"
    assert {rule.kind.value for rule in loaded.rules} == {"rule", "safety"}


async def test_saving_facts_again_replaces_children_and_keeps_one_facts_row(
    session: AsyncSession, matinhos_facts: ListingFacts
) -> None:
    repo = HouseRepository(session)
    house = await repo.add("46397403", "Casa do Albatroz")
    await repo.save_facts(house, matinhos_facts)
    await session.commit()

    trimmed = matinhos_facts.model_copy(update={"amenities": matinhos_facts.amenities[:3], "rating": 4.5})
    await repo.save_facts(house, trimmed)
    await session.commit()

    assert (await session.scalar(select(func.count()).select_from(Amenity))) == 3
    assert (await session.scalar(select(func.count()).select_from(Facts))) == 1
    reloaded = await repo.get("46397403")
    assert reloaded is not None
    assert reloaded.facts is not None
    assert reloaded.facts.rating == 4.5


async def test_save_reviews_upserts_by_airbnb_review_id(session: AsyncSession) -> None:
    repo = HouseRepository(session)
    house = await repo.add("1", "Casa Um")

    def review(review_id: str, text: str) -> ReviewData:
        return ReviewData(
            airbnb_review_id=review_id,
            rating=5,
            created_at=datetime(2026, 10, 1),
            language="pt",
            text=text,
            host_response=None,
        )

    await repo.save_reviews(house, [review("a", "first"), review("b", "second")])
    await repo.save_reviews(house, [review("b", "second edited"), review("c", "third")])
    await session.commit()

    loaded = await repo.get("1")
    assert loaded is not None
    assert {r.airbnb_review_id: r.text for r in loaded.reviews} == {"a": "first", "b": "second edited", "c": "third"}


async def test_save_quote_upserts_per_dates_and_guests(session: AsyncSession) -> None:
    repo = HouseRepository(session)
    house = await repo.add("1", "Casa Um")
    trip = make_trip()

    await repo.save_quote(house, trip, PriceData(total_cents=367500))
    await repo.save_quote(house, trip, PriceData(total_cents=360000, original_total_cents=367500))
    other_dates = make_trip(check_out="2026-12-10")
    await repo.save_quote(house, other_dates, PriceData(total_cents=450000))
    await session.commit()

    loaded = await repo.get("1")
    assert loaded is not None
    assert len(loaded.quotes) == 2
    quote = loaded.quote_for(trip)
    assert quote is not None
    assert (quote.total_cents, quote.original_total_cents, quote.nights) == (360000, 367500, 4)
    other = loaded.quote_for(other_dates)
    assert other is not None
    assert other.total_cents == 450000
    assert loaded.quote_for(make_trip(adults=2)) is None


async def test_save_analysis_replaces_the_previous_one(session: AsyncSession) -> None:
    repo = HouseRepository(session)
    house = await repo.add("1", "Casa Um")

    await repo.save_analysis(house, "hash-1", "model-a", sample_analysis(["Perto da praia", "Piscina"]))
    await session.commit()
    await repo.save_analysis(house, "hash-2", "model-b", sample_analysis(["Perto da praia"]))
    await session.commit()

    loaded = await repo.get("1")
    assert loaded is not None
    assert loaded.analysis is not None
    assert (loaded.analysis.input_hash, loaded.analysis.model) == ("hash-2", "model-b")
    assert [v.aspect_name for v in loaded.analysis.verdicts] == ["Perto da praia"]
    verdict = loaded.analysis.verdicts[0]
    assert verdict.status is AspectStatus.MET
    assert verdict.evidence[0].source is EvidenceSource.LISTING
    assert [(p.kind, p.text) for p in loaded.analysis.points] == [
        (PointKind.PRO, "Piscina privativa"),
        (PointKind.CON, "Sem ar-condicionado"),
    ]
    assert (await session.scalar(select(func.count()).select_from(House))) == 1
```

- [ ] **Step 7: Run the tests to verify they fail**

Run: `uv run pytest tests/test_repository.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'casa_certa.db.repository'`.

- [ ] **Step 8: Write the repository**

Create `casa_certa/db/repository.py`:

```python
"""All database reads and writes. Callers own the transaction and call `session.commit()`."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from casa_certa.ai.schemas import HouseAnalysis
from casa_certa.config import TripConfig
from casa_certa.db.models import (
    Amenity,
    Analysis,
    AnalysisPoint,
    AspectVerdict,
    Evidence,
    Facts,
    Highlight,
    House,
    HouseRule,
    PriceQuote,
    Review,
    Room,
    utcnow,
)
from casa_certa.enums import PointKind, RuleKind
from casa_certa.sources.airbnb_page import ListingFacts
from casa_certa.sources.apify import PriceData, ReviewData


def _facts_values(facts: ListingFacts) -> dict[str, Any]:
    ratings = facts.category_ratings
    return {
        "title": facts.title,
        "property_type": facts.property_type,
        "person_capacity": facts.person_capacity,
        "bedrooms": facts.bedrooms,
        "beds": facts.beds,
        "bathrooms": facts.bathrooms,
        "rating": facts.rating,
        "review_count": facts.review_count,
        "description": facts.description,
        "location_name": facts.location_name,
        "latitude": facts.latitude,
        "longitude": facts.longitude,
        "is_guest_favorite": facts.is_guest_favorite,
        "rating_cleanliness": ratings.get("cleanliness"),
        "rating_accuracy": ratings.get("accuracy"),
        "rating_checkin": ratings.get("checkin"),
        "rating_communication": ratings.get("communication"),
        "rating_location": ratings.get("location"),
        "rating_value": ratings.get("value"),
        "host_name": facts.host.name,
        "host_is_superhost": facts.host.is_superhost,
        "host_rating": facts.host.rating,
        "host_review_count": facts.host.rating_count,
        "host_years": facts.host.years_hosting,
        "fetched_at": utcnow(),
    }


class HouseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, airbnb_id: str) -> House | None:
        statement = select(House).where(House.airbnb_id == airbnb_id).execution_options(populate_existing=True)
        return await self._session.scalar(statement)

    async def all(self) -> list[House]:
        return list((await self._session.scalars(select(House).order_by(House.id))).all())

    async def names(self) -> set[str]:
        return set((await self._session.scalars(select(House.name))).all())

    async def add(self, airbnb_id: str, name: str) -> House:
        house = House(airbnb_id=airbnb_id, name=name, extra="")
        self._session.add(house)
        await self._session.flush()
        return house

    async def save_facts(self, house: House, facts: ListingFacts) -> None:
        """Replace the house's facts and every child table built from the listing page."""
        values = _facts_values(facts)
        if house.facts is None:
            house.facts = Facts(**values)
        else:
            for column, value in values.items():
                setattr(house.facts, column, value)
        house.amenities = [
            Amenity(group=a.group, name=a.name, available=a.available, note=a.note) for a in facts.amenities
        ]
        house.rooms = [
            Room(name=room.name, beds_text="; ".join(room.beds), position=position)
            for position, room in enumerate(facts.rooms)
        ]
        house.highlights = [Highlight(type=h.type, headline=h.headline, body=h.body) for h in facts.highlights]
        house.rules = [
            HouseRule(kind=RuleKind.RULE, type=r.type, title=r.title, description=r.description)
            for r in facts.house_rules
        ] + [
            HouseRule(kind=RuleKind.SAFETY, type=r.type, title=r.title, description=r.description)
            for r in facts.safety
        ]
        await self._session.flush()

    async def save_reviews(self, house: House, reviews: list[ReviewData]) -> None:
        """Insert new reviews and update known ones (matched by Airbnb review id)."""
        existing = {review.airbnb_review_id: review for review in house.reviews}
        for data in reviews:
            row = existing.get(data.airbnb_review_id)
            if row is None:
                house.reviews.append(Review(**data.model_dump()))
                continue
            row.rating = data.rating
            row.created_at = data.created_at
            row.language = data.language
            row.text = data.text
            row.host_response = data.host_response
        await self._session.flush()

    async def save_quote(self, house: House, trip: TripConfig, price: PriceData) -> None:
        quote = house.quote_for(trip)
        if quote is None:
            house.quotes.append(
                PriceQuote(
                    check_in=trip.check_in,
                    check_out=trip.check_out,
                    adults=trip.adults,
                    children=trip.children,
                    infants=trip.infants,
                    nights=trip.nights,
                    total_cents=price.total_cents,
                    original_total_cents=price.original_total_cents,
                )
            )
        else:
            quote.total_cents = price.total_cents
            quote.original_total_cents = price.original_total_cents
            quote.fetched_at = utcnow()
        await self._session.flush()

    async def save_analysis(self, house: House, input_hash: str, model: str, analysis: HouseAnalysis) -> None:
        """Replace the current analysis. The old one is deleted first because of the unique house_id."""
        if house.analysis is not None:
            await self._session.delete(house.analysis)
            await self._session.flush()
        points = [
            AnalysisPoint(kind=PointKind.PRO, text=text, position=position) for position, text in enumerate(analysis.pros)
        ] + [
            AnalysisPoint(kind=PointKind.CON, text=text, position=position) for position, text in enumerate(analysis.cons)
        ]
        house.analysis = Analysis(
            input_hash=input_hash,
            summary=analysis.summary,
            confidence_note=analysis.confidence_note,
            model=model,
            verdicts=[
                AspectVerdict(
                    aspect_name=verdict.aspect,
                    status=verdict.status,
                    reason=verdict.reason,
                    evidence=[Evidence(source=e.source, quote=e.quote) for e in verdict.evidence],
                )
                for verdict in analysis.aspects
            ],
            points=points,
        )
        await self._session.flush()
```

- [ ] **Step 9: Run the repository tests**

Run: `uv run pytest tests/test_repository.py -q`
Expected: 6 passed. If `test_save_analysis_replaces_the_previous_one` fails with a SQLAlchemy warning/error about the deleted object still attached to `house.analysis`, set `house.analysis = None` right after the `flush()` that follows `delete(...)`, then re-run.

- [ ] **Step 10: Set up Alembic**

```bash
uv run alembic init -t async alembic
```

Then edit `alembic/env.py`:
1. Below the line `config = context.config`, add:

```python
from casa_certa.config import DatabaseSettings
from casa_certa.db.models import Base

config.set_main_option("sqlalchemy.url", DatabaseSettings().database_url)
```

2. Replace `target_metadata = None` with `target_metadata = Base.metadata`.
3. In `do_run_migrations`, change the `context.configure(...)` call to include `render_as_batch=True` (SQLite needs batch mode for later `ALTER TABLE` migrations). Do the same in `run_migrations_offline`.

Confirm `script_location` in `alembic.ini` is `%(here)s/alembic` (adjust if the template wrote something else), then generate and apply the first migration against a throwaway database:

```bash
DATABASE_URL=sqlite+aiosqlite:////tmp/casa_certa_migration.db uv run alembic revision --autogenerate -m "initial schema"
DATABASE_URL=sqlite+aiosqlite:////tmp/casa_certa_migration.db uv run alembic upgrade head
rm /tmp/casa_certa_migration.db
```

Run `uv run ruff format alembic` once (generated files are not pre-formatted), then open the generated file in `alembic/versions/` and confirm it creates all 12 tables (`house`, `house_facts`, `amenity`, `room`, `highlight`, `house_rule`, `review`, `price_quote`, `analysis`, `aspect_verdict`, `evidence`, `analysis_point`) and the `Enum` check constraints.

- [ ] **Step 11: Add a migration test**

Create `tests/test_migrations.py`:

```python
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from casa_certa.db.models import Base

ROOT = Path(__file__).resolve().parent.parent


def test_migrations_create_every_orm_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "migrated.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database}")

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert set(Base.metadata.tables) <= tables
```

Run: `uv run pytest tests/test_migrations.py -q`
Expected: 1 passed.

- [ ] **Step 12: Run the checks and commit**

```bash
make format lint check
git add casa_certa tests alembic alembic.ini pyproject.toml
git commit -m "feat: add AI schemas, ORM models, repository and Alembic migrations" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Prompts and the DeepSeek agents

**Files:**
- Create: `casa_certa/ai/prompts.py`, `casa_certa/ai/agents.py`
- Test: `tests/test_prompts.py`, `tests/test_agents.py`

- [ ] **Step 1: Write the failing prompt tests**

Create `tests/test_prompts.py`:

```python
from casa_certa.ai.prompts import analysis_instructions, analysis_prompt, naming_prompt, prompt_hash
from casa_certa.db.models import House
from casa_certa.sources.airbnb_page import ListingFacts
from tests.fakes import make_trip


def test_instructions_state_the_override_rule_and_language() -> None:
    text = analysis_instructions(make_trip(language="pt-BR"))
    assert "override" in text
    assert "pt-BR" in text
    assert "9 to 13" in text


def test_analysis_prompt_contains_facts_reviews_extra_and_aspects(stored_house: House) -> None:
    prompt = analysis_prompt(stored_house, make_trip())

    assert "100 metros do mar" in prompt  # listing description
    assert "NOT available" in prompt
    assert "Ar-condicionado" in prompt
    assert "Adoramos a estadia" in prompt  # a review
    assert "3 minutos a pé" in prompt  # the family's extra note
    assert "[MUST_HAVE] Perto da praia" in prompt
    assert "[NICE_TO_HAVE] Piscina" in prompt
    assert stored_house.name not in prompt  # renaming a house must not invalidate its analysis


def test_analysis_prompt_respects_the_review_cap(stored_house: House) -> None:
    prompt = analysis_prompt(stored_house, make_trip(review_cap=1))
    assert prompt.count("stars]") == 1


def test_prompt_hash_changes_with_the_extra_note(stored_house: House) -> None:
    trip = make_trip()
    before = prompt_hash(analysis_instructions(trip), analysis_prompt(stored_house, trip))
    stored_house.extra = "Nova informação"
    after = prompt_hash(analysis_instructions(trip), analysis_prompt(stored_house, trip))
    assert before != after
    assert len(before) == 64


def test_naming_prompt_mentions_title_and_place(matinhos_facts: ListingFacts) -> None:
    prompt = naming_prompt(matinhos_facts)
    assert "Albatroz" in prompt
    assert "Matinhos" in prompt
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_prompts.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'casa_certa.ai.prompts'`.

- [ ] **Step 3: Write the prompts**

Create `casa_certa/ai/prompts.py`:

```python
"""Prompt text for the language models. Prompts are code, so they are written in English; the models are
told which language to answer in."""

import hashlib

from casa_certa.config import TripConfig
from casa_certa.db.models import House
from casa_certa.sources.airbnb_page import ListingFacts

MAX_REVIEW_CHARS = 700
MAX_DESCRIPTION_CHARS = 300


def analysis_instructions(trip: TripConfig) -> str:
    return f"""You are helping a family choose an Airbnb house for a group trip of about \
{trip.min_people} to {trip.max_people} people.

You receive the listing facts, recent guest reviews, optional notes from the family (EXTRA) and a list of aspects, \
each MUST_HAVE or NICE_TO_HAVE.

Rules:
- The listing is the baseline. Recent guest reviews can override it when they contradict or refine it (for example \
the real distance to the beach, the beach quality, or whether air conditioning really exists in every room). When a \
review contradicts the listing, say so and quote the review.
- EXTRA notes come from the family. Accept them as additional information and label them with source "extra", but \
never use them to contradict an explicit statement in the listing.
- Return exactly one verdict per requested aspect: met, partial, unmet or unknown. Use unknown when there is no \
evidence. Never guess.
- Evidence quotes must be short and verbatim from the listing, a review or the EXTRA notes. The source must be \
"listing", "review" or "extra".
- Weigh the rating together with the number and age of the reviews. Few or old reviews are weaker evidence: say so \
in confidence_note.
- Pros and cons must be concrete and relevant for a group of that size.
- Write every free-text field (reason, pros, cons, summary, confidence_note) in {trip.language}. Keep JSON keys and \
enum values in English."""


def _one_line(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def analysis_prompt(house: House, trip: TripConfig) -> str:
    """Everything the analyst may use, rendered from the stored data. The house name is left out on purpose."""
    facts = house.facts
    if facts is None:
        raise ValueError("Cannot build an analysis prompt for a house without stored facts")

    category_ratings = {
        "cleanliness": facts.rating_cleanliness,
        "accuracy": facts.rating_accuracy,
        "check-in": facts.rating_checkin,
        "communication": facts.rating_communication,
        "location": facts.rating_location,
        "value": facts.rating_value,
    }
    lines = [
        "## Listing facts",
        f"Title: {facts.title}",
        f"Type: {facts.property_type}",
        f"Capacity: {facts.person_capacity} guests, {facts.bedrooms} bedrooms, {facts.beds} beds, "
        f"{facts.bathrooms} bathrooms",
        f"Rating: {facts.rating} from {facts.review_count} reviews (guest favorite: {facts.is_guest_favorite})",
        "Category ratings: " + ", ".join(f"{name} {value}" for name, value in category_ratings.items() if value),
        f"Location: {facts.location_name}",
        f"Host: {facts.host_name}, superhost: {facts.host_is_superhost}, rating {facts.host_rating}, "
        f"{facts.host_years} years hosting",
        "",
        "## Description",
        facts.description or "(none)",
        "",
        "## Rooms",
        *[f"- {room.name}: {room.beds_text}" for room in house.rooms],
        "",
        "## Amenities available",
        ", ".join(a.name for a in house.amenities if a.available) or "(none)",
        "## Amenities NOT available",
        ", ".join(a.name for a in house.amenities if not a.available) or "(none)",
        "",
        "## House rules and safety",
        *[f"- {rule.title} {rule.description}".strip() for rule in house.rules],
        "",
        "## Highlights",
        *[f"- {h.headline}: {h.body}" for h in house.highlights],
        "",
    ]

    reviews = sorted(house.reviews, key=lambda review: review.created_at, reverse=True)[: trip.review_cap]
    lines.append(f"## Guest reviews (most recent {len(reviews)} of {facts.review_count} total)")
    for review in reviews:
        lines.append(
            f"- [{review.created_at:%Y-%m-%d}, {review.rating} stars] {_one_line(review.text, MAX_REVIEW_CHARS)}"
        )
    lines += ["", "## EXTRA notes from the family", house.extra.strip() or "(none)", "", "## Aspects to judge"]
    for aspect in trip.aspects:
        detail = f": {aspect.details}" if aspect.details else ""
        lines.append(f"- [{aspect.kind.name}] {aspect.name}{detail}")
    return "\n".join(lines)


def prompt_hash(instructions: str, prompt: str) -> str:
    """Identifies the exact analysis input. A different hash means the analysis must be redone."""
    return hashlib.sha256(f"{instructions}\n\x00\n{prompt}".encode()).hexdigest()


def naming_instructions(language: str) -> str:
    return (
        f"Create a short, descriptive, memorable name (2 to 5 words) in {language} for the house below, based on "
        "its most distinctive feature or neighbourhood, for example 'Casa da Piscina' or 'Sobrado Vista Mar'. "
        "Do not include the city unless it is the distinctive part. Do not use quotes or emoji."
    )


def naming_prompt(facts: ListingFacts) -> str:
    amenities = ", ".join(facts.available_amenities()[:15])
    return "\n".join(
        [
            f"Title: {facts.title}",
            f"Type: {facts.property_type}",
            f"Place: {facts.location_name}",
            f"Main amenities: {amenities}",
            f"Description: {_one_line(facts.description or '', MAX_DESCRIPTION_CHARS)}",
        ]
    )
```

- [ ] **Step 4: Run the prompt tests**

Run: `uv run pytest tests/test_prompts.py -q`
Expected: 5 passed.

- [ ] **Step 5: Write the failing agent tests**

Create `tests/test_agents.py`:

```python
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from casa_certa.ai.agents import HouseAnalyst, HouseNamer, make_deepseek_model, make_unique_name
from casa_certa.sources.airbnb_page import ListingFacts
from tests.fakes import sample_analysis


def reply_with(text: str, seen: list[list[ModelMessage]]) -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(respond)


def test_make_unique_name() -> None:
    assert make_unique_name("Casa da Piscina", set()) == "Casa da Piscina"
    assert make_unique_name("Casa da Piscina", {"Casa da Piscina"}) == "Casa da Piscina 2"
    assert make_unique_name("Casa da Piscina", {"casa da piscina", "Casa da Piscina 2"}) == "Casa da Piscina 3"


async def test_namer_returns_a_trimmed_name_and_sends_facts_and_language(matinhos_facts: ListingFacts) -> None:
    seen: list[list[ModelMessage]] = []
    namer = HouseNamer(reply_with('{"name": "  Casa da Piscina "}', seen))

    name = await namer.name(matinhos_facts, "pt-BR")

    assert name == "Casa da Piscina"
    sent = str(seen[0])
    assert "Albatroz" in sent
    assert "pt-BR" in sent


async def test_analyst_parses_structured_output() -> None:
    expected = sample_analysis(["Perto da praia", "Piscina"])
    seen: list[list[ModelMessage]] = []
    model = reply_with(expected.model_dump_json(), seen)
    analyst = HouseAnalyst(model)

    result = await analyst.analyze("PROMPT TEXT", "INSTRUCTION TEXT")

    assert result == expected
    assert "PROMPT TEXT" in str(seen[0])
    assert "INSTRUCTION TEXT" in str(seen[0])
    assert analyst.model_name == model.model_name


def test_make_deepseek_model_uses_the_requested_model_name() -> None:
    assert make_deepseek_model("key", "deepseek-chat").model_name == "deepseek-chat"
```

- [ ] **Step 6: Run the tests to verify they fail**

Run: `uv run pytest tests/test_agents.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'casa_certa.ai.agents'`.

- [ ] **Step 7: Write the agents**

Create `casa_certa/ai/agents.py`:

```python
"""DeepSeek agents built on Pydantic AI: a namer and an analyst. Each takes its model, so tiers are a setting."""

from pydantic_ai import Agent, PromptedOutput
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider

from casa_certa.ai.prompts import naming_instructions, naming_prompt
from casa_certa.ai.schemas import HouseAnalysis, HouseName
from casa_certa.sources.airbnb_page import ListingFacts


def make_deepseek_model(api_key: str, model_name: str) -> OpenAIChatModel:
    return OpenAIChatModel(model_name, provider=DeepSeekProvider(api_key=api_key))


def make_unique_name(name: str, taken: set[str]) -> str:
    """'Casa da Piscina' -> 'Casa da Piscina 2' (3, 4...) when the name is already used (case-insensitive)."""
    used = {existing.casefold() for existing in taken}
    if name.casefold() not in used:
        return name
    number = 2
    while f"{name} {number}".casefold() in used:
        number += 1
    return f"{name} {number}"


class HouseNamer:
    """Cheap model: suggests a short descriptive name for a new house."""

    def __init__(self, model: Model) -> None:
        self._agent: Agent[None, HouseName] = Agent(model, output_type=PromptedOutput(HouseName))

    async def name(self, facts: ListingFacts, language: str) -> str:
        result = await self._agent.run(naming_prompt(facts), instructions=naming_instructions(language))
        return result.output.name.strip()


class HouseAnalyst:
    """Judges a house against the aspects using the listing, reviews and notes. On the cheap model today."""

    def __init__(self, model: Model) -> None:
        self._agent: Agent[None, HouseAnalysis] = Agent(model, output_type=PromptedOutput(HouseAnalysis))
        self.model_name = model.model_name

    async def analyze(self, prompt: str, instructions: str) -> HouseAnalysis:
        result = await self._agent.run(prompt, instructions=instructions)
        return result.output
```

- [ ] **Step 8: Run the agent tests**

Run: `uv run pytest tests/test_agents.py -q`
Expected: 4 passed.

- [ ] **Step 9: Run the checks and commit**

```bash
make format lint check
git add casa_certa tests
git commit -m "feat: add prompts and DeepSeek namer/analyst agents" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The pipeline (cache-first orchestration)

**Files:**
- Modify: `casa_certa/db/repository.py` (add `commit` and `rollback`)
- Create: `casa_certa/pipeline.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Let the repository own the transaction calls**

Append to `HouseRepository` in `casa_certa/db/repository.py`:

```python
    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_pipeline.py`:

```python
from dataclasses import dataclass, field

import httpx
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from casa_certa.ai.agents import HouseAnalyst, HouseNamer
from casa_certa.config import TripConfig
from casa_certa.db.repository import HouseRepository
from casa_certa.pipeline import Pipeline
from casa_certa.sources.apify import PRICE_ACTOR, REVIEWS_ACTOR, AirbnbApify
from tests.fakes import FakeRunner, load_html, load_json, make_trip, sample_analysis

PAGES = {"46397403": "room_46397403.html", "52584351": "room_52584351.html"}


@dataclass
class World:
    """The pipeline plus every fake it talks to, so tests can count what was fetched."""

    pipeline: Pipeline
    runner: FakeRunner
    page_requests: list[str] = field(default_factory=list)
    namer_calls: list[str] = field(default_factory=list)
    analyst_calls: list[str] = field(default_factory=list)


async def no_sleep(seconds: float) -> None:
    return None


def make_world(
    session_factory: async_sessionmaker[AsyncSession], *, blocked: frozenset[str] = frozenset(), analyst_fails: bool = False
) -> World:
    world_runner = FakeRunner(
        {
            PRICE_ACTOR: load_json("apify_rooms_46397403.json") + load_json("apify_rooms_52584351.json"),
            REVIEWS_ACTOR: load_json("apify_reviews_46397403.json"),
        }
    )
    page_requests: list[str] = []
    namer_calls: list[str] = []
    analyst_calls: list[str] = []

    def serve_page(request: httpx.Request) -> httpx.Response:
        room_id = request.url.path.rsplit("/", 1)[-1]
        page_requests.append(room_id)
        if room_id in blocked:
            return httpx.Response(403)
        return httpx.Response(200, text=load_html(PAGES[room_id]))

    def name_house(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        namer_calls.append(str(messages))
        name = "Casa do Albatroz" if "Albatroz" in str(messages) else "Casa do Campo"
        return ModelResponse(parts=[TextPart(f'{{"name": "{name}"}}')])

    def analyse_house(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        analyst_calls.append(str(messages))
        if analyst_fails:
            raise UnexpectedModelBehavior("the model returned garbage")
        return ModelResponse(parts=[TextPart(sample_analysis(["Perto da praia", "Piscina"]).model_dump_json())])

    pipeline = Pipeline(
        session_factory=session_factory,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(serve_page)),
        apify=AirbnbApify(world_runner),
        namer=HouseNamer(FunctionModel(name_house)),
        analyst=HouseAnalyst(FunctionModel(analyse_house)),
        page_delay=(0, 0),
        sleep=no_sleep,
    )
    return World(pipeline, world_runner, page_requests, namer_calls, analyst_calls)


async def test_plan_before_the_first_run_lists_everything_and_estimates_cost(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory)
    plan = await world.pipeline.plan(make_trip())

    assert plan.fetch_facts == plan.fetch_reviews == plan.fetch_prices == ["46397403", "52584351"]
    assert round(plan.estimated_cost_usd, 3) == round(2 * 30 * 0.005 + 2 * 0.004, 3)
    assert world.page_requests == []  # planning never touches the network


async def test_first_run_creates_named_houses_with_prices_reviews_and_analysis(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory)
    trip: TripConfig = make_trip()

    report = await world.pipeline.run(trip)

    assert report.failures == {}
    assert [h.name for h in report.houses] == ["Casa do Albatroz", "Casa do Campo"]
    albatroz = report.houses[0]
    assert albatroz.facts is not None
    assert albatroz.facts.person_capacity == 12
    assert len(albatroz.reviews) == 4
    quote = albatroz.quote_for(trip)
    assert quote is not None
    assert quote.total_cents == 367500
    assert albatroz.analysis is not None
    assert albatroz.analysis.input_hash
    assert len(world.runner.calls_to(PRICE_ACTOR)) == 1  # one actor run for both houses
    assert len(world.runner.calls_to(REVIEWS_ACTOR)) == 1
    assert world.page_requests == ["46397403", "52584351"]


async def test_second_run_fetches_and_analyses_nothing(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory)
    trip = make_trip()
    await world.pipeline.run(trip)
    calls_before = (len(world.runner.calls), len(world.page_requests), len(world.namer_calls), len(world.analyst_calls))

    report = await world.pipeline.run(trip)

    assert (len(world.runner.calls), len(world.page_requests), len(world.namer_calls), len(world.analyst_calls)) == (
        calls_before
    )
    assert [h.name for h in report.houses] == ["Casa do Albatroz", "Casa do Campo"]
    plan = await world.pipeline.plan(trip)
    assert plan.fetch_facts == plan.fetch_reviews == plan.fetch_prices == []


async def test_refresh_fetches_everything_again_but_keeps_names(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory)
    trip = make_trip()
    await world.pipeline.run(trip)

    report = await world.pipeline.run(trip, refresh=True)

    assert len(world.runner.calls_to(PRICE_ACTOR)) == 2
    assert len(world.runner.calls_to(REVIEWS_ACTOR)) == 2
    assert world.page_requests == ["46397403", "52584351"] * 2
    assert len(world.namer_calls) == 2  # existing houses are never renamed
    assert [h.name for h in report.houses] == ["Casa do Albatroz", "Casa do Campo"]


async def test_new_dates_only_fetch_prices(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory)
    await world.pipeline.run(make_trip())

    report = await world.pipeline.run(make_trip(check_in="2027-01-10", check_out="2027-01-14"))

    assert len(world.runner.calls_to(PRICE_ACTOR)) == 2
    assert len(world.runner.calls_to(REVIEWS_ACTOR)) == 1
    assert len(world.page_requests) == 2
    assert all(len(h.quotes) == 2 for h in report.houses)


async def test_changing_the_extra_note_reanalyses_only_that_house(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory)
    trip = make_trip()
    await world.pipeline.run(trip)
    async with session_factory() as session:
        repo = HouseRepository(session)
        house = await repo.get("46397403")
        assert house is not None
        house.extra = "Vimos nas fotos que a praia é a 2 minutos."
        await session.commit()
    analyst_calls_before = len(world.analyst_calls)

    await world.pipeline.run(trip)

    assert len(world.analyst_calls) == analyst_calls_before + 1


async def test_one_blocked_house_does_not_stop_the_run(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory, blocked=frozenset({"46397403"}))

    report = await world.pipeline.run(make_trip())

    assert list(report.failures) == ["46397403"]
    assert "HTTP 403" in report.failures["46397403"]
    assert [h.name for h in report.houses] == ["Casa do Campo"]


async def test_failed_analysis_keeps_the_fetched_data(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory, analyst_fails=True)

    report = await world.pipeline.run(make_trip())

    assert set(report.failures) == {"46397403", "52584351"}
    async with session_factory() as session:
        house = await HouseRepository(session).get("46397403")
    assert house is not None
    assert house.facts is not None
    assert len(house.reviews) == 4
    assert house.analysis is None
    # the next run must not pay for the data again
    plan = await world.pipeline.plan(make_trip())
    assert plan.fetch_facts == plan.fetch_reviews == plan.fetch_prices == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'casa_certa.pipeline'`.

- [ ] **Step 4: Write the pipeline**

Create `casa_certa/pipeline.py`:

```python
"""Orchestration: decide what to fetch (cache-first), fetch it, persist it, analyze it."""

import asyncio
from dataclasses import dataclass, field

import httpx
from pydantic_ai.exceptions import AgentRunError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from casa_certa.ai.agents import HouseAnalyst, HouseNamer, make_unique_name
from casa_certa.ai.prompts import analysis_instructions, analysis_prompt, prompt_hash
from casa_certa.config import TripConfig
from casa_certa.db.models import House
from casa_certa.db.repository import HouseRepository
from casa_certa.sources.airbnb_page import (
    AirbnbPageError,
    ListingFacts,
    PageFetchError,
    Sleep,
    fetch_listings,
    room_id_from_url,
)
from casa_certa.sources.apify import (
    PRICE_COST_USD,
    REVIEW_COST_USD,
    AirbnbApify,
    ApifyError,
    PriceData,
    ReviewData,
)

# Errors that fail one house but not the whole run.
HOUSE_ERRORS = (AirbnbPageError, ApifyError, AgentRunError)


@dataclass(frozen=True)
class RunPlan:
    """What a run would fetch. Houses already stored are not listed unless `refresh` was requested."""

    room_ids: list[str]
    fetch_facts: list[str]
    fetch_reviews: list[str]
    fetch_prices: list[str]
    review_cap: int

    @property
    def estimated_cost_usd(self) -> float:
        return len(self.fetch_reviews) * self.review_cap * REVIEW_COST_USD + len(self.fetch_prices) * PRICE_COST_USD

    @property
    def is_empty(self) -> bool:
        return not (self.fetch_facts or self.fetch_reviews or self.fetch_prices)


@dataclass
class RunReport:
    plan: RunPlan
    houses: list[House]
    failures: dict[str, str]
    warnings: dict[str, list[str]] = field(default_factory=dict)


class Pipeline:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        http_client: httpx.AsyncClient,
        apify: AirbnbApify,
        namer: HouseNamer,
        analyst: HouseAnalyst,
        page_delay: tuple[float, float] = (2.0, 5.0),
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._session_factory = session_factory
        self._http = http_client
        self._apify = apify
        self._namer = namer
        self._analyst = analyst
        self._page_delay = page_delay
        self._sleep = sleep

    async def plan(self, trip: TripConfig, *, refresh: bool = False) -> RunPlan:
        """Decide what is missing. Reads the database only; never touches the network."""
        room_ids = list(dict.fromkeys(room_id_from_url(url) for url in trip.urls))
        fetch_facts: list[str] = []
        fetch_prices: list[str] = []
        async with self._session_factory() as session:
            repo = HouseRepository(session)
            for room_id in room_ids:
                house = await repo.get(room_id)
                if house is None or refresh:
                    fetch_facts.append(room_id)
                if house is None or refresh or house.quote_for(trip) is None:
                    fetch_prices.append(room_id)
        return RunPlan(
            room_ids=room_ids,
            fetch_facts=fetch_facts,
            fetch_reviews=list(fetch_facts),  # reviews are fetched exactly when the facts are
            fetch_prices=fetch_prices,
            review_cap=trip.review_cap,
        )

    async def run(self, trip: TripConfig, *, refresh: bool = False) -> RunReport:
        plan = await self.plan(trip, refresh=refresh)
        pages: dict[str, ListingFacts | AirbnbPageError] = {}
        if plan.fetch_facts:
            pages = await fetch_listings(plan.fetch_facts, client=self._http, delay=self._page_delay, sleep=self._sleep)
        reviews = await self._fetch_reviews(plan, trip)
        prices = await self._fetch_prices(plan, trip)

        failures: dict[str, str] = {}
        warnings: dict[str, list[str]] = {}
        houses: list[House] = []
        async with self._session_factory() as session:
            repo = HouseRepository(session)
            for room_id in plan.room_ids:
                try:
                    messages = await self._process(
                        repo, trip, room_id, pages.get(room_id), reviews.get(room_id), prices.get(room_id)
                    )
                except HOUSE_ERRORS as error:
                    await repo.rollback()
                    failures[room_id] = str(error)
                    continue
                if messages:
                    warnings[room_id] = messages
            for room_id in plan.room_ids:
                house = await repo.get(room_id)
                if room_id not in failures and house is not None:
                    houses.append(house)
        return RunReport(plan=plan, houses=houses, failures=failures, warnings=warnings)

    async def _fetch_reviews(self, plan: RunPlan, trip: TripConfig) -> dict[str, list[ReviewData] | ApifyError]:
        if not plan.fetch_reviews:
            return {}
        try:
            fetched = await self._apify.fetch_reviews(plan.fetch_reviews, trip)
        except ApifyError as error:
            return {room_id: error for room_id in plan.fetch_reviews}
        return {room_id: items for room_id, items in fetched.items()}

    async def _fetch_prices(self, plan: RunPlan, trip: TripConfig) -> dict[str, PriceData | ApifyError]:
        if not plan.fetch_prices:
            return {}
        try:
            return await self._apify.fetch_prices(plan.fetch_prices, trip)
        except ApifyError as error:
            return {room_id: error for room_id in plan.fetch_prices}

    async def _process(
        self,
        repo: HouseRepository,
        trip: TripConfig,
        room_id: str,
        page: ListingFacts | AirbnbPageError | None,
        reviews: list[ReviewData] | ApifyError | None,
        price: PriceData | ApifyError | None,
    ) -> list[str]:
        """Store what was fetched for one house, then analyze it. Returns non-fatal warnings."""
        warnings: list[str] = []
        house = await repo.get(room_id)

        if isinstance(page, AirbnbPageError):
            if house is None:
                raise page
            warnings.append(f"could not refresh the listing facts: {page}")
        elif page is not None:
            if house is None:
                suggested = await self._namer.name(page, trip.language)
                house = await repo.add(room_id, make_unique_name(suggested, await repo.names()))
            await repo.save_facts(house, page)
        if house is None:  # the plan always fetches facts for unknown houses, so this is a bug guard
            raise PageFetchError(f"No stored or fetched data for listing {room_id}")

        if isinstance(reviews, ApifyError):
            warnings.append(f"could not fetch reviews: {reviews}")
        elif reviews is not None:
            await repo.save_reviews(house, reviews)
        if isinstance(price, ApifyError):
            warnings.append(f"could not fetch the price: {price}")
        elif price is not None:
            await repo.save_quote(house, trip, price)
        await repo.commit()  # keep paid data even if the analysis below fails

        instructions = analysis_instructions(trip)
        prompt = analysis_prompt(house, trip)
        digest = prompt_hash(instructions, prompt)
        if house.analysis is None or house.analysis.input_hash != digest:
            analysis = await self._analyst.analyze(prompt, instructions)
            await repo.save_analysis(house, digest, self._analyst.model_name, analysis)
            await repo.commit()
        return warnings
```

- [ ] **Step 5: Run the pipeline tests**

Run: `uv run pytest tests/test_pipeline.py -q`
Expected: 8 passed. If a test fails only because `str(messages)` in the fake namer does not contain "Albatroz", print `str(messages)[:500]` inside `name_house` once to see the real shape and fix the test fake, not the pipeline.

- [ ] **Step 6: Run the checks and commit**

```bash
make format lint check
git add casa_certa tests
git commit -m "feat: add the cache-first pipeline" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Spreadsheet export

**Files:**
- Create: `casa_certa/export/__init__.py`, `casa_certa/export/xlsx.py`
- Test: `tests/test_xlsx.py`

- [ ] **Step 1: Add the openpyxl type stubs**

Run: `uv add --dev types-openpyxl`
Expected: the package is added to the `dev` dependency group.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_xlsx.py`:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from casa_certa.db.models import House
from casa_certa.db.repository import HouseRepository
from casa_certa.enums import AspectStatus
from casa_certa.export.xlsx import build_workbook
from casa_certa.sources.apify import PriceData
from tests.fakes import make_trip, sample_analysis


async def analysed_house(session: AsyncSession, house: House) -> House:
    repo = HouseRepository(session)
    await repo.save_quote(house, make_trip(), PriceData(total_cents=367500))
    await repo.save_analysis(house, "hash", "test-model", sample_analysis(["Perto da praia", "Piscina"]))
    await session.commit()
    return house


def all_cell_values(sheet: object) -> list[str]:
    return [str(cell.value) for row in sheet.iter_rows() for cell in row if cell.value is not None]  # type: ignore[attr-defined]


async def test_comparison_sheet_has_one_row_per_house(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)

    workbook = build_workbook([house], {}, make_trip())

    assert workbook.sheetnames == ["Comparativo", "Detalhes"]
    sheet = workbook["Comparativo"]
    header = [cell.value for cell in sheet[1]]
    assert header == ["Casa", "Nota", "Avaliações", "★ Perto da praia", "Piscina", "Eliminatórios", "Total", "Resumo"]
    row = [cell.value for cell in sheet[2]]
    assert row == ["Casa do Albatroz", 4.95, 87, "✅", "✅", None, "R$ 3.675,00", "Boa casa para o grupo"]
    assert sheet["A2"].hyperlink.target == "https://www.airbnb.com.br/rooms/46397403"  # type: ignore[union-attr]


async def test_dealbreaker_column_lists_unmet_must_haves(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)
    assert house.analysis is not None
    house.analysis.verdicts[0].status = AspectStatus.UNMET

    sheet = build_workbook([house], {}, make_trip())["Comparativo"]

    assert sheet["D2"].value == "❌"
    assert sheet["F2"].value == "Perto da praia"


async def test_missing_analysis_and_price_render_as_unknown_and_not_available(
    session: AsyncSession, stored_house: House
) -> None:
    sheet = build_workbook([stored_house], {}, make_trip())["Comparativo"]
    row = [cell.value for cell in sheet[2]]
    assert row[3:5] == ["❓", "❓"]
    assert row[6] == "n/d"


async def test_failures_are_listed_after_the_houses(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)
    sheet = build_workbook([house], {"999": "HTTP 403 while fetching"}, make_trip())["Comparativo"]
    assert sheet["A3"].value == "999 — Falhou: HTTP 403 while fetching"


async def test_details_sheet_has_points_evidence_and_price_table(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)

    values = all_cell_values(build_workbook([house], {}, make_trip())["Detalhes"])

    assert "Piscina privativa" in values  # pro
    assert "Sem ar-condicionado" in values  # con
    assert any("100 metros do mar" in value for value in values)  # evidence quote
    assert "O dono confirmou que a praia fica a 3 minutos a pé." in values  # extra note
    assert "R$ 3.675,00" in values
    assert "R$ 408,34" in values  # 367500 / 9 rounded up
    assert "n/d" in values  # 13 people exceed this house's capacity of 12
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_xlsx.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'casa_certa.export'`.

- [ ] **Step 4: Write the exporter**

Create `casa_certa/export/__init__.py` (empty) and `casa_certa/export/xlsx.py`:

```python
"""Spreadsheet export: a compact comparison sheet and a details sheet."""

from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from casa_certa.config import AspectKind, TripConfig
from casa_certa.db.models import House
from casa_certa.enums import AspectStatus, EvidenceSource, PointKind
from casa_certa.i18n import Labels, labels_for
from casa_certa.pricing import format_brl, price_table
from casa_certa.sources.airbnb_page import listing_url

SYMBOLS = {
    AspectStatus.MET: "✅",
    AspectStatus.PARTIAL: "⚠️",
    AspectStatus.UNMET: "❌",
    AspectStatus.UNKNOWN: "❓",
}
BOLD = Font(bold=True)
WRAP = Alignment(wrap_text=True, vertical="top")


def build_workbook(houses: list[House], failures: dict[str, str], trip: TripConfig) -> Workbook:
    labels = labels_for(trip.language)
    workbook = Workbook()
    comparison = workbook.worksheets[0]
    comparison.title = labels.comparison_sheet
    _write_comparison(comparison, houses, failures, trip, labels)
    details = workbook.create_sheet(labels.details_sheet)
    _write_details(details, houses, trip, labels)
    return workbook


def _append(sheet: Worksheet, values: list[Any], *, bold: bool = False) -> int:
    sheet.append(values)
    row = sheet.max_row
    for cell in sheet[row]:
        cell.alignment = WRAP
        if bold:
            cell.font = BOLD
    return row


def _status(house: House, aspect_name: str) -> AspectStatus:
    if house.analysis is not None:
        for verdict in house.analysis.verdicts:
            if verdict.aspect_name == aspect_name:
                return verdict.status
    return AspectStatus.UNKNOWN


def _dealbreakers(house: House, trip: TripConfig) -> list[str]:
    return [
        aspect.name
        for aspect in trip.aspects
        if aspect.kind is AspectKind.MUST_HAVE and _status(house, aspect.name) is AspectStatus.UNMET
    ]


def _total_text(house: House, trip: TripConfig, labels: Labels) -> str:
    quote = house.quote_for(trip)
    return format_brl(quote.total_cents) if quote else labels.not_available


def _write_comparison(
    sheet: Worksheet, houses: list[House], failures: dict[str, str], trip: TripConfig, labels: Labels
) -> None:
    aspect_headers = [f"★ {a.name}" if a.kind is AspectKind.MUST_HAVE else a.name for a in trip.aspects]
    _append(
        sheet,
        [labels.house, labels.rating, labels.reviews, *aspect_headers, labels.dealbreakers, labels.total_price]
        + [labels.summary],
        bold=True,
    )
    for house in houses:
        facts = house.facts
        row = _append(
            sheet,
            [
                house.name,
                facts.rating if facts else None,
                facts.review_count if facts else None,
                *[SYMBOLS[_status(house, aspect.name)] for aspect in trip.aspects],
                ", ".join(_dealbreakers(house, trip)) or None,
                _total_text(house, trip, labels),
                house.analysis.summary if house.analysis else None,
            ],
        )
        sheet.cell(row=row, column=1).hyperlink = listing_url(house.airbnb_id)
    for room_id, message in failures.items():
        _append(sheet, [f"{room_id} — {labels.failed}: {message}"])
    sheet.column_dimensions["A"].width = 30
    sheet.column_dimensions[get_column_letter(sheet.max_column)].width = 60  # the summary column
    sheet.freeze_panes = "B2"


def _source_label(source: EvidenceSource, labels: Labels) -> str:
    return {
        EvidenceSource.LISTING: labels.source_listing,
        EvidenceSource.REVIEW: labels.source_review,
        EvidenceSource.EXTRA: labels.source_extra,
    }[source]


def _write_details(sheet: Worksheet, houses: list[House], trip: TripConfig, labels: Labels) -> None:
    for house in houses:
        facts = house.facts
        analysis = house.analysis
        title_row = _append(sheet, [house.name], bold=True)
        sheet.cell(row=title_row, column=1).hyperlink = listing_url(house.airbnb_id)
        _append(sheet, [labels.rating, facts.rating if facts else None, labels.reviews, facts.review_count if facts else None])
        if analysis:
            _append(sheet, [labels.summary, analysis.summary])
            if analysis.confidence_note:
                _append(sheet, [labels.confidence, analysis.confidence_note])
        if house.extra.strip():
            _append(sheet, [labels.extra, house.extra.strip()])

        if analysis:
            _append(sheet, [labels.pros], bold=True)
            for point in (p for p in analysis.points if p.kind is PointKind.PRO):
                _append(sheet, [point.text])
            _append(sheet, [labels.cons], bold=True)
            for point in (p for p in analysis.points if p.kind is PointKind.CON):
                _append(sheet, [point.text])
            _append(sheet, [labels.aspect, labels.status, labels.reason, labels.evidence], bold=True)
            for verdict in analysis.verdicts:
                evidence = "\n".join(
                    f'[{_source_label(e.source, labels)}] "{e.quote}"' for e in verdict.evidence
                )
                _append(sheet, [verdict.aspect_name, SYMBOLS[verdict.status], verdict.reason, evidence])

        quote = house.quote_for(trip)
        if quote and facts:
            _append(sheet, [labels.prices], bold=True)
            _append(sheet, [labels.people, labels.total, labels.per_person], bold=True)
            for row in price_table(quote.total_cents, facts.person_capacity, trip):
                _append(
                    sheet,
                    [
                        row.people,
                        format_brl(row.total_cents) if row.total_cents is not None else labels.not_available,
                        format_brl(row.per_person_cents) if row.per_person_cents is not None else labels.not_available,
                    ],
                )
        sheet.append([])
        sheet.append([])
    for column, width in zip("ABCD", (34, 16, 50, 60), strict=True):
        sheet.column_dimensions[column].width = width
```

- [ ] **Step 5: Run the exporter tests**

Run: `uv run pytest tests/test_xlsx.py -q`
Expected: 5 passed. The `# type: ignore[...]` comments in this test file exist only to keep mypy quiet about openpyxl's loose types; if mypy reports one as unused, delete that comment.

- [ ] **Step 6: Run the checks and commit**

```bash
make format lint check
git add casa_certa tests pyproject.toml uv.lock
git commit -m "feat: add the spreadsheet exporter" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: CLI, README and a live smoke test

**Files:**
- Create: `casa_certa/cli.py`, `README.md`, `tests/test_cli.py`, `tests/test_live.py`
- Modify: `pyproject.toml` (script entry point)

- [ ] **Step 1: Write the failing CLI tests**

Create `tests/test_cli.py`:

```python
from pathlib import Path

from casa_certa.cli import build_parser, format_plan
from casa_certa.pipeline import RunPlan


def test_parser_reads_run_options() -> None:
    args = build_parser().parse_args(["run", "trip.yaml", "--refresh", "--dry-run", "--yes", "--output", "out.xlsx"])
    assert args.trip_file == Path("trip.yaml")
    assert (args.refresh, args.dry_run, args.yes) == (True, True, True)
    assert args.output == Path("out.xlsx")


def test_parser_defaults_are_safe() -> None:
    args = build_parser().parse_args(["run", "trip.yaml"])
    assert (args.refresh, args.dry_run, args.yes) == (False, False, False)


def test_format_plan_shows_counts_and_cost() -> None:
    plan = RunPlan(
        room_ids=["1", "2", "3"], fetch_facts=["3"], fetch_reviews=["3"], fetch_prices=["2", "3"], review_cap=30
    )
    text = format_plan(plan)
    assert "3 houses" in text
    assert "listing pages to fetch (free): 1" in text
    assert "reviews to fetch: 1 houses, up to 30 each" in text
    assert "prices to fetch: 2" in text
    assert "$0.16" in text  # 1 * 30 * 0.005 + 2 * 0.004 = 0.158


def test_format_plan_when_everything_is_cached() -> None:
    plan = RunPlan(room_ids=["1"], fetch_facts=[], fetch_reviews=[], fetch_prices=[], review_cap=30)
    assert "Nothing to fetch" in format_plan(plan)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'casa_certa.cli'`.

- [ ] **Step 3: Write the CLI**

Create `casa_certa/cli.py`:

```python
"""Command line entry point: `casa-certa run trip.yaml`."""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from alembic import command
from alembic.config import Config
from apify_client import ApifyClientAsync

from casa_certa.ai.agents import HouseAnalyst, HouseNamer, make_deepseek_model
from casa_certa.config import Settings
from casa_certa.config_sources.base import ConfigError
from casa_certa.config_sources.yaml_source import YamlConfigSource
from casa_certa.db.session import create_session_factory
from casa_certa.export.xlsx import build_workbook
from casa_certa.pipeline import Pipeline, RunPlan
from casa_certa.sources.airbnb_page import make_client
from casa_certa.sources.apify import AirbnbApify, ApifyActorRunner

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="casa-certa", description="Compare Airbnb houses for a family trip.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Analyze the houses in a trip file and write a spreadsheet")
    run.add_argument("trip_file", type=Path)
    run.add_argument("--refresh", action="store_true", help="Re-fetch everything from Airbnb (costs money)")
    run.add_argument("--dry-run", action="store_true", help="Only print what would be fetched")
    run.add_argument("--yes", action="store_true", help="Do not ask for confirmation before spending")
    run.add_argument("--output", type=Path, default=None, help="Output .xlsx path")
    return parser


def format_plan(plan: RunPlan) -> str:
    lines = [f"Trip has {len(plan.room_ids)} houses."]
    if plan.is_empty:
        lines.append("Nothing to fetch: everything is already stored.")
        return "\n".join(lines)
    lines += [
        f"- listing pages to fetch (free): {len(plan.fetch_facts)}",
        f"- reviews to fetch: {len(plan.fetch_reviews)} houses, up to {plan.review_cap} each",
        f"- prices to fetch: {len(plan.fetch_prices)}",
        f"Estimated Apify cost: ~${plan.estimated_cost_usd:.2f}",
    ]
    return "\n".join(lines)


def run_migrations() -> None:
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")


async def _confirm() -> bool:
    answer = await asyncio.to_thread(input, "Continue? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()  # type: ignore[call-arg]  # values come from the environment / .env
    trip = await YamlConfigSource(args.trip_file).load()
    await asyncio.to_thread(run_migrations)

    engine, session_factory = create_session_factory(settings.database_url)
    apify_client = ApifyClientAsync(settings.apify_token.get_secret_value())
    api_key = settings.deepseek_api_key.get_secret_value()
    try:
        async with make_client() as http_client:
            pipeline = Pipeline(
                session_factory=session_factory,
                http_client=http_client,
                apify=AirbnbApify(ApifyActorRunner(apify_client)),
                namer=HouseNamer(make_deepseek_model(api_key, settings.cheap_model)),
                analyst=HouseAnalyst(make_deepseek_model(api_key, settings.analysis_model)),
                page_delay=(settings.page_delay_min, settings.page_delay_max),
            )
            plan = await pipeline.plan(trip, refresh=args.refresh)
            print(format_plan(plan))
            if args.dry_run:
                return 0
            if not plan.is_empty and not args.yes and not await _confirm():
                print("Cancelled.")
                return 1
            report = await pipeline.run(trip, refresh=args.refresh)
    finally:
        await engine.dispose()

    output = args.output or Path(f"casa_certa_{trip.check_in.isoformat()}.xlsx")
    build_workbook(report.houses, report.failures, trip).save(output)
    print(f"Wrote {output} with {len(report.houses)} houses.")
    for room_id, messages in report.warnings.items():
        for message in messages:
            print(f"warning [{room_id}]: {message}", file=sys.stderr)
    for room_id, message in report.failures.items():
        print(f"FAILED [{room_id}]: {message}", file=sys.stderr)
    return 1 if report.failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except (ConfigError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

In `pyproject.toml`, add after the `dependencies` line:

```toml
[project.scripts]
casa-certa = "casa_certa.cli:main"
```

- [ ] **Step 4: Run the CLI tests**

Run: `uv sync && uv run pytest tests/test_cli.py -q`
Expected: 4 passed.

- [ ] **Step 5: Write the README**

Create `README.md`:

````markdown
# casa-certa

Compares Airbnb houses for a family trip: gathers each listing's facts, price and recent reviews, asks DeepSeek to
judge them against your aspects, and writes a spreadsheet (comparison + details with a price table per group size).

## Setup

```bash
uv sync
cp .env.example .env   # then fill APIFY_TOKEN and DEEPSEEK_API_KEY
```

## Use

Edit `trip.yaml` (URLs, dates, aspects), then:

```bash
uv run casa-certa run trip.yaml --dry-run   # see what would be fetched and what it costs
uv run casa-certa run trip.yaml             # fetch what is missing, analyze, write casa_certa_<check-in>.xlsx
uv run casa-certa run trip.yaml --refresh   # re-fetch everything (costs money)
```

Data already stored is never re-fetched unless you pass `--refresh`. New dates only fetch new prices.

## Development

```bash
make check       # format check, lint, mypy, tests
make test-live   # one real run on one house (spends a few cents)
```
````

Create `.env.example`:

```
APIFY_TOKEN=
DEEPSEEK_API_KEY=
# DATABASE_URL=sqlite+aiosqlite:///casa_certa.db
# ANALYSIS_MODEL=deepseek-chat   # deepseek-reasoner is deferred
```

- [ ] **Step 6: Write the live smoke test**

Create `tests/test_live.py`:

```python
"""One real run on one house. Skipped by default; costs a few cents.

Run with: uv run pytest -m live tests/test_live.py -s
"""

from pathlib import Path

import pytest
from apify_client import ApifyClientAsync

from casa_certa.ai.agents import HouseAnalyst, HouseNamer, make_deepseek_model
from casa_certa.config import Settings
from casa_certa.db.models import Base
from casa_certa.db.session import create_session_factory
from casa_certa.pipeline import Pipeline
from casa_certa.sources.airbnb_page import make_client
from casa_certa.sources.apify import AirbnbApify, ApifyActorRunner
from tests.fakes import make_trip

pytestmark = pytest.mark.live


async def test_real_pipeline_on_one_house(tmp_path: Path) -> None:
    settings = Settings()  # type: ignore[call-arg]
    engine, session_factory = create_session_factory(f"sqlite+aiosqlite:///{tmp_path / 'live.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    api_key = settings.deepseek_api_key.get_secret_value()
    trip = make_trip(urls=["https://www.airbnb.com.br/rooms/52584351"], review_cap=5)

    async with make_client() as http_client:
        pipeline = Pipeline(
            session_factory=session_factory,
            http_client=http_client,
            apify=AirbnbApify(ApifyActorRunner(ApifyClientAsync(settings.apify_token.get_secret_value()))),
            namer=HouseNamer(make_deepseek_model(api_key, settings.cheap_model)),
            analyst=HouseAnalyst(make_deepseek_model(api_key, settings.analysis_model)),
        )
        report = await pipeline.run(trip)
    await engine.dispose()

    assert report.failures == {}
    house = report.houses[0]
    print(house.name, house.quote_for(trip), house.analysis.summary if house.analysis else None)
    assert house.analysis is not None
    assert house.quote_for(trip) is not None
```

- [ ] **Step 7: Run all checks, then the live test once**

```bash
make format lint check
make test-live
```
Expected: the regular suite passes with the live test deselected; the live test prints a Portuguese house name, a price quote and a summary. Both agents use `deepseek-chat`: the reasoning model is deferred by decision. Moving the analysis to it later is only `ANALYSIS_MODEL=deepseek-reasoner` in `.env`, and this same live test is how to check it.

- [ ] **Step 8: Commit**

```bash
git add casa_certa tests README.md .env.example pyproject.toml uv.lock
git commit -m "feat: add the CLI, README and a live smoke test" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Out of scope for this plan (future work, per the spec)

Moving tasks to `deepseek-reasoner` (a settings change plus a live check), Google Sheets sync, the WhatsApp bot and its `WhatsAppConfigSource`, search by request (`tri_angle/airbnb-scraper`), renaming and `extra` notes from chat, fuzzy house-name resolution, per-house `--refresh <name>`, and running the analyses concurrently (the reasoning model is slow; the pipeline processes houses sequentially today for readability).
