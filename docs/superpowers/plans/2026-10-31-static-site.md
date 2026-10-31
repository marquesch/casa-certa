# Static comparison site Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `casa-certa site` / `publish` / `passphrase`: an encrypted, mobile-first static page (two sections, client-side filters and sorting) published to the `gh-pages` branch, plus the beach-distance extraction it needs.

**Architecture:** The analysis also returns a nullable beach distance (new DB columns). A pure view-model layer (`web/model.py`) turns stored houses into cards and sections; Jinja2 renders one self-contained HTML page (inline CSS, inline filter script); `cryptography` encrypts it (PBKDF2-SHA256 + AES-256-GCM) and a small unencrypted shell with a WebCrypto script decrypts it in the browser; `publish` force-pushes one orphan commit to `gh-pages` from a temporary repository.

**Tech Stack:** Python 3.11, SQLAlchemy async + Alembic, Pydantic, Jinja2 (new), cryptography (new), plain JS (no framework) tested with Node's built-in `node --test`, pytest.

Spec: `docs/superpowers/specs/2026-10-31-static-site-design.md`. Conventions: `CLAUDE.md` (English code, 120 cols, mypy strict, ruff, Conventional Commits, no personal data in the repo, never read or print `.env`, no `--no-verify`). Every commit message ends with the line `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

**File map**

| File | Responsibility |
|---|---|
| `casa_certa/ai/schemas.py`, `ai/prompts.py` | analysis output gains the beach distance |
| `casa_certa/db/models.py`, `db/repository.py`, `alembic/versions/*` | store it; `Analysis.verdict_for` |
| `casa_certa/web/model.py` | view models and sorting (no HTML) |
| `casa_certa/web/static/filters.js` | pure `matches`/`compare` (Node-testable) |
| `casa_certa/web/static/site.js` | DOM glue for the filter panel |
| `casa_certa/web/static/site.css`, `templates/page.html.j2` | page |
| `casa_certa/web/render.py` | `render_page`, `render_shell` |
| `casa_certa/web/crypto.py` | passphrase check, encrypt/decrypt |
| `casa_certa/web/static/unlock.js`, `templates/unlock.html.j2` | unlock shell |
| `casa_certa/web/site.py` | `write_site` |
| `casa_certa/web/publish.py` | git publishing, Pages URL |
| `casa_certa/web/passphrase.py`, `static/wordlist.txt` | passphrase generator |
| `casa_certa/cli.py`, `config.py`, `Makefile`, `.gitignore`, `.env.example`, README, CLAUDE.md | wiring and docs |

---

### Task 1: Extract the distance to the beach in the analysis

**Files:**
- Modify: `casa_certa/ai/schemas.py`, `casa_certa/ai/prompts.py`, `casa_certa/db/models.py`, `casa_certa/db/repository.py`
- Create: `alembic/versions/<generated>_analysis_beach_distance.py`, `tests/test_beach_distance.py`
- Modify: `tests/test_migrations.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_beach_distance.py`:

```python
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from casa_certa.ai.prompts import analysis_instructions
from casa_certa.ai.schemas import EvidenceOut, HouseAnalysis
from casa_certa.db.repository import HouseRepository
from casa_certa.enums import EvidenceSource
from tests.fakes import make_trip, sample_analysis


def analysis_with(distance: int | None, evidence: EvidenceOut | None) -> HouseAnalysis:
    data = sample_analysis(["Perto da praia"]).model_dump()
    data.update(beach_distance_m=distance, beach_distance_evidence=evidence)
    return HouseAnalysis.model_validate(data)


QUOTE = EvidenceOut(source=EvidenceSource.REVIEW, quote="a praia fica a 300 metros")


def test_distance_defaults_to_unknown() -> None:
    analysis = sample_analysis(["Perto da praia"])
    assert analysis.beach_distance_m is None
    assert analysis.beach_distance_evidence is None


def test_a_distance_needs_its_evidence() -> None:
    with pytest.raises(ValidationError, match="beach_distance_evidence"):
        analysis_with(300, None)


def test_a_distance_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        analysis_with(-1, QUOTE)


def test_zero_means_beachfront_and_is_valid() -> None:
    assert analysis_with(0, QUOTE).beach_distance_m == 0


def test_instructions_describe_the_distance_field() -> None:
    text = analysis_instructions(make_trip())
    assert "beach_distance_m" in text
    assert "beach_distance_evidence" in text


async def test_the_distance_and_its_evidence_are_stored(session: AsyncSession) -> None:
    repo = HouseRepository(session)
    house = await repo.add("1", "Casa")
    await repo.save_analysis(house, "hash", "model", analysis_with(300, QUOTE))
    await session.commit()

    loaded = await repo.get("1")

    assert loaded is not None and loaded.analysis is not None
    assert loaded.analysis.beach_distance_m == 300
    assert loaded.analysis.beach_distance_source is EvidenceSource.REVIEW
    assert loaded.analysis.beach_distance_quote == "a praia fica a 300 metros"


async def test_an_unknown_distance_stores_nothing(session: AsyncSession) -> None:
    repo = HouseRepository(session)
    house = await repo.add("1", "Casa")
    data = sample_analysis(["Perto da praia"]).model_dump()
    data.update(beach_distance_m=None, beach_distance_evidence=QUOTE)  # a stray quote without a number is dropped
    await repo.save_analysis(house, "hash", "model", HouseAnalysis.model_validate(data))
    await session.commit()

    loaded = await repo.get("1")

    assert loaded is not None and loaded.analysis is not None
    assert (loaded.analysis.beach_distance_m, loaded.analysis.beach_distance_source) == (None, None)
    assert loaded.analysis.beach_distance_quote is None
```

Append to `tests/test_migrations.py`:

```python
def test_beach_distance_columns_are_added_and_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "old.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "da6f61dd4ee4")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO house (id, airbnb_id, name, extra, created_at, updated_at)"
            " VALUES (1, '1', 'Casa', '', '2026-10-01', '2026-10-01')"
        )
        connection.execute(
            "INSERT INTO analysis (house_id, input_hash, summary, model, created_at)"
            " VALUES (1, 'h', 'ok', 'm', '2026-10-01')"
        )

    command.upgrade(config, "head")

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT summary, beach_distance_m, beach_distance_source, beach_distance_quote FROM analysis"
        ).fetchone()
    assert row == ("ok", None, None, None)

    command.downgrade(config, "da6f61dd4ee4")

    with sqlite3.connect(database) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(analysis)")]
    assert "beach_distance_m" not in columns
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_beach_distance.py tests/test_migrations.py -q`
Expected: failures (`beach_distance_m` unknown attribute / column).

- [ ] **Step 3: Implement the schema, prompt, model and repository changes**

`casa_certa/ai/schemas.py`: change the import to `from typing import Self` and `from pydantic import BaseModel, Field, model_validator`, then replace `HouseAnalysis` with:

```python
class HouseAnalysis(BaseModel):
    aspects: list[VerdictOut]
    pros: list[str]
    cons: list[str]
    summary: str = Field(description="One-line verdict on the house")
    confidence_note: str | None = Field(default=None, description="Thin or old evidence, missing reviews, etc.")
    beach_distance_m: int | None = Field(
        default=None,
        ge=0,
        description="Walking distance in meters to the nearest beach; 0 when beachfront; null when unknown",
    )
    beach_distance_evidence: EvidenceOut | None = Field(
        default=None, description="Verbatim quote the distance is based on; required when beach_distance_m is set"
    )

    @model_validator(mode="after")
    def _distance_needs_evidence(self) -> Self:
        if self.beach_distance_m is not None and self.beach_distance_evidence is None:
            raise ValueError("beach_distance_m needs beach_distance_evidence")
        return self
```

`casa_certa/ai/prompts.py`: in `analysis_instructions`, insert this bullet immediately before the line starting `- Write every free-text field`:

```
- beach_distance_m is the walking distance in meters from the house to the nearest beach: 0 for beachfront \
("pé na areia", "frente ao mar"), kilometers converted to meters. Use null when neither the listing nor the reviews \
give or clearly imply a distance; never guess. Reviews override the listing. Set beach_distance_evidence to a short \
verbatim quote the number is based on; it is required whenever beach_distance_m is set.
```

`casa_certa/db/models.py`: add `from casa_certa.names import normalize_name` to the imports. In class `Analysis`, after `created_at`, add the three columns, and after the `points` relationship add the method:

```python
    beach_distance_m: Mapped[int | None] = mapped_column(Integer)
    beach_distance_source: Mapped[EvidenceSource | None] = mapped_column(_enum_column(EvidenceSource))
    beach_distance_quote: Mapped[str | None] = mapped_column(Text)
```

```python
    def verdict_for(self, aspect_name: str) -> AspectVerdict | None:
        """The verdict for an aspect, matched ignoring case and spacing."""
        wanted = normalize_name(aspect_name)
        return next((v for v in self.verdicts if normalize_name(v.aspect_name) == wanted), None)
```

`casa_certa/db/repository.py`, in `save_analysis`, compute the evidence before building `Analysis(...)` and pass three more arguments:

```python
        evidence = analysis.beach_distance_evidence if analysis.beach_distance_m is not None else None
        house.analysis = Analysis(
            input_hash=input_hash,
            summary=analysis.summary,
            confidence_note=analysis.confidence_note,
            model=model,
            beach_distance_m=analysis.beach_distance_m,
            beach_distance_source=evidence.source if evidence else None,
            beach_distance_quote=evidence.quote if evidence else None,
            verdicts=[...unchanged...],
            points=points,
        )
```

Make `casa_certa/export/xlsx.py` use the new method: replace the body of `_status` with

```python
def _status(house: House, aspect_name: str) -> AspectStatus:
    verdict = house.analysis.verdict_for(aspect_name) if house.analysis else None
    return verdict.status if verdict else AspectStatus.UNKNOWN
```

and delete the now-unused `from casa_certa.names import normalize_name` import there.

- [ ] **Step 4: Generate the migration**

```bash
export DATABASE_URL="sqlite+aiosqlite:///$SCRATCHPAD/mig.db"   # SCRATCHPAD: the session scratchpad directory
rm -f "$SCRATCHPAD/mig.db"
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "analysis beach distance"
```

Open the new file in `alembic/versions/`. It must only add the three columns in `upgrade()` (inside `op.batch_alter_table('analysis')`) and drop them in `downgrade()`. The enum column must read `sa.Enum('listing', 'review', 'extra', name='evidencesource', native_enum=False, create_constraint=True, length=16)` with `nullable=True`. Remove any unrelated operations autogenerate may have produced. If Alembic complains about an unnamed CHECK constraint, give this one column's Enum a `name` in both the model (build it with `Enum(..., name="ck_analysis_beach_distance_source")` instead of `_enum_column`) and the migration, and keep the rest as is.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q` then `uv run mypy` then `uv run ruff check . && uv run ruff format --check .`
Expected: all pass (the existing prompt-hash and migration tests still pass; `test_migrated_schema_matches_the_orm_models` proves the migration matches the model).

- [ ] **Step 6: Commit**

```bash
git add casa_certa alembic tests
git commit -m "feat: extract the distance to the beach in the analysis

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Site view model

**Files:**
- Create: `casa_certa/web/__init__.py` (empty), `casa_certa/web/model.py`, `tests/test_web_model.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_web_model.py`:

```python
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from casa_certa.db.models import House
from casa_certa.db.repository import HouseRepository
from casa_certa.enums import AspectStatus, EvidenceSource
from casa_certa.sources.airbnb_page import ListingFacts
from casa_certa.sources.apify import PriceData
from casa_certa.web.model import build_site, city_of
from tests.fakes import ALBATROZ_TOTAL, ITAPOA_TOTALS, make_trip, sample_analysis

MUST = "Perto da praia"
NICE = "Piscina"


async def store(
    session: AsyncSession,
    airbnb_id: str,
    name: str,
    facts: ListingFacts,
    *,
    statuses: dict[str, AspectStatus] | None = None,
    totals: dict[int, int | None] | None = None,
    analysed: bool = True,
) -> House:
    repo = HouseRepository(session)
    house = await repo.add(airbnb_id, name)
    await repo.save_facts(house, facts)
    for guests, total in (totals or {}).items():
        await repo.save_quote(house, make_trip(), guests, PriceData(total_cents=total))
    if analysed:
        analysis = sample_analysis([MUST, NICE])
        for verdict in analysis.aspects:
            verdict.status = (statuses or {}).get(verdict.aspect, AspectStatus.MET)
        await repo.save_analysis(house, "hash", "model", analysis)
    await session.commit()
    return house


def albatroz_totals() -> dict[int, int | None]:
    return {size: ALBATROZ_TOTAL if size <= 12 else None for size in make_trip().group_sizes()}


def itapoa_totals() -> dict[int, int | None]:
    return {size: ITAPOA_TOTALS[size][0] for size in make_trip().group_sizes()}


def test_city_is_the_first_part_of_the_location() -> None:
    assert city_of("Guaratuba, Paraná, Brasil") == "Guaratuba"
    assert city_of(" Itapoá ") == "Itapoá"
    assert city_of(None) is None
    assert city_of("") is None


async def test_a_house_with_every_must_have_met_is_in_the_first_section(
    session: AsyncSession, matinhos_facts: ListingFacts
) -> None:
    house = await store(session, "46397403", "Casa do Albatroz", matinhos_facts, totals=albatroz_totals())

    site = build_site([house], make_trip())

    assert [c.name for c in site.complete] == ["Casa do Albatroz"]
    assert site.incomplete == ()
    card = site.complete[0]
    assert (card.city, card.rating, card.review_count) == ("Matinhos", 4.95, 87)
    assert card.url == "https://www.airbnb.com.br/rooms/46397403"
    assert card.per_person_cents(9) == 40834  # 367500 / 9 rounded up
    assert card.per_person_cents(13) is None  # unavailable


async def test_a_must_have_that_is_not_met_moves_the_house_down_and_is_listed(
    session: AsyncSession, matinhos_facts: ListingFacts
) -> None:
    for status in (AspectStatus.UNMET, AspectStatus.PARTIAL, AspectStatus.UNKNOWN):
        house = await store(session, "46397403", "Casa", matinhos_facts, statuses={MUST: status})

        site = build_site([house], make_trip())

        assert site.complete == ()
        assert [(a.name, a.status) for a in site.incomplete[0].missing_must_haves] == [(MUST, status)]
        await session.delete(house)
        await session.commit()


async def test_a_nice_to_have_that_is_not_met_does_not_move_the_house(
    session: AsyncSession, matinhos_facts: ListingFacts
) -> None:
    house = await store(session, "46397403", "Casa", matinhos_facts, statuses={NICE: AspectStatus.UNMET})

    site = build_site([house], make_trip())

    assert len(site.complete) == 1
    assert site.complete[0].met_indexes == (0,)  # only "Perto da praia" is met


async def test_a_listing_without_a_stored_house_or_analysis_gets_a_pending_card(
    session: AsyncSession, matinhos_facts: ListingFacts
) -> None:
    house = await store(session, "46397403", "Casa do Albatroz", matinhos_facts, analysed=False)

    site = build_site([house], make_trip())  # the trip lists a second house that was never stored

    assert site.complete == ()
    by_name = {c.name: c for c in site.incomplete}
    assert by_name["Casa do Albatroz"].analysed is False
    assert by_name["52584351"].analysed is False
    assert by_name["52584351"].url == "https://www.airbnb.com.br/rooms/52584351"
    assert by_name["52584351"].missing_must_haves == ()


async def test_houses_that_are_not_in_the_trip_are_ignored(session: AsyncSession, matinhos_facts: ListingFacts) -> None:
    house = await store(session, "46397403", "Casa do Albatroz", matinhos_facts)

    site = build_site([house], make_trip(urls=["https://www.airbnb.com.br/rooms/99999999"]))

    assert [c.name for c in site.incomplete] == ["99999999"]


async def test_cards_are_sorted_by_price_per_person_at_the_largest_size_with_unavailable_last(
    session: AsyncSession, matinhos_facts: ListingFacts, itapoa_facts: ListingFacts
) -> None:
    albatroz = await store(session, "46397403", "Casa do Albatroz", matinhos_facts, totals=albatroz_totals())
    itapoa = await store(session, "52584351", "Casa Itapoá", itapoa_facts, totals=itapoa_totals())

    site = build_site([albatroz, itapoa], make_trip())

    assert [c.name for c in site.complete] == ["Casa Itapoá", "Casa do Albatroz"]
    assert site.complete[0].per_person_cents(13) == 34116  # 443500 / 13 rounded up


async def test_site_header_data(session: AsyncSession, matinhos_facts: ListingFacts, itapoa_facts: ListingFacts) -> None:
    albatroz = await store(session, "46397403", "Casa do Albatroz", matinhos_facts, totals=albatroz_totals())
    itapoa = await store(session, "52584351", "Casa Itapoá", itapoa_facts)

    site = build_site([albatroz, itapoa], make_trip())

    assert site.cities == ("Itapoá", "Matinhos")
    assert [(a.name, a.must_have) for a in site.aspects] == [(MUST, True), (NICE, False)]
    assert (site.min_people, site.max_people) == (9, 13)
    assert list(site.sizes) == [9, 10, 11, 12, 13]
    assert isinstance(site.updated_at, datetime)


async def test_updated_at_is_empty_without_prices(session: AsyncSession, matinhos_facts: ListingFacts) -> None:
    house = await store(session, "46397403", "Casa do Albatroz", matinhos_facts)
    assert build_site([house], make_trip()).updated_at is None


async def test_beach_distance_and_text_are_copied_from_the_analysis(
    session: AsyncSession, matinhos_facts: ListingFacts
) -> None:
    house = await store(session, "46397403", "Casa do Albatroz", matinhos_facts)
    assert house.analysis is not None
    house.analysis.beach_distance_m = 30
    house.analysis.beach_distance_source = EvidenceSource.REVIEW
    house.analysis.beach_distance_quote = "a 30 metros do mar"

    card = build_site([house], make_trip()).complete[0]

    assert (card.beach_distance_m, card.beach_distance_source, card.beach_distance_quote) == (
        30,
        EvidenceSource.REVIEW,
        "a 30 metros do mar",
    )
    assert card.summary == "Boa casa para o grupo"
    assert card.pros == ("Piscina privativa",)
    assert card.cons == ("Sem ar-condicionado",)
    assert [a.name for a in card.aspects] == [MUST, NICE]
    assert card.aspects[0].evidence[0].quote == "100 metros do mar"
```

- [ ] **Step 2: Run and watch it fail**

Run: `uv run pytest tests/test_web_model.py -q` — Expected: `ModuleNotFoundError: casa_certa.web`.

- [ ] **Step 3: Implement `casa_certa/web/model.py`** (and an empty `casa_certa/web/__init__.py`)

```python
"""View models for the static site: plain data built from the stored houses, with no HTML in it."""

from dataclasses import dataclass
from datetime import date, datetime

from casa_certa.config import AspectKind, TripConfig
from casa_certa.db.models import House
from casa_certa.enums import AspectStatus, EvidenceSource, PointKind
from casa_certa.pricing import PriceRow, SizeQuote, price_table
from casa_certa.sources.airbnb_page import listing_url, room_id_from_url


@dataclass(frozen=True)
class EvidenceView:
    source: EvidenceSource
    quote: str


@dataclass(frozen=True)
class AspectHeading:
    name: str
    must_have: bool


@dataclass(frozen=True)
class AspectView:
    name: str
    must_have: bool
    status: AspectStatus
    reason: str
    evidence: tuple[EvidenceView, ...]


@dataclass(frozen=True)
class HouseCard:
    """One house. `aspects` follows the order of the trip's aspects, so an index here is an index there."""

    name: str
    url: str
    analysed: bool
    city: str | None = None
    rating: float | None = None
    review_count: int | None = None
    beach_distance_m: int | None = None
    beach_distance_source: EvidenceSource | None = None
    beach_distance_quote: str | None = None
    summary: str | None = None
    confidence_note: str | None = None
    extra: str = ""
    pros: tuple[str, ...] = ()
    cons: tuple[str, ...] = ()
    aspects: tuple[AspectView, ...] = ()
    prices: tuple[PriceRow, ...] = ()

    @property
    def missing_must_haves(self) -> tuple[AspectView, ...]:
        return tuple(a for a in self.aspects if a.must_have and a.status is not AspectStatus.MET)

    @property
    def has_all_must_haves(self) -> bool:
        return self.analysed and not self.missing_must_haves

    @property
    def met_indexes(self) -> tuple[int, ...]:
        return tuple(index for index, aspect in enumerate(self.aspects) if aspect.status is AspectStatus.MET)

    @property
    def has_original_price(self) -> bool:
        return any(row.original_total_cents is not None for row in self.prices)

    def per_person_cents(self, people: int) -> int | None:
        return next((row.per_person_cents for row in self.prices if row.people == people), None)


@dataclass(frozen=True)
class SiteModel:
    check_in: date
    check_out: date
    min_people: int
    max_people: int
    aspects: tuple[AspectHeading, ...]
    cities: tuple[str, ...]
    updated_at: datetime | None
    complete: tuple[HouseCard, ...]
    incomplete: tuple[HouseCard, ...]

    @property
    def sizes(self) -> range:
        return range(self.min_people, self.max_people + 1)


def city_of(location_name: str | None) -> str | None:
    """'Guaratuba, Paraná, Brasil' -> 'Guaratuba'."""
    if not location_name:
        return None
    return location_name.split(",")[0].strip() or None


def default_sort_key(card: HouseCard, people: int) -> tuple[bool, int, float, str]:
    """Cheapest per person at `people` first, houses without a price last, then best rated, then by name."""
    price = card.per_person_cents(people)
    return (price is None, price or 0, -(card.rating or 0.0), card.name)


def _aspect_views(house: House, trip: TripConfig) -> tuple[AspectView, ...]:
    views = []
    for aspect in trip.aspects:
        verdict = house.analysis.verdict_for(aspect.name) if house.analysis else None
        views.append(
            AspectView(
                name=aspect.name,
                must_have=aspect.kind is AspectKind.MUST_HAVE,
                status=verdict.status if verdict else AspectStatus.UNKNOWN,
                reason=verdict.reason if verdict else "",
                evidence=tuple(EvidenceView(e.source, e.quote) for e in verdict.evidence) if verdict else (),
            )
        )
    return tuple(views)


def _card(house: House, trip: TripConfig) -> HouseCard:
    facts = house.facts
    analysis = house.analysis
    quotes = {
        guests: SizeQuote(quote.total_cents, quote.original_total_cents)
        for guests, quote in house.quotes_by_guests(trip).items()
    }
    points = analysis.points if analysis else []
    return HouseCard(
        name=house.name,
        url=listing_url(house.airbnb_id),
        analysed=analysis is not None,
        city=city_of(facts.location_name) if facts else None,
        rating=facts.rating if facts else None,
        review_count=facts.review_count if facts else None,
        beach_distance_m=analysis.beach_distance_m if analysis else None,
        beach_distance_source=analysis.beach_distance_source if analysis else None,
        beach_distance_quote=analysis.beach_distance_quote if analysis else None,
        summary=analysis.summary if analysis else None,
        confidence_note=analysis.confidence_note if analysis else None,
        extra=house.extra.strip(),
        pros=tuple(p.text for p in points if p.kind is PointKind.PRO),
        cons=tuple(p.text for p in points if p.kind is PointKind.CON),
        aspects=_aspect_views(house, trip),
        prices=tuple(price_table(quotes, trip)) if quotes else (),
    )


def build_site(houses: list[House], trip: TripConfig) -> SiteModel:
    """Cards for the trip's listings only (the database may hold houses from other trips), in two sections."""
    by_airbnb_id = {house.airbnb_id: house for house in houses}
    shown: list[House] = []
    cards: list[HouseCard] = []
    for url in trip.urls:
        room_id = room_id_from_url(url)
        house = by_airbnb_id.get(room_id)
        if house is None:
            cards.append(HouseCard(name=room_id, url=listing_url(room_id), analysed=False))
        else:
            shown.append(house)
            cards.append(_card(house, trip))
    cards.sort(key=lambda card: default_sort_key(card, trip.max_people))
    fetched = [quote.fetched_at for house in shown for quote in house.quotes_by_guests(trip).values()]
    return SiteModel(
        check_in=trip.check_in,
        check_out=trip.check_out,
        min_people=trip.min_people,
        max_people=trip.max_people,
        aspects=tuple(AspectHeading(a.name, a.kind is AspectKind.MUST_HAVE) for a in trip.aspects),
        cities=tuple(sorted({card.city for card in cards if card.city})),
        updated_at=max(fetched, default=None),
        complete=tuple(card for card in cards if card.has_all_must_haves),
        incomplete=tuple(card for card in cards if not card.has_all_must_haves),
    )
```

- [ ] **Step 4: Run, fix, and run the whole quality gate**

Run: `uv run pytest -q && uv run mypy && uv run ruff check . && uv run ruff format --check .`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add casa_certa/web tests/test_web_model.py
git commit -m "feat: build the site view model with two sections and a default sort

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Filtering and sorting script

**Files:**
- Create: `casa_certa/web/static/filters.js`, `tests/js/filters.test.js`, `tests/test_js.py`

- [ ] **Step 1: Write the failing tests**

`tests/js/filters.test.js`:

```js
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { defaultState, matches, compare } = require("../../casa_certa/web/static/filters.js");

const card = (overrides = {}) => ({
  city: "Alfa",
  rating: 4.5,
  reviews: 10,
  beachM: 100,
  prices: { 9: 40000, 13: 30000 },
  met: [0, 1],
  ...overrides,
});
const state = (overrides = {}) => ({ ...defaultState(13), ...overrides });

test("the default state matches everything", () => {
  assert.equal(matches(card(), state()), true);
  assert.equal(matches(card({ prices: {}, beachM: null, city: null }), state()), true);
});

test("price bounds are in reais per person at the selected size", () => {
  assert.equal(matches(card(), state({ minPrice: 300 })), true);
  assert.equal(matches(card(), state({ minPrice: 301 })), false);
  assert.equal(matches(card(), state({ maxPrice: 300 })), true);
  assert.equal(matches(card(), state({ maxPrice: 299 })), false);
  assert.equal(matches(card(), state({ size: 9, maxPrice: 350 })), false); // 400 at 9 people
});

test("a house without a price for the size is hidden only while a price bound is set", () => {
  const unpriced = card({ prices: { 9: 40000, 13: null } });
  assert.equal(matches(unpriced, state()), true);
  assert.equal(matches(unpriced, state({ minPrice: 0 })), false);
  assert.equal(matches(unpriced, state({ maxPrice: 1000 })), false);
});

test("cities: none selected means all, otherwise the city must be selected", () => {
  assert.equal(matches(card(), state({ cities: ["Beta"] })), false);
  assert.equal(matches(card(), state({ cities: ["Beta", "Alfa"] })), true);
  assert.equal(matches(card({ city: null }), state({ cities: ["Alfa"] })), false);
});

test("beach distance: up to a bound, and unknown only matches 'any'", () => {
  assert.equal(matches(card({ beachM: 100 }), state({ maxBeachM: 100 })), true);
  assert.equal(matches(card({ beachM: 101 }), state({ maxBeachM: 100 })), false);
  assert.equal(matches(card({ beachM: 0 }), state({ maxBeachM: 100 })), true);
  assert.equal(matches(card({ beachM: null }), state({ maxBeachM: 1000 })), false);
  assert.equal(matches(card({ beachM: null }), state()), true);
});

test("aspect checkboxes require every checked aspect to be met", () => {
  assert.equal(matches(card({ met: [0, 2] }), state({ aspects: [0] })), true);
  assert.equal(matches(card({ met: [0, 2] }), state({ aspects: [0, 1] })), false);
  assert.equal(matches(card({ met: [] }), state({ aspects: [] })), true);
});

test("sort by price: cheapest first at the selected size, unpriced last, then best rated", () => {
  const cheap = card({ prices: { 13: 20000 } });
  const dear = card({ prices: { 13: 50000 } });
  const none = card({ prices: { 13: null } });
  const tieHigh = card({ prices: { 13: 20000 }, rating: 4.9 });
  const sorted = [none, dear, cheap, tieHigh].sort((a, b) => compare(a, b, state()));
  assert.deepEqual(sorted, [tieHigh, cheap, dear, none]);
  const at9 = [card({ prices: { 9: 90000 } }), card({ prices: { 9: 10000 } })].sort((a, b) =>
    compare(a, b, state({ size: 9 })),
  );
  assert.equal(at9[0].prices[9], 10000);
});

test("sort by rating: best first, unrated last, then cheapest", () => {
  const best = card({ rating: 4.9 });
  const good = card({ rating: 4.5, prices: { 13: 10000 } });
  const good2 = card({ rating: 4.5, prices: { 13: 90000 } });
  const unrated = card({ rating: null });
  const sorted = [unrated, good2, best, good].sort((a, b) => compare(a, b, state({ sort: "rating" })));
  assert.deepEqual(sorted, [best, good, good2, unrated]);
});

test("sort by beach: nearest first, unknown last, then cheapest", () => {
  const front = card({ beachM: 0 });
  const near = card({ beachM: 50, prices: { 13: 10000 } });
  const near2 = card({ beachM: 50, prices: { 13: 90000 } });
  const unknown = card({ beachM: null });
  const sorted = [unknown, near2, front, near].sort((a, b) => compare(a, b, state({ sort: "beach" })));
  assert.deepEqual(sorted, [front, near, near2, unknown]);
});
```

`tests/test_js.py`:

```python
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def test_the_javascript_unit_tests_pass() -> None:
    result = subprocess.run(
        ["node", "--test", "tests/js/*.test.js"], capture_output=True, text=True, cwd=ROOT, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
```

- [ ] **Step 2: Run and watch it fail**

Run: `uv run pytest tests/test_js.py -q` — Expected: FAIL (cannot find module `filters.js`).

- [ ] **Step 3: Implement `casa_certa/web/static/filters.js`**

```js
"use strict";

// Pure filtering and sorting for the house cards. A card is
// { city, rating, reviews, beachM, prices: { <people>: cents per person | null }, met: [aspect indexes] }.
// The state is { size, minPrice, maxPrice (reais per person), cities, maxBeachM, aspects, sort }.
const filters = (() => {
  const num = (value) => (typeof value === "number" && !Number.isNaN(value) ? value : null);

  function defaultState(size) {
    return { size, minPrice: null, maxPrice: null, cities: [], maxBeachM: null, aspects: [], sort: "price" };
  }

  function priceOf(card, state) {
    return num(card.prices[state.size]);
  }

  function matches(card, state) {
    const price = priceOf(card, state);
    if (state.minPrice !== null && !(price !== null && price >= state.minPrice * 100)) return false;
    if (state.maxPrice !== null && !(price !== null && price <= state.maxPrice * 100)) return false;
    if (state.cities.length > 0 && !state.cities.includes(card.city)) return false;
    const beach = num(card.beachM);
    if (state.maxBeachM !== null && !(beach !== null && beach <= state.maxBeachM)) return false;
    return state.aspects.every((index) => card.met.includes(index));
  }

  // direction 1: smaller first, -1: larger first; missing values always go last.
  function byValue(x, y, direction) {
    const a = num(x);
    const b = num(y);
    if (a === null && b === null) return 0;
    if (a === null) return 1;
    if (b === null) return -1;
    return direction * (a - b);
  }

  function compare(a, b, state) {
    const price = byValue(priceOf(a, state), priceOf(b, state), 1);
    const rating = byValue(a.rating, b.rating, -1);
    if (state.sort === "rating") return rating || price;
    if (state.sort === "beach") return byValue(a.beachM, b.beachM, 1) || price;
    return price || rating;
  }

  return { defaultState, matches, compare };
})();

if (typeof module !== "undefined") module.exports = filters;
```

- [ ] **Step 4: Run, then the quality gate**

Run: `uv run pytest -q` (the JS tests run through `tests/test_js.py`) — Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add casa_certa/web/static/filters.js tests/js tests/test_js.py
git commit -m "feat: add the card filtering and sorting script

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Render the page

**Files:**
- Modify: `casa_certa/i18n.py`, `pyproject.toml` (via `uv add`)
- Create: `casa_certa/web/render.py`, `casa_certa/web/templates/page.html.j2`, `casa_certa/web/static/site.css`, `casa_certa/web/static/site.js`, `tests/test_web_render.py`

- [ ] **Step 1: Add the dependency**

Run: `uv add jinja2` (and `uv add cryptography` now too, it is needed in Task 5).

- [ ] **Step 2: Add the site labels**

In `casa_certa/i18n.py`, append these fields to the `Labels` dataclass (after `source_extra: str`):

```python
    site_title: str
    trip_line: str
    updated: str
    section_complete: str
    section_incomplete: str
    count_of: str
    no_matches: str
    filters: str
    group_size: str
    price_range: str
    price_min: str
    price_max: str
    city: str
    beach_distance: str
    beach_any: str
    beach_up_to: str
    aspects_filter: str
    sort_by: str
    sort_price: str
    sort_rating: str
    sort_beach: str
    clear_filters: str
    details: str
    open_listing: str
    beach: str
    estimated: str
    no_analysis: str
    missing: str
    must_have: str
    status_met: str
    status_partial: str
    status_unmet: str
    status_unknown: str
    decimal_separator: str
    unlock_title: str
    passphrase: str
    unlock_button: str
    remember: str
    wrong_passphrase: str
    unlocking: str
```

and pass these values to the two instances (add after `source_extra=...` in each):

```python
# PT_BR
    site_title="Casa Certa",
    trip_line="{dates} · {min} a {max} pessoas",
    updated="Atualizado em {date}",
    section_complete="Têm todos os essenciais",
    section_incomplete="Faltam essenciais",
    count_of="de",
    no_matches="Nenhuma casa com esses filtros.",
    filters="Filtros",
    group_size="Pessoas",
    price_range="Preço por pessoa (R$)",
    price_min="mínimo",
    price_max="máximo",
    city="Cidade",
    beach_distance="Distância da praia",
    beach_any="qualquer",
    beach_up_to="até {distance}",
    aspects_filter="Exigir",
    sort_by="Ordenar por",
    sort_price="Preço por pessoa",
    sort_rating="Nota",
    sort_beach="Distância da praia",
    clear_filters="Limpar filtros",
    details="Detalhes",
    open_listing="Abrir no Airbnb",
    beach="Praia",
    estimated="estimado",
    no_analysis="sem análise",
    missing="Falta",
    must_have="essencial",
    status_met="atende",
    status_partial="parcial",
    status_unmet="não atende",
    status_unknown="não confirmado",
    decimal_separator=",",
    unlock_title="Digite a senha para ver as casas",
    passphrase="Senha",
    unlock_button="Abrir",
    remember="Lembrar neste aparelho",
    wrong_passphrase="Senha incorreta",
    unlocking="Abrindo…",
```

```python
# EN
    site_title="Casa Certa",
    trip_line="{dates} · {min} to {max} people",
    updated="Updated {date}",
    section_complete="Have every must-have",
    section_incomplete="Missing must-haves",
    count_of="of",
    no_matches="No house matches these filters.",
    filters="Filters",
    group_size="People",
    price_range="Price per person (R$)",
    price_min="minimum",
    price_max="maximum",
    city="City",
    beach_distance="Distance to the beach",
    beach_any="any",
    beach_up_to="up to {distance}",
    aspects_filter="Require",
    sort_by="Sort by",
    sort_price="Price per person",
    sort_rating="Rating",
    sort_beach="Distance to the beach",
    clear_filters="Clear filters",
    details="Details",
    open_listing="Open on Airbnb",
    beach="Beach",
    estimated="estimated",
    no_analysis="not analysed",
    missing="Missing",
    must_have="must-have",
    status_met="met",
    status_partial="partial",
    status_unmet="not met",
    status_unknown="unconfirmed",
    decimal_separator=".",
    unlock_title="Enter the passphrase to see the houses",
    passphrase="Passphrase",
    unlock_button="Open",
    remember="Remember on this device",
    wrong_passphrase="Wrong passphrase",
    unlocking="Opening…",
```

- [ ] **Step 3: Write the failing render tests**

`tests/test_web_render.py`:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from casa_certa.db.models import House
from casa_certa.enums import AspectStatus, EvidenceSource
from casa_certa.i18n import labels_for
from casa_certa.web.model import build_site
from casa_certa.web.render import format_distance, render_page
from tests.fakes import make_trip
from tests.test_xlsx import analysed_house


def page(house: House, **trip_overrides: object) -> str:
    trip = make_trip(**trip_overrides)
    return render_page(build_site([house], trip), trip, labels_for(trip.language))


def test_distances_are_shown_in_meters_or_kilometers() -> None:
    labels = labels_for("pt-BR")
    assert format_distance(30, labels) == "30 m"
    assert format_distance(1000, labels) == "1 km"
    assert format_distance(1500, labels) == "1,5 km"
    assert format_distance(1500, labels_for("en")) == "1.5 km"


async def test_page_has_header_sections_filters_and_card_data(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)

    html = page(house)

    assert '<html lang="pt-BR">' in html
    assert 'name="robots" content="noindex"' in html
    assert "05/12–09/12 · 9 a 13 pessoas" in html
    assert "Atualizado em" in html
    assert 'id="complete"' in html and 'id="incomplete"' in html
    assert "Têm todos os essenciais" in html and "Faltam essenciais" in html
    assert "Casa do Albatroz" in html
    assert 'data-city="Matinhos"' in html
    assert 'data-price9="40834"' in html
    assert 'data-price13=""' in html
    assert 'data-met="0 1"' in html
    assert 'id="f-size"' in html and 'id="f-min"' in html and 'id="f-beach"' in html and 'id="f-sort"' in html
    assert 'name="city" value="Matinhos"' in html
    assert 'name="aspect" value="0"' in html and 'name="aspect" value="1"' in html
    assert "★ Perto da praia" in html
    assert "https://www.airbnb.com.br/rooms/46397403" in html


async def test_a_house_missing_a_must_have_is_listed_under_the_second_section(
    session: AsyncSession, stored_house: House
) -> None:
    house = await analysed_house(session, stored_house)
    assert house.analysis is not None
    house.analysis.verdicts[0].status = AspectStatus.UNMET

    html = page(house)

    second = html[html.index('id="incomplete"') :]
    assert "Casa do Albatroz" in second
    assert "Falta" in second and "não atende" in second
    assert "Casa do Albatroz" not in html[: html.index('id="incomplete"')].split('id="complete"')[1]


async def test_beach_distance_is_labelled_as_an_estimate_with_its_quote(
    session: AsyncSession, stored_house: House
) -> None:
    house = await analysed_house(session, stored_house)
    assert house.analysis is not None
    house.analysis.beach_distance_m = 30
    house.analysis.beach_distance_source = EvidenceSource.REVIEW
    house.analysis.beach_distance_quote = "a 30 metros do mar"

    html = page(house)

    assert "30 m" in html and "estimado" in html and "a 30 metros do mar" in html
    assert 'data-beach="30"' in html


async def test_unknown_values_are_shown_as_not_available(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)
    assert 'data-beach=""' in page(house)
    assert "n/d" in page(house)


async def test_untrusted_text_is_escaped(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)
    assert house.analysis is not None
    house.analysis.verdicts[0].evidence[0].quote = "<script>alert(1)</script>"
    house.extra = "<b>x</b>"

    html = page(house)

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html


async def test_details_have_the_summary_pros_cons_evidence_and_the_price_table(
    session: AsyncSession, stored_house: House
) -> None:
    house = await analysed_house(session, stored_house)

    html = page(house)

    for text in ("Boa casa para o grupo", "Piscina privativa", "Sem ar-condicionado", "100 metros do mar", "R$ 3.675,00"):
        assert text in html
    assert "O dono confirmou que a praia fica a 3 minutos a pé." in html


async def test_the_page_can_be_rendered_in_english(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)
    html = page(house, language="en")
    assert "Have every must-have" in html and "Missing must-haves" in html
```

- [ ] **Step 4: Run and watch it fail**

Run: `uv run pytest tests/test_web_render.py -q` — Expected: `ModuleNotFoundError: casa_certa.web.render`.

- [ ] **Step 5: Implement `casa_certa/web/render.py`**

```python
"""Renders the site view model to the page (still plain HTML: encryption happens afterwards)."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

from casa_certa.config import TripConfig
from casa_certa.enums import AspectStatus, EvidenceSource
from casa_certa.i18n import Labels
from casa_certa.pricing import format_brl
from casa_certa.web.model import SiteModel

WEB_DIR = Path(__file__).parent
BEACH_BANDS_M = (100, 300, 1000)
STATUS_SYMBOLS = {
    AspectStatus.MET: "✅",
    AspectStatus.PARTIAL: "⚠️",
    AspectStatus.UNMET: "❌",
    AspectStatus.UNKNOWN: "❓",
}


def read_static(name: str) -> Markup:
    """A bundled CSS or JS file, to be inlined as is."""
    return Markup((WEB_DIR / "static" / name).read_text(encoding="utf8"))  # noqa: S704 - our own files


def format_distance(meters: int, labels: Labels) -> str:
    if meters < 1000:
        return f"{meters} m"
    kilometers = f"{meters / 1000:.1f}".rstrip("0").rstrip(".")
    return f"{kilometers.replace('.', labels.decimal_separator)} km"


def _environment(labels: Labels) -> Environment:
    environment = Environment(
        loader=FileSystemLoader(WEB_DIR / "templates"), autoescape=True, undefined=StrictUndefined
    )
    environment.filters["brl"] = format_brl
    environment.filters["distance"] = lambda meters: format_distance(meters, labels)
    return environment


def _status_texts(labels: Labels) -> dict[str, tuple[str, str]]:
    names = {
        AspectStatus.MET: labels.status_met,
        AspectStatus.PARTIAL: labels.status_partial,
        AspectStatus.UNMET: labels.status_unmet,
        AspectStatus.UNKNOWN: labels.status_unknown,
    }
    return {status.value: (STATUS_SYMBOLS[status], names[status]) for status in AspectStatus}


def _source_texts(labels: Labels) -> dict[str, str]:
    return {
        EvidenceSource.LISTING.value: labels.source_listing,
        EvidenceSource.REVIEW.value: labels.source_review,
        EvidenceSource.EXTRA.value: labels.source_extra,
    }


def render_page(site: SiteModel, trip: TripConfig, labels: Labels) -> str:
    dates = f"{site.check_in:%d/%m}–{site.check_out:%d/%m}"
    return _environment(labels).get_template("page.html.j2").render(
        site=site,
        labels=labels,
        language=trip.language,
        trip_line=labels.trip_line.format(dates=dates, min=site.min_people, max=site.max_people),
        updated=labels.updated.format(date=f"{site.updated_at:%d/%m}") if site.updated_at else "",
        price_basis=labels.price_basis.format(dates=dates),
        statuses=_status_texts(labels),
        source_texts=_source_texts(labels),
        beach_bands=[
            (meters, labels.beach_up_to.format(distance=format_distance(meters, labels))) for meters in BEACH_BANDS_M
        ],
        css=read_static("site.css"),
        script=Markup(f"{read_static('filters.js')}\n{read_static('site.js')}"),  # noqa: S704 - our own files
    )
```

(`render_shell` is added in Task 5.)

- [ ] **Step 6: Create the template `casa_certa/web/templates/page.html.j2`**

```jinja
{% macro status_icon(aspect) -%}
<span class="icon" aria-hidden="true">{{ statuses[aspect.status.value][0] }}</span><span class="sr">{{ statuses[aspect.status.value][1] }}</span>
{%- endmacro %}

{% macro card(c, show_missing) %}
<article class="card{% if not c.analysed %} pending{% endif %}" data-city="{{ c.city or '' }}" data-rating="{{ '' if c.rating is none else c.rating }}" data-reviews="{{ '' if c.review_count is none else c.review_count }}" data-beach="{{ '' if c.beach_distance_m is none else c.beach_distance_m }}"{% for p in c.prices %} data-price{{ p.people }}="{{ '' if p.per_person_cents is none else p.per_person_cents }}"{% endfor %} data-met="{{ c.met_indexes | join(' ') }}">
  <h3><a href="{{ c.url }}" rel="noopener">{{ c.name }}</a></h3>
  {% if not c.analysed %}
  <p class="muted">{{ labels.no_analysis }}</p>
  {% else %}
  <p class="facts">
    {% if c.rating is not none %}★ {{ c.rating }}{% if c.review_count is not none %} ({{ c.review_count }}){% endif %}{% else %}{{ labels.not_available }}{% endif %}
    · {{ c.city or labels.not_available }}
  </p>
  <p class="facts">{{ labels.beach }}:
    {% if c.beach_distance_m is none %}{{ labels.not_available }}{% else %}{{ c.beach_distance_m | distance }} <span class="muted">({{ labels.estimated }})</span>{% endif %}
  </p>
  <p class="price">{{ labels.per_person }}: <strong class="price-now" data-na="{{ labels.not_available }}">{% set cents = c.per_person_cents(site.max_people) %}{{ (cents | brl) if cents is not none else labels.not_available }}</strong></p>
  <ul class="aspects">
    {% for a in c.aspects %}
    <li class="{{ a.status.value }}">{{ status_icon(a) }} {% if a.must_have %}<span title="{{ labels.must_have }}">★</span> {% endif %}{{ a.name }}</li>
    {% endfor %}
  </ul>
  {% if show_missing and c.missing_must_haves %}
  <p class="missing">{{ labels.missing }}:
    {% for a in c.missing_must_haves %}{{ statuses[a.status.value][0] }} {{ a.name }} ({{ statuses[a.status.value][1] }}){% if not loop.last %}; {% endif %}{% endfor %}
  </p>
  {% endif %}
  <details class="more">
    <summary>{{ labels.details }}</summary>
    {% if c.summary %}<p><strong>{{ labels.summary }}:</strong> {{ c.summary }}</p>{% endif %}
    {% if c.confidence_note %}<p><strong>{{ labels.confidence }}:</strong> {{ c.confidence_note }}</p>{% endif %}
    {% if c.extra %}<p><strong>{{ labels.extra }}:</strong> {{ c.extra }}</p>{% endif %}
    {% if c.pros %}<h4>{{ labels.pros }}</h4><ul>{% for text in c.pros %}<li>{{ text }}</li>{% endfor %}</ul>{% endif %}
    {% if c.cons %}<h4>{{ labels.cons }}</h4><ul>{% for text in c.cons %}<li>{{ text }}</li>{% endfor %}</ul>{% endif %}
    {% if c.beach_distance_quote %}<p><strong>{{ labels.beach }}:</strong> <q>{{ c.beach_distance_quote }}</q> <span class="muted">[{{ source_texts[c.beach_distance_source.value] }}]</span></p>{% endif %}
    <h4>{{ labels.aspect }}</h4>
    {% for a in c.aspects %}
    <div class="verdict">
      <p><strong>{{ status_icon(a) }} {% if a.must_have %}★ {% endif %}{{ a.name }}</strong>{% if a.reason %} — {{ a.reason }}{% endif %}</p>
      {% for e in a.evidence %}<blockquote>[{{ source_texts[e.source.value] }}] “{{ e.quote }}”</blockquote>{% endfor %}
    </div>
    {% endfor %}
    {% if c.prices %}
    <h4>{{ labels.prices }}</h4>
    <table>
      <thead><tr><th>{{ labels.people }}</th><th>{{ labels.total }}</th><th>{{ labels.per_person }}</th>{% if c.has_original_price %}<th>{{ labels.original_price }}</th>{% endif %}</tr></thead>
      <tbody>
        {% for row in c.prices %}
        <tr>
          <td>{{ row.people }}</td>
          <td>{{ (row.total_cents | brl) if row.total_cents is not none else labels.not_available }}</td>
          <td>{{ (row.per_person_cents | brl) if row.per_person_cents is not none else labels.not_available }}</td>
          {% if c.has_original_price %}<td>{{ (row.original_total_cents | brl) if row.original_total_cents is not none else '' }}</td>{% endif %}
        </tr>
        {% endfor %}
      </tbody>
    </table>
    <p class="muted">{{ price_basis }}</p>
    {% endif %}
    <p><a href="{{ c.url }}" rel="noopener">{{ labels.open_listing }} ↗</a></p>
  </details>
  {% endif %}
</article>
{% endmacro %}

{% macro group(id, title, cards, show_missing) %}
<section class="group" id="{{ id }}">
  <h2>{{ title }} <span class="count">({{ cards | length }} {{ labels.count_of }} {{ cards | length }})</span></h2>
  <div class="cards">{% for c in cards %}{{ card(c, show_missing) }}{% endfor %}</div>
  <p class="empty muted"{% if cards %} hidden{% endif %}>{{ labels.no_matches }}</p>
</section>
{% endmacro %}
<!doctype html>
<html lang="{{ language }}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{{ labels.site_title }}</title>
<style>{{ css }}</style>
</head>
<body>
<main>
  <header>
    <h1>{{ labels.site_title }}</h1>
    <p class="muted">{{ trip_line }}{% if updated %} · {{ updated }}{% endif %}</p>
    <p class="muted">{{ price_basis }}</p>
  </header>

  <details class="filters" id="filters">
    <summary>{{ labels.filters }}</summary>
    <div class="filter-grid">
      <label>{{ labels.group_size }}
        <select id="f-size" data-default="{{ site.max_people }}">
          {% for n in site.sizes %}<option value="{{ n }}"{% if n == site.max_people %} selected{% endif %}>{{ n }}</option>{% endfor %}
        </select>
      </label>
      <label>{{ labels.price_range }}
        <span class="pair">
          <input id="f-min" type="number" inputmode="decimal" min="0" step="any" placeholder="{{ labels.price_min }}">
          <input id="f-max" type="number" inputmode="decimal" min="0" step="any" placeholder="{{ labels.price_max }}">
        </span>
      </label>
      <label>{{ labels.beach_distance }}
        <select id="f-beach">
          <option value="">{{ labels.beach_any }}</option>
          {% for meters, text in beach_bands %}<option value="{{ meters }}">{{ text }}</option>{% endfor %}
        </select>
      </label>
      <label>{{ labels.sort_by }}
        <select id="f-sort">
          <option value="price">{{ labels.sort_price }}</option>
          <option value="rating">{{ labels.sort_rating }}</option>
          <option value="beach">{{ labels.sort_beach }}</option>
        </select>
      </label>
      {% if site.cities %}
      <fieldset><legend>{{ labels.city }}</legend>
        {% for city in site.cities %}<label class="check"><input type="checkbox" name="city" value="{{ city }}"> {{ city }}</label>{% endfor %}
      </fieldset>
      {% endif %}
      <fieldset><legend>{{ labels.aspects_filter }}</legend>
        {% for a in site.aspects %}<label class="check"><input type="checkbox" name="aspect" value="{{ loop.index0 }}"> {% if a.must_have %}★ {% endif %}{{ a.name }}</label>{% endfor %}
      </fieldset>
    </div>
    <button type="button" id="f-clear">{{ labels.clear_filters }}</button>
  </details>

  {{ group("complete", labels.section_complete, site.complete, false) }}
  {{ group("incomplete", labels.section_incomplete, site.incomplete, true) }}
</main>
<script>{{ script }}</script>
</body>
</html>
```

- [ ] **Step 7: Create `casa_certa/web/static/site.css`**

```css
:root {
  color-scheme: light dark;
  --bg: #fff; --fg: #1d1d1f; --muted: #5f6368; --line: #d9d9de; --card: #f6f6f8; --accent: #0b57d0;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #121214; --fg: #ececf0; --muted: #a0a3aa; --line: #33343a; --card: #1c1c20; --accent: #8ab4f8; }
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body { margin: 0; font: 16px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; background: var(--bg); color: var(--fg); }
main { max-width: 72rem; margin: 0 auto; padding: 1rem; }
a { color: var(--accent); }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
h2 { font-size: 1.2rem; margin: 1.5rem 0 .75rem; }
h3 { font-size: 1.05rem; margin: 0 0 .25rem; }
h4 { margin: .75rem 0 .25rem; font-size: .95rem; }
p { margin: .25rem 0; }
.muted { color: var(--muted); }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.filters { border: 1px solid var(--line); border-radius: .5rem; padding: .5rem .75rem; margin-top: 1rem; }
.filters summary { cursor: pointer; font-weight: 600; padding: .25rem 0; }
.filter-grid { display: grid; gap: .75rem; margin: .5rem 0; grid-template-columns: 1fr; }
.filter-grid label { display: grid; gap: .25rem; font-size: .9rem; }
.pair { display: flex; gap: .5rem; }
select, input[type="number"], button { font: inherit; padding: .5rem; border: 1px solid var(--line); border-radius: .4rem; background: var(--bg); color: var(--fg); min-height: 2.5rem; }
.pair input { flex: 1; min-width: 0; }
fieldset { border: 1px solid var(--line); border-radius: .4rem; margin: 0; padding: .25rem .75rem .5rem; }
.check { display: flex; align-items: center; gap: .5rem; padding: .2rem 0; }
.check input { width: 1.1rem; height: 1.1rem; }
.cards { display: grid; gap: .75rem; grid-template-columns: 1fr; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: .6rem; padding: .75rem; }
.card.pending { opacity: .75; }
.facts { color: var(--muted); font-size: .9rem; }
.price { font-size: 1.05rem; }
.aspects { list-style: none; padding: 0; margin: .5rem 0; display: grid; gap: .15rem; }
.missing { font-size: .9rem; border-left: 3px solid var(--line); padding-left: .5rem; }
.more summary { cursor: pointer; color: var(--accent); padding: .35rem 0; }
blockquote { margin: .2rem 0 .2rem .5rem; padding-left: .5rem; border-left: 3px solid var(--line); color: var(--muted); font-size: .9rem; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { text-align: left; padding: .25rem .5rem; border-bottom: 1px solid var(--line); }
@media (min-width: 48em) {
  .filter-grid { grid-template-columns: repeat(2, 1fr); }
  .cards { grid-template-columns: repeat(2, 1fr); }
}
@media (min-width: 72em) {
  .filter-grid { grid-template-columns: repeat(4, 1fr); }
  .cards { grid-template-columns: repeat(3, 1fr); }
}
```

- [ ] **Step 8: Create `casa_certa/web/static/site.js`** (uses the `filters` object defined just before it in the same script)

```js
"use strict";

(() => {
  const money = new Intl.NumberFormat(document.documentElement.lang || "pt-BR", { style: "currency", currency: "BRL" });
  const panel = document.getElementById("filters");
  const size = document.getElementById("f-size");
  const min = document.getElementById("f-min");
  const max = document.getElementById("f-max");
  const beach = document.getElementById("f-beach");
  const sort = document.getElementById("f-sort");
  const clear = document.getElementById("f-clear");
  const sections = [...document.querySelectorAll(".group")];

  const number = (value) => {
    const text = String(value).trim().replace(",", ".");
    return text === "" || Number.isNaN(Number(text)) ? null : Number(text);
  };

  function readCard(element) {
    const prices = {};
    for (const [key, value] of Object.entries(element.dataset)) {
      const match = /^price(\d+)$/.exec(key);
      if (match) prices[Number(match[1])] = number(value);
    }
    return {
      city: element.dataset.city || null,
      rating: number(element.dataset.rating ?? ""),
      reviews: number(element.dataset.reviews ?? ""),
      beachM: number(element.dataset.beach ?? ""),
      prices,
      met: element.dataset.met ? element.dataset.met.split(" ").map(Number) : [],
    };
  }

  const cards = [...document.querySelectorAll(".card")].map((element) => ({ element, data: readCard(element) }));
  const checked = (name) => [...document.querySelectorAll(`input[name="${name}"]:checked`)];

  function readState() {
    return {
      size: Number(size.value),
      minPrice: number(min.value),
      maxPrice: number(max.value),
      cities: checked("city").map((input) => input.value),
      maxBeachM: number(beach.value),
      aspects: checked("aspect").map((input) => Number(input.value)),
      sort: sort.value,
    };
  }

  function apply() {
    const state = readState();
    for (const section of sections) {
      const inSection = cards.filter((card) => section.contains(card.element));
      const visible = inSection
        .filter((card) => filters.matches(card.data, state))
        .sort((a, b) => filters.compare(a.data, b.data, state));
      const container = section.querySelector(".cards");
      for (const card of inSection) card.element.hidden = !visible.includes(card);
      for (const card of visible) container.append(card.element);
      const count = section.querySelector(".count");
      const word = count.textContent.split(" ")[2];
      count.textContent = `(${visible.length} ${word} ${inSection.length})`;
      section.querySelector(".empty").hidden = visible.length > 0;
    }
    for (const card of cards) {
      const label = card.element.querySelector(".price-now");
      if (!label) continue;
      const cents = card.data.prices[state.size];
      label.textContent = typeof cents === "number" ? money.format(cents / 100) : label.dataset.na;
    }
  }

  function reset() {
    size.value = size.dataset.default;
    min.value = "";
    max.value = "";
    beach.value = "";
    sort.value = "price";
    for (const input of document.querySelectorAll("#filters input[type=checkbox]")) input.checked = false;
    apply();
  }

  panel.addEventListener("input", apply);
  panel.addEventListener("change", apply);
  clear.addEventListener("click", reset);
  if (window.matchMedia("(min-width: 48em)").matches) panel.open = true;
  apply();
})();
```

Note: the count text is `(N de M)`; `split(" ")[2]` is the word between the numbers ("de"/"of").

- [ ] **Step 9: Run the tests and the quality gate**

Run: `uv run pytest -q && uv run mypy && uv run ruff check . && uv run ruff format --check .`
Expected: pass. If ruff flags the `# noqa: S704` comments as unknown rules, remove them (they only exist to document that the inlined files are ours).

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml uv.lock casa_certa tests
git commit -m "feat: render the comparison page with filters

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Encryption and the unlock shell

**Files:**
- Create: `casa_certa/web/crypto.py`, `casa_certa/web/templates/unlock.html.j2`, `casa_certa/web/static/unlock.js`, `tests/test_web_crypto.py`, `tests/js/run_unlock.js`, `tests/test_web_unlock.py`
- Modify: `casa_certa/web/render.py` (add `render_shell`)

- [ ] **Step 1: Write the failing crypto tests**

`tests/test_web_crypto.py`:

```python
import pytest

from casa_certa.web.crypto import DecryptionError, WeakPassphraseError, check_passphrase, decrypt, encrypt

PASSPHRASE = "cavalo nuvem tijolo lampada rio"
FAST = 1000  # iterations: keep the tests quick


def test_a_page_survives_a_round_trip() -> None:
    page = "<html>Casa do Albatroz — R$ 408,34 ✅</html>"
    assert decrypt(encrypt(page, PASSPHRASE, iterations=FAST), PASSPHRASE, iterations=FAST) == page


def test_the_payload_does_not_contain_the_plaintext() -> None:
    assert "Albatroz" not in encrypt("Casa do Albatroz", PASSPHRASE, iterations=FAST)


def test_every_encryption_is_different() -> None:
    assert encrypt("x", PASSPHRASE, iterations=FAST) != encrypt("x", PASSPHRASE, iterations=FAST)


def test_a_wrong_passphrase_fails() -> None:
    payload = encrypt("secret", PASSPHRASE, iterations=FAST)
    with pytest.raises(DecryptionError):
        decrypt(payload, "outra senha qualquer aqui sim", iterations=FAST)


def test_a_modified_payload_fails() -> None:
    payload = encrypt("secret", PASSPHRASE, iterations=FAST)
    tampered = payload[:-6] + ("AAAAAA" if not payload.endswith("AAAAAA") else "BBBBBB")
    with pytest.raises(DecryptionError):
        decrypt(tampered, PASSPHRASE, iterations=FAST)


def test_accents_and_spacing_do_not_change_the_key() -> None:
    payload = encrypt("secret", "  maçã verde céu azul sol  ", iterations=FAST)
    assert decrypt(payload, "maçã verde céu azul sol", iterations=FAST) == "secret"
    assert decrypt(payload, "maçã verde céu azul sol", iterations=FAST) == "secret"  # decomposed


@pytest.mark.parametrize("passphrase", ["casa2026", "a-b-c-d-e", "um dois tres quatro", ""])
def test_weak_passphrases_are_refused(passphrase: str) -> None:
    with pytest.raises(WeakPassphraseError):
        check_passphrase(passphrase)


@pytest.mark.parametrize(
    "passphrase", ["cavalo-nuvem-tijolo-lampada-rio", "cavalo nuvem tijolo lampada rio", "x" * 30]
)
def test_strong_enough_passphrases_are_accepted(passphrase: str) -> None:
    check_passphrase(passphrase)
```

- [ ] **Step 2: Run and watch it fail**, then implement `casa_certa/web/crypto.py`

```python
"""Encrypts the page with a shared passphrase. The browser decrypts it with WebCrypto (see static/unlock.js).

Format: base64(salt[16] + iv[12] + AES-256-GCM ciphertext with its 16-byte tag), key = PBKDF2-HMAC-SHA256.
"""

import base64
import re
import secrets
import unicodedata

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16
IV_BYTES = 12
KEY_BYTES = 32
MIN_WORDS = 5
MIN_WORD_LENGTH = 3
MIN_CHARACTERS = 30


class WeakPassphraseError(ValueError):
    """The passphrase is too easy to guess for a payload whose ciphertext is public."""


class DecryptionError(Exception):
    """Wrong passphrase or modified payload."""


def _normalise(passphrase: str) -> str:
    """The same normalisation the browser applies, so accents and stray spaces cannot change the key."""
    return unicodedata.normalize("NFKC", passphrase.strip())


def check_passphrase(passphrase: str) -> None:
    """A guard against obviously weak choices (not a proof of strength): five words of 3+ letters or 30+ characters."""
    text = _normalise(passphrase)
    words = [word for word in re.split(r"[-\s]+", text) if len(word) >= MIN_WORD_LENGTH]
    if len(words) < MIN_WORDS and len(text) < MIN_CHARACTERS:
        raise WeakPassphraseError(
            f"The site passphrase needs at least {MIN_WORDS} words or {MIN_CHARACTERS} characters. "
            "Run `make passphrase` to generate one."
        )


def _derive_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=salt, iterations=iterations)
    return kdf.derive(_normalise(passphrase).encode("utf8"))


def encrypt(plaintext: str, passphrase: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    iv = secrets.token_bytes(IV_BYTES)
    ciphertext = AESGCM(_derive_key(passphrase, salt, iterations)).encrypt(iv, plaintext.encode("utf8"), None)
    return base64.b64encode(salt + iv + ciphertext).decode("ascii")


def decrypt(payload: str, passphrase: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    raw = base64.b64decode(payload)
    salt, iv, ciphertext = raw[:SALT_BYTES], raw[SALT_BYTES : SALT_BYTES + IV_BYTES], raw[SALT_BYTES + IV_BYTES :]
    try:
        return AESGCM(_derive_key(passphrase, salt, iterations)).decrypt(iv, ciphertext, None).decode("utf8")
    except InvalidTag as error:
        raise DecryptionError("wrong passphrase or modified payload") from error
```

Run: `uv run pytest tests/test_web_crypto.py -q` — Expected: pass.

- [ ] **Step 3: Write the unlock script `casa_certa/web/static/unlock.js`**

```js
"use strict";

(() => {
  const STORAGE_KEY = "casa-certa-passphrase";
  const SALT_BYTES = 16;
  const IV_BYTES = 12;
  const form = document.getElementById("unlock");
  const input = document.getElementById("passphrase");
  const remember = document.getElementById("remember");
  const message = document.getElementById("message");
  const payloadElement = document.getElementById("payload");
  const iterations = Number(payloadElement.dataset.iterations);

  async function decrypt(passphrase) {
    const raw = Uint8Array.from(atob(payloadElement.textContent.trim()), (character) => character.charCodeAt(0));
    const salt = raw.slice(0, SALT_BYTES);
    const iv = raw.slice(SALT_BYTES, SALT_BYTES + IV_BYTES);
    const data = raw.slice(SALT_BYTES + IV_BYTES);
    const secret = new TextEncoder().encode(passphrase.trim().normalize("NFKC"));
    const material = await crypto.subtle.importKey("raw", secret, "PBKDF2", false, ["deriveKey"]);
    const key = await crypto.subtle.deriveKey(
      { name: "PBKDF2", hash: "SHA-256", salt, iterations },
      material,
      { name: "AES-GCM", length: 256 },
      false,
      ["decrypt"],
    );
    return new TextDecoder().decode(await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, data));
  }

  async function unlock(passphrase, store) {
    let html;
    try {
      html = await decrypt(passphrase);
    } catch (error) {
      return false;
    }
    if (store) localStorage.setItem(STORAGE_KEY, passphrase);
    document.open();
    document.write(html);
    document.close();
    return true;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    message.textContent = message.dataset.busy;
    const opened = await unlock(input.value, remember.checked);
    message.textContent = opened ? "" : message.dataset.wrong;
  });

  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved) {
    unlock(saved, false).then((opened) => {
      if (!opened) localStorage.removeItem(STORAGE_KEY);
    });
  }
})();
```

- [ ] **Step 4: Create the shell template `casa_certa/web/templates/unlock.html.j2`**

```jinja
<!doctype html>
<html lang="{{ language }}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{{ labels.site_title }}</title>
<style>
  :root { color-scheme: light dark; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; font: 16px/1.45 system-ui, sans-serif; }
  form { width: min(22rem, 92vw); display: grid; gap: .75rem; }
  h1 { font-size: 1.3rem; margin: 0; }
  input[type="password"], button { font: inherit; padding: .65rem; min-height: 2.75rem; }
  label.check { display: flex; gap: .5rem; align-items: center; }
  #message { min-height: 1.4rem; margin: 0; color: #c00000; }
</style>
</head>
<body>
<form id="unlock">
  <h1>{{ labels.unlock_title }}</h1>
  <input id="passphrase" type="password" autocomplete="current-password" aria-label="{{ labels.passphrase }}" placeholder="{{ labels.passphrase }}" autofocus required>
  <label class="check"><input id="remember" type="checkbox"> {{ labels.remember }}</label>
  <button type="submit">{{ labels.unlock_button }}</button>
  <p id="message" role="alert" data-wrong="{{ labels.wrong_passphrase }}" data-busy="{{ labels.unlocking }}"></p>
</form>
<script type="text/plain" id="payload" data-iterations="{{ iterations }}">{{ payload }}</script>
<script id="unlock-code">{{ code }}</script>
</body>
</html>
```

Add to `casa_certa/web/render.py`:

```python
def render_shell(payload: str, iterations: int, labels: Labels, language: str) -> str:
    """The unencrypted page that holds the ciphertext and the passphrase form."""
    return _environment(labels).get_template("unlock.html.j2").render(
        labels=labels, language=language, payload=payload, iterations=iterations, code=read_static("unlock.js")
    )
```

- [ ] **Step 5: Write the Node runner and the end-to-end tests**

`tests/js/run_unlock.js` (runs the real unlock script against a stub `document`; it is not a `*.test.js` file, pytest drives it):

```js
"use strict";
// Usage: node run_unlock.js <shell.html> <passphrase> [remember]
// Prints { written, message, stored } as JSON after "submitting" the form.
const fs = require("node:fs");
const vm = require("node:vm");

const [shellPath, passphrase, remember] = process.argv.slice(2);
const html = fs.readFileSync(shellPath, "utf8");
const payload = /<script type="text\/plain" id="payload" data-iterations="(\d+)">([^<]*)<\/script>/.exec(html);
const code = /<script id="unlock-code">([\s\S]*?)<\/script>/.exec(html);
if (!payload || !code) throw new Error("not an unlock shell");

const handlers = {};
const element = (extra = {}) => ({
  value: "",
  checked: false,
  textContent: "",
  dataset: {},
  addEventListener(type, handler) {
    handlers[type] = handler;
  },
  ...extra,
});
const elements = {
  unlock: element(),
  passphrase: element({ value: passphrase }),
  remember: element({ checked: remember === "remember" }),
  message: element({ dataset: { wrong: "WRONG", busy: "BUSY" } }),
  payload: element({ textContent: payload[2], dataset: { iterations: payload[1] } }),
};
const written = [];
const storage = new Map();
const sandbox = {
  document: {
    getElementById: (id) => elements[id],
    open() {},
    write: (text) => written.push(text),
    close() {},
  },
  localStorage: {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: (key) => storage.delete(key),
  },
  crypto: globalThis.crypto,
  atob,
  TextEncoder,
  TextDecoder,
  Uint8Array,
  ArrayBuffer,
  Number,
};
vm.runInNewContext(code[1], sandbox);

(async () => {
  await handlers.submit({ preventDefault() {} });
  console.log(JSON.stringify({ written, message: elements.message.textContent, stored: [...storage.keys()] }));
})();
```

`tests/test_web_unlock.py`:

```python
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from casa_certa.i18n import labels_for
from casa_certa.web.crypto import encrypt
from casa_certa.web.render import render_shell

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "tests" / "js" / "run_unlock.js"
PASSPHRASE = "cavalo nuvem tijolo lampada rio"
PAGE = "<!doctype html><html><body>Casa do Albatroz ✅ R$ 408,34</body></html>"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def build_shell(tmp_path: Path, payload: str | None = None) -> Path:
    labels = labels_for("pt-BR")
    shell = render_shell(payload or encrypt(PAGE, PASSPHRASE, iterations=1000), 1000, labels, "pt-BR")
    path = tmp_path / "index.html"
    path.write_text(shell, encoding="utf8")
    return path


def unlock(shell: Path, passphrase: str, remember: bool = False) -> dict[str, object]:
    result = subprocess.run(
        ["node", str(RUNNER), str(shell), passphrase, "remember" if remember else "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    output: dict[str, object] = json.loads(result.stdout)
    return output


def test_the_shell_does_not_contain_the_page(tmp_path: Path) -> None:
    text = build_shell(tmp_path).read_text(encoding="utf8")
    assert "Albatroz" not in text and PASSPHRASE not in text
    assert 'name="robots" content="noindex"' in text


def test_the_right_passphrase_writes_the_decrypted_page(tmp_path: Path) -> None:
    result = unlock(build_shell(tmp_path), PASSPHRASE)
    assert result == {"written": [PAGE], "message": "", "stored": []}


def test_a_passphrase_with_stray_spaces_and_decomposed_accents_still_works(tmp_path: Path) -> None:
    payload = encrypt(PAGE, "maçã verde céu azul sol", iterations=1000)
    result = unlock(build_shell(tmp_path, payload), "  maçã verde céu azul sol ")
    assert result["written"] == [PAGE]


def test_a_wrong_passphrase_shows_the_error_and_writes_nothing(tmp_path: Path) -> None:
    result = unlock(build_shell(tmp_path), "outra senha qualquer aqui sim")
    assert result == {"written": [], "message": "WRONG", "stored": []}


def test_a_modified_payload_is_rejected(tmp_path: Path) -> None:
    payload = encrypt(PAGE, PASSPHRASE, iterations=1000)
    tampered = payload[:-8] + ("AAAAAAAA" if not payload.endswith("AAAAAAAA") else "BBBBBBBB")
    result = unlock(build_shell(tmp_path, tampered), PASSPHRASE)
    assert result["written"] == [] and result["message"] == "WRONG"


def test_the_passphrase_is_stored_only_when_asked(tmp_path: Path) -> None:
    shell = build_shell(tmp_path)
    assert unlock(shell, PASSPHRASE, remember=True)["stored"] == ["casa-certa-passphrase"]
    assert unlock(shell, PASSPHRASE, remember=False)["stored"] == []
```

- [ ] **Step 6: Run everything**

Run: `uv run pytest -q && uv run mypy && uv run ruff check . && uv run ruff format --check .`
Expected: pass. If the Node runner cannot hand `Uint8Array` across the `vm` context, adjust only `run_unlock.js` (for example pass `crypto`/typed arrays differently); the browser script must stay as is.

- [ ] **Step 7: Commit**

```bash
git add casa_certa tests
git commit -m "feat: encrypt the page and add the unlock shell

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Publishing

**Files:**
- Create: `casa_certa/web/publish.py`, `tests/test_web_publish.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_web_publish.py`:

```python
import subprocess
from pathlib import Path

import pytest

from casa_certa.web.publish import PublishError, origin_url, pages_url, publish


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture(autouse=True)
def git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for prefix in ("GIT_AUTHOR", "GIT_COMMITTER"):
        monkeypatch.setenv(f"{prefix}_NAME", "Tester")
        monkeypatch.setenv(f"{prefix}_EMAIL", "tester@example.com")


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    path = tmp_path / "origin.git"
    git("init", "--bare", "-q", str(path))
    return path


@pytest.fixture
def site(tmp_path: Path) -> Path:
    path = tmp_path / "site"
    path.mkdir()
    (path / "index.html").write_text("<html>one</html>", encoding="utf8")
    (path / ".nojekyll").write_text("", encoding="utf8")
    return path


async def test_publish_pushes_one_commit_with_the_site_files(site: Path, remote: Path) -> None:
    await publish(site, str(remote))

    assert git("rev-list", "--count", "gh-pages", cwd=remote) == "1"
    assert git("log", "-1", "--format=%s", "gh-pages", cwd=remote) == "chore: publish site"
    assert sorted(git("ls-tree", "--name-only", "gh-pages", cwd=remote).split()) == [".nojekyll", "index.html"]
    assert git("show", "gh-pages:index.html", cwd=remote) == "<html>one</html>"


async def test_publishing_again_replaces_the_history(site: Path, remote: Path) -> None:
    await publish(site, str(remote))
    (site / "index.html").write_text("<html>two</html>", encoding="utf8")

    await publish(site, str(remote))

    assert git("rev-list", "--count", "gh-pages", cwd=remote) == "1"
    assert git("show", "gh-pages:index.html", cwd=remote) == "<html>two</html>"


async def test_publish_does_not_touch_the_site_directory_or_other_branches(site: Path, remote: Path) -> None:
    await publish(site, str(remote))
    assert git("branch", "--list", cwd=remote).strip("* ") == "gh-pages"
    assert not (site / ".git").exists()


async def test_publish_reports_git_failures(site: Path, tmp_path: Path) -> None:
    with pytest.raises(PublishError, match="push"):
        await publish(site, str(tmp_path / "does-not-exist.git"))


async def test_publish_refuses_a_missing_site(tmp_path: Path, remote: Path) -> None:
    with pytest.raises(PublishError, match="make site"):
        await publish(tmp_path / "nope", str(remote))


async def test_origin_url_reads_the_remote_of_a_repository(tmp_path: Path) -> None:
    git("init", "-q", str(tmp_path / "repo"))
    git("remote", "add", "origin", "git@github.com:someone/repo.git", cwd=tmp_path / "repo")
    assert await origin_url(tmp_path / "repo") == "git@github.com:someone/repo.git"


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("git@github.com:someone/casa-certa.git", "https://someone.github.io/casa-certa/"),
        ("https://github.com/someone/casa-certa.git", "https://someone.github.io/casa-certa/"),
        ("https://github.com/someone/casa-certa", "https://someone.github.io/casa-certa/"),
        ("/tmp/some/local.git", None),
    ],
)
def test_pages_url(origin: str, expected: str | None) -> None:
    assert pages_url(origin) == expected
```

- [ ] **Step 2: Run, watch it fail, then implement `casa_certa/web/publish.py`**

```python
"""Publishes the built site as a single commit on the gh-pages branch, from a temporary repository."""

import asyncio
import re
import shutil
import tempfile
from pathlib import Path

BRANCH = "gh-pages"
COMMIT_MESSAGE = "chore: publish site"
_GITHUB_URL = re.compile(r"(?:git@github\.com:|https://github\.com/)(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")


class PublishError(Exception):
    """A git step failed, or there is nothing to publish."""


async def _git(cwd: Path, *args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "git", *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise PublishError(f"git {args[0]} failed: {stderr.decode().strip()}")
    return stdout.decode().strip()


async def origin_url(repository: Path) -> str:
    return await _git(repository, "remote", "get-url", "origin")


def pages_url(origin: str) -> str | None:
    """The GitHub Pages address for a GitHub remote, or None for any other remote."""
    match = _GITHUB_URL.search(origin)
    if match is None:
        return None
    return f"https://{match['owner']}.github.io/{match['repo']}/"


async def publish(site_dir: Path, origin: str) -> None:
    """Force-push the site files as one orphan commit to the gh-pages branch of `origin`.

    The working tree, `master` and every local branch are untouched: the commit is built in a temporary repository."""
    if not (site_dir / "index.html").is_file():
        raise PublishError(f"No site in {site_dir}: run `make site` first.")
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        await asyncio.to_thread(shutil.copytree, site_dir, work, dirs_exist_ok=True)
        await _git(work, "init", "-q", "-b", BRANCH)
        await _git(work, "add", "--all")
        await _git(work, "commit", "-q", "-m", COMMIT_MESSAGE)
        await _git(work, "push", "--force", origin, f"HEAD:refs/heads/{BRANCH}")
```

- [ ] **Step 3: Run the quality gate**

Run: `uv run pytest -q && uv run mypy && uv run ruff check . && uv run ruff format --check .`
Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add casa_certa/web/publish.py tests/test_web_publish.py
git commit -m "feat: publish the site as one commit on gh-pages

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Passphrase generator, `write_site`, CLI, Makefile and docs

**Files:**
- Create: `casa_certa/web/passphrase.py`, `casa_certa/web/static/wordlist.txt`, `casa_certa/web/static/wordlist.LICENSE.txt`, `casa_certa/web/site.py`, `tests/test_web_site.py`
- Modify: `casa_certa/config.py`, `casa_certa/cli.py`, `tests/test_cli.py`, `Makefile`, `.gitignore`, `.env.example`, `README.md`, `CLAUDE.md`, `docs/superpowers/specs/2026-10-31-static-site-design.md`

- [ ] **Step 1: Vendor the word list**

```bash
curl -s https://www.eff.org/files/2016/07/18/eff_large_wordlist.txt | cut -f2 | grep -v -- '-' > casa_certa/web/static/wordlist.txt
wc -l casa_certa/web/static/wordlist.txt      # about 7772: the few words containing a hyphen are dropped
```

Create `casa_certa/web/static/wordlist.LICENSE.txt`:

```
wordlist.txt is the EFF Long Wordlist (https://www.eff.org/dice), (c) Electronic Frontier Foundation,
licensed under CC BY 3.0 US (https://creativecommons.org/licenses/by/3.0/us/). The dice numbers were removed.
```

- [ ] **Step 2: Write the failing tests for the generator**

`tests/test_web_passphrase.py`:

```python
from casa_certa.web.crypto import check_passphrase
from casa_certa.web.passphrase import WORDS, generate_passphrase


def test_the_word_list_is_large_and_clean() -> None:
    assert len(WORDS) >= 2048
    assert len(set(WORDS)) == len(WORDS)
    assert all(word.isalpha() for word in WORDS)


def test_a_passphrase_has_five_words_from_the_list_and_passes_the_check() -> None:
    passphrase = generate_passphrase()
    words = passphrase.split("-")
    assert len(words) == 5 and all(word in WORDS for word in words)
    check_passphrase(passphrase)


def test_passphrases_differ() -> None:
    assert generate_passphrase() != generate_passphrase()
```

- [ ] **Step 3: Implement `casa_certa/web/passphrase.py`**

```python
"""Generates the shared site passphrase: random words drawn from a vendored word list."""

import secrets
from pathlib import Path

WORD_COUNT = 5
WORDS = tuple((Path(__file__).parent / "static" / "wordlist.txt").read_text(encoding="utf8").split())


def generate_passphrase(words: int = WORD_COUNT) -> str:
    return "-".join(secrets.choice(WORDS) for _ in range(words))
```

Run `uv run pytest tests/test_web_passphrase.py -q` — Expected: pass.

- [ ] **Step 4: Write the failing tests for `write_site`**

`tests/test_web_site.py`:

```python
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from casa_certa.db.models import House
from casa_certa.web.crypto import PBKDF2_ITERATIONS, WeakPassphraseError, decrypt
from casa_certa.web.site import build_site_html, write_site
from tests.fakes import make_trip
from tests.test_xlsx import analysed_house

PASSPHRASE = "cavalo-nuvem-tijolo-lampada-rio"


async def test_write_site_creates_an_encrypted_index_and_nojekyll(
    session: AsyncSession, stored_house: House, tmp_path: Path
) -> None:
    house = await analysed_house(session, stored_house)

    index = write_site([house], make_trip(), PASSPHRASE, tmp_path / "site")

    assert index == tmp_path / "site" / "index.html"
    assert (tmp_path / "site" / ".nojekyll").is_file()
    shell = index.read_text(encoding="utf8")
    assert "Casa do Albatroz" not in shell and "R$" not in shell and PASSPHRASE not in shell
    payload = shell.split('data-iterations="')[1].split(">", 1)[1].split("</script>")[0]
    page = decrypt(payload, PASSPHRASE)
    assert "Casa do Albatroz" in page and "Têm todos os essenciais" in page


async def test_each_build_produces_a_different_file(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)
    assert build_site_html([house], make_trip(), PASSPHRASE) != build_site_html([house], make_trip(), PASSPHRASE)


async def test_a_weak_passphrase_builds_nothing(session: AsyncSession, stored_house: House, tmp_path: Path) -> None:
    house = await analysed_house(session, stored_house)
    with pytest.raises(WeakPassphraseError):
        write_site([house], make_trip(), "casa2026", tmp_path / "site")
    assert not (tmp_path / "site").exists()


def test_the_default_cost_factor_is_the_documented_one() -> None:
    assert PBKDF2_ITERATIONS == 600_000
```

- [ ] **Step 5: Implement `casa_certa/web/site.py`**

```python
"""Builds the encrypted site files from the stored houses."""

from pathlib import Path

from casa_certa.config import TripConfig
from casa_certa.db.models import House
from casa_certa.i18n import labels_for
from casa_certa.web.crypto import PBKDF2_ITERATIONS, check_passphrase, encrypt
from casa_certa.web.model import build_site
from casa_certa.web.render import render_page, render_shell


def build_site_html(houses: list[House], trip: TripConfig, passphrase: str) -> str:
    """The unlock shell holding the encrypted page. Raises WeakPassphraseError before doing any work."""
    check_passphrase(passphrase)
    labels = labels_for(trip.language)
    page = render_page(build_site(houses, trip), trip, labels)
    return render_shell(encrypt(page, passphrase), PBKDF2_ITERATIONS, labels, trip.language)


def write_site(houses: list[House], trip: TripConfig, passphrase: str, output_dir: Path) -> Path:
    html = build_site_html(houses, trip, passphrase)
    output_dir.mkdir(parents=True, exist_ok=True)
    index = output_dir / "index.html"
    index.write_text(html, encoding="utf8")
    (output_dir / ".nojekyll").write_text("", encoding="utf8")  # GitHub Pages: serve the files as they are
    return index
```

Run `uv run pytest tests/test_web_site.py -q` — Expected: pass.

- [ ] **Step 6: Settings and CLI — write the failing CLI tests**

Append to `tests/test_cli.py`:

```python
SITE_TRIP = "urls: [https://www.airbnb.com.br/rooms/1]\ncheck_in: 2026-12-05\ncheck_out: 2026-12-09\n"


def site_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    trip_file = tmp_path / "trip.yaml"
    trip_file.write_text(SITE_TRIP, encoding="utf8")
    monkeypatch.chdir(tmp_path)  # so that no real .env is read
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.delenv("SITE_PASSPHRASE", raising=False)
    return trip_file


def test_parser_reads_the_site_commands() -> None:
    parser = build_parser()
    site = parser.parse_args(["site", "trip.yaml", "--output-dir", "out"])
    assert (site.command, site.trip_file, site.output_dir) == ("site", Path("trip.yaml"), Path("out"))
    assert parser.parse_args(["site", "trip.yaml"]).output_dir == Path("site")
    publish = parser.parse_args(["publish", "--dir", "out", "--yes"])
    assert (publish.command, publish.site_dir, publish.yes) == ("publish", Path("out"), True)
    assert parser.parse_args(["publish"]).site_dir == Path("site")
    assert parser.parse_args(["passphrase"]).command == "passphrase"


def test_passphrase_prints_five_words(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["passphrase"]) == 0
    assert len(capsys.readouterr().out.split()[0].split("-")) == 5


def test_site_without_a_passphrase_explains_how_to_set_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trip_file = site_environment(tmp_path, monkeypatch)
    assert main(["site", str(trip_file)]) == 2
    assert "SITE_PASSPHRASE" in capsys.readouterr().err


def test_site_with_a_weak_passphrase_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trip_file = site_environment(tmp_path, monkeypatch)
    monkeypatch.setenv("SITE_PASSPHRASE", "casa2026")
    assert main(["site", str(trip_file)]) == 2
    assert "passphrase" in capsys.readouterr().err
    assert not (tmp_path / "site").exists()


def test_site_builds_from_an_empty_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trip_file = site_environment(tmp_path, monkeypatch)
    monkeypatch.setenv("SITE_PASSPHRASE", "cavalo-nuvem-tijolo-lampada-rio")

    assert main(["site", str(trip_file)]) == 0

    assert (tmp_path / "site" / "index.html").is_file()
    assert "site/index.html" in capsys.readouterr().out
```

Run: `uv run pytest tests/test_cli.py -q` — Expected: failures (unknown command).

- [ ] **Step 7: Implement the settings and the CLI**

`casa_certa/config.py`: after `DatabaseSettings`, add

```python
class SiteSettings(DatabaseSettings):
    """What `casa-certa site` needs: the database and the shared passphrase (no API keys)."""

    site_passphrase: SecretStr
```

`casa_certa/cli.py`:

1. Imports to add: `from pydantic import ValidationError`; `from casa_certa.config import Settings, SiteSettings`; `from casa_certa.db.repository import HouseRepository`; `from casa_certa.web.passphrase import generate_passphrase`; `from casa_certa.web.publish import PublishError, origin_url, pages_url, publish`; `from casa_certa.web.site import write_site`.
2. In `build_parser`, before `return parser`:

```python
    site = commands.add_parser("site", help="Build the encrypted static site from the stored data (no network)")
    site.add_argument("trip_file", type=Path)
    site.add_argument("--output-dir", type=Path, default=Path("site"), help="Where to write the site")
    publish_command = commands.add_parser("publish", help="Publish the built site to the gh-pages branch of origin")
    publish_command.add_argument("--dir", type=Path, default=Path("site"), dest="site_dir", help="The built site")
    publish_command.add_argument("--yes", action="store_true", help="Do not ask for confirmation")
    commands.add_parser("passphrase", help="Print a new random passphrase for the site")
```

3. New handlers (after `_run`):

```python
async def _site(args: argparse.Namespace) -> int:
    try:
        settings = SiteSettings()
    except ValidationError:
        print("error: set SITE_PASSPHRASE in .env (run `make passphrase` to generate one)", file=sys.stderr)
        return 2
    trip = await YamlConfigSource(args.trip_file).load()
    passphrase = settings.site_passphrase.get_secret_value()
    await asyncio.to_thread(check_passphrase, passphrase)  # refuse a weak one before touching anything
    await asyncio.to_thread(run_migrations)
    engine, session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory() as session:
            houses = await HouseRepository(session).all()
    finally:
        await engine.dispose()
    index = await asyncio.to_thread(write_site, houses, trip, passphrase, args.output_dir)
    print(f"Wrote {index} ({len(trip.urls)} listings). Run `make publish` to put it online.")
    return 0


async def _publish(args: argparse.Namespace) -> int:
    origin = await origin_url(PROJECT_ROOT)
    target = pages_url(origin) or origin
    print(f"This replaces the gh-pages branch of {origin} with the contents of {args.site_dir} (served at {target}).")
    if not args.yes and not await _confirm():
        print("Cancelled.")
        return 1
    await publish(args.site_dir, origin)
    print(f"Published. It can take a minute to appear at {target}")
    return 0


async def _passphrase(args: argparse.Namespace) -> int:
    print(generate_passphrase())
    print("Put it in .env as SITE_PASSPHRASE=<the words above> and share it with the group.", file=sys.stderr)
    return 0
```

(add `from casa_certa.web.crypto import check_passphrase` to the imports.)

4. In `main`, replace `return asyncio.run(_run(args))` with a dispatch and handle the new error:

```python
HANDLERS = {"run": _run, "site": _site, "publish": _publish, "passphrase": _passphrase}
```
(defined at module level after the handlers) and

```python
    try:
        return asyncio.run(HANDLERS[args.command](args))
    except (ConfigError, ValueError) as error:  # WeakPassphraseError is a ValueError
        print(f"error: {error}", file=sys.stderr)
        return 2
    except PublishError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
```

Update the module docstring to `"""Command line entry point: `casa-certa run|site|publish|passphrase`."""`.

Run: `uv run pytest -q && uv run mypy && uv run ruff check . && uv run ruff format --check .` — Expected: pass.

- [ ] **Step 8: Makefile, ignore rules, env example**

`Makefile`: add `site publish passphrase` to the `.PHONY` line and these targets before `migrate`:

```make
site: ## Build the encrypted site from the stored data, no network (TRIP=trip.yaml)
	uv run casa-certa site $(TRIP)

publish: ## Publish the built site to the gh-pages branch of origin
	uv run casa-certa publish

passphrase: ## Print a new random passphrase for SITE_PASSPHRASE
	uv run casa-certa passphrase
```

`.gitignore`: append

```
# The built site (encrypted, but generated: it is published to gh-pages, never committed to master)
site/
```

`.env.example`: append `# SITE_PASSPHRASE=   # required by `make site`; generate one with `make passphrase``.

- [ ] **Step 9: Docs**

`README.md`: after the "Sources and cost" section add:

```markdown
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
to cut someone off, change `SITE_PASSPHRASE` and publish again. Never commit `site/`.
```

`CLAUDE.md`: in the Commands block add `make site / make publish / make passphrase` lines (`# build / publish the encrypted static site`), and add this bullet to Conventions: `- **Site:** the published page is always encrypted (\`SITE_PASSPHRASE\` in \`.env\`); \`site/\` is gitignored and goes only to the \`gh-pages\` branch via \`make publish\`. Never commit a decrypted page.`

Spec: in `docs/superpowers/specs/2026-10-31-static-site-design.md`, replace "missing location gives a fallback label" in the Testing section with "missing location gives no city (shown as n/d)" and replace "`site` needs a read-only repository query for the trip's houses; it never starts the pipeline." with "`site` reads the houses with the existing `HouseRepository.all()` and filters to the trip's listings in `build_site`; it never starts the pipeline."

- [ ] **Step 10: Full verification and commit**

Run: `make check` — Expected: format, lint, mypy and every test pass (the JS tests run if `node` exists).

```bash
git add -A -- casa_certa tests Makefile .gitignore .env.example README.md CLAUDE.md docs pyproject.toml uv.lock
git status --short   # nothing unexpected: no site/, no .db, no .xlsx, no trip.yaml, no lock files
git commit -m "feat: add the site, publish and passphrase commands

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Verify on real data (local only) and final review

- [ ] **Step 1: Re-analyze once for the beach distance.** `make dry-run` must show nothing to fetch from Apify (cost $0.000). Then `make run` (the user's own run, DeepSeek only). If the user has not run it, build the site anyway: distances show as "n/d".
- [ ] **Step 2: Smoke-test the real build without leaving data in the repo.** In the session scratchpad: `SITE_PASSPHRASE=<throw-away passphrase from make passphrase> uv run casa-certa site trip.yaml --output-dir <scratchpad>/site`, then decrypt it with `tests/js/run_unlock.js` and write the decrypted page to `<scratchpad>/page.html`. If a browser tool is available, open it at a 390 px width and check: both sections, every aspect icon visible, filters work, the group-size picker changes the price, the sort order changes. Delete the scratchpad files afterwards (they contain the user's real data).
- [ ] **Step 3: Whole-branch review.** Dispatch a final code reviewer over the full diff against `b556471` (spec compliance: `docs/superpowers/specs/2026-10-31-static-site-design.md`). Fix what it finds.
- [ ] **Step 4: Tell the user** how to enable Pages once, that `master` is ahead of `origin/master` and unpushed, and the open items (re-run `make run` once for distances).
