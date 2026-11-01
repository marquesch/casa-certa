"""Orchestration: decide what to fetch (cache-first), fetch it, persist it, analyze it."""

import asyncio
import logging
import time
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field

import httpx
from pydantic_ai.exceptions import AgentRunError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from casa_certa.ai.agents import HouseAnalyst, HouseNamer, make_unique_name
from casa_certa.ai.prompts import analysis_instructions, analysis_prompt, must_haves, prompt_hash, screen_hash
from casa_certa.ai.schemas import ScreenOutput
from casa_certa.ai.screener import HouseScreener
from casa_certa.config import TripConfig
from casa_certa.costs import CostTracker
from casa_certa.db.models import House
from casa_certa.db.repository import HouseRepository, SearchRepository
from casa_certa.enums import ScreenStatus
from casa_certa.sources.airbnb_page import (
    AirbnbPageError,
    ListingFacts,
    Sleep,
    fetch_listings,
    listing_url,
    room_id_from_url,
)
from casa_certa.sources.apify import (
    ApifyError,
    PriceData,
    ReviewData,
)
from casa_certa.sources.protocols import PriceSource, ReviewSource, SourceBatch

# Price runs in flight at once; below the free Apify plan's limit of 5 concurrent actor jobs.
MAX_CONCURRENT_PRICE_RUNS = 3

log = logging.getLogger(__name__)

# Errors that fail one house but not the whole run.
HOUSE_ERRORS = (AirbnbPageError, ApifyError, AgentRunError, SQLAlchemyError)


@dataclass(frozen=True)
class RoomRef:
    """A house to process. `screen` is True for a search hit, which must pass the must-haves before any paid step."""

    room_id: str
    screen: bool


def rooms_from(trip: TripConfig, hit_ids: list[str]) -> list[RoomRef]:
    """The yaml urls first (never screened), then the search hits not already listed; each house once."""
    listed = dict.fromkeys(room_id_from_url(url) for url in trip.urls)
    hits = [room_id for room_id in dict.fromkeys(hit_ids) if room_id not in listed]
    return [RoomRef(room_id, screen=False) for room_id in listed] + [RoomRef(room_id, screen=True) for room_id in hits]


@dataclass(frozen=True)
class RunPlan:
    """What a run would fetch. Houses already stored are not listed unless `refresh` was requested."""

    room_ids: list[str]  # every house in play, without the stored screen failures that are skipped
    fetch_facts: list[str]
    fetch_reviews: list[str]
    fetch_prices: dict[str, list[int]]  # room id -> the group sizes that still need a quote
    review_cap: int
    estimated_cost_usd: float  # assumes every hit to screen passes; fallback sources may add to it
    screen_rooms: list[str] = field(default_factory=list)  # search hits whose page will be screened
    skipped_screened: list[str] = field(default_factory=list)  # hits that failed the same must-haves before

    @property
    def price_quotes(self) -> int:
        return sum(len(sizes) for sizes in self.fetch_prices.values())

    @property
    def is_empty(self) -> bool:
        return not (self.fetch_facts or self.fetch_reviews or self.fetch_prices)


@dataclass
class RunReport:
    """The outcome of a run; `failures` and `warnings` are keyed by Airbnb room id."""

    plan: RunPlan
    houses: list[House]
    failures: dict[str, str]
    warnings: dict[str, list[str]] = field(default_factory=dict)
    screened_out: dict[str, str] = field(default_factory=dict)  # room id -> the reason of the first failed must-have


class Pipeline:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        http_client: httpx.AsyncClient,
        price_source: PriceSource,
        review_source: ReviewSource,
        namer: HouseNamer,
        analyst: HouseAnalyst,
        page_delay: tuple[float, float] = (2.0, 5.0),
        sleep: Sleep = asyncio.sleep,
        screener: HouseScreener | None = None,
        tracker: CostTracker | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._http = http_client
        self._price_source = price_source
        self._review_source = review_source
        self._namer = namer
        self._analyst = analyst
        self._page_delay = page_delay
        self._sleep = sleep
        self._screener = screener
        self._tracker = tracker

    async def plan(self, trip: TripConfig, rooms: list[RoomRef], *, refresh: bool = False) -> RunPlan:
        """Decide what is missing. Reads the database only; never touches the network."""
        screening = self._screener is not None and bool(must_haves(trip))
        digest = screen_hash(must_haves(trip))
        room_ids: list[str] = []
        fetch_facts: list[str] = []
        fetch_reviews: list[str] = []
        fetch_prices: dict[str, list[int]] = {}
        screen_rooms: list[str] = []
        skipped_screened: list[str] = []
        async with self._session_factory() as session:
            repo = HouseRepository(session)
            searches = SearchRepository(session)
            for room_id, is_hit in self._screen_flags(rooms).items():
                house = await repo.get(room_id)
                if house is None and is_hit and screening:  # a stored house is never screened
                    stored = await searches.get_screen(room_id)
                    if stored is None or stored.input_hash != digest or refresh:
                        screen_rooms.append(room_id)
                    elif not stored.passed:  # failed these same must-haves before: not even the page is fetched
                        skipped_screened.append(room_id)
                        continue
                room_ids.append(room_id)
                if house is None or refresh:
                    fetch_facts.append(room_id)
                # A house that truly has zero reviews is asked again on every run: that is nearly free.
                if trip.review_cap > 0 and (house is None or refresh or not house.reviews):
                    fetch_reviews.append(room_id)
                if sizes := self._missing_sizes(house, trip, refresh=refresh):
                    fetch_prices[room_id] = sizes
        quotes = sum(len(sizes) for sizes in fetch_prices.values())
        cost = self._price_source.estimate_usd(quotes) if quotes else 0.0
        if fetch_reviews:  # review actors have a start fee, which is not paid when no run is made
            cost += self._review_source.estimate_usd(len(fetch_reviews), trip.review_cap)
        return RunPlan(
            room_ids=room_ids,
            fetch_facts=fetch_facts,
            fetch_reviews=fetch_reviews,
            fetch_prices=fetch_prices,
            review_cap=trip.review_cap,
            estimated_cost_usd=cost,
            screen_rooms=screen_rooms,
            skipped_screened=skipped_screened,
        )

    @staticmethod
    def _screen_flags(rooms: list[RoomRef]) -> dict[str, bool]:
        """Each room once, in order; a room listed both ways is not screened (the user chose it)."""
        flags: dict[str, bool] = {}
        for room in rooms:
            flags[room.room_id] = flags.get(room.room_id, True) and room.screen
        return flags

    @staticmethod
    def _missing_sizes(house: House | None, trip: TripConfig, *, refresh: bool) -> list[int]:
        """Group sizes without a stored quote (unavailable answers count as stored). A new house, whose capacity is
        unknown, or a refresh wants every size; the run drops those above the capacity once the facts are stored."""
        if house is None or refresh:
            return list(trip.group_sizes())
        stored = house.quotes_by_guests(trip)
        capacity = house.facts.person_capacity if house.facts else None
        return [size for size in trip.group_sizes() if size not in stored and (capacity is None or size <= capacity)]

    async def run(self, trip: TripConfig, rooms: list[RoomRef], *, refresh: bool = False) -> RunReport:
        """Free page fetch, screen the hits, store facts, pay Apify only for houses we can store, then the rest.

        Screened-out houses never reach a paid step and get no `house` row."""
        plan = await self.plan(trip, rooms, refresh=refresh)
        failures: dict[str, str] = {}
        warnings: dict[str, list[str]] = {}
        pages: dict[str, ListingFacts | AirbnbPageError] = {}
        if plan.fetch_facts:
            log.info("fetching %d listing pages (free; paced to be polite)", len(plan.fetch_facts))
            pages = await fetch_listings(plan.fetch_facts, client=self._http, delay=self._page_delay, sleep=self._sleep)
        screened_out = await self._screen_all(trip, plan.screen_rooms, pages, failures)
        dropped = failures.keys() | screened_out.keys()
        await self._store_all_facts(trip, [r for r in plan.fetch_facts if r not in dropped], pages, failures, warnings)
        in_play = [room_id for room_id in plan.room_ids if room_id not in failures and room_id not in screened_out]
        reviews = await self._fetch_reviews([r for r in plan.fetch_reviews if r in in_play], trip, warnings)
        requests = await self._price_requests(plan.fetch_prices, in_play)
        prices = await self._fetch_prices(requests, trip, warnings)
        notes = self._house_notes(trip, warnings)
        houses = await self._store_extras_and_analyze(
            trip, in_play, reviews, prices, notes, failures=failures, warnings=warnings
        )
        return RunReport(plan=plan, houses=houses, failures=failures, warnings=warnings, screened_out=screened_out)

    async def _screen_all(
        self,
        trip: TripConfig,
        room_ids: list[str],
        pages: dict[str, ListingFacts | AirbnbPageError],
        failures: dict[str, str],
    ) -> dict[str, str]:
        """Phase 1b: judge each hit's free listing text against the must-haves and store the result.

        Returns the screened-out houses with their reason. A page that could not be fetched is left to phase 2,
        which fails the house; a screening error fails the house without storing a result, so it is tried again."""
        screener = self._screener
        if screener is None or not room_ids:
            return {}
        digest = screen_hash(must_haves(trip))
        screened_out: dict[str, str] = {}
        total = len(room_ids)
        log.info("screening %d searched houses against the must-haves with %s", total, screener.model_name)
        async with self._session_factory() as session:
            searches = SearchRepository(session)
            for index, room_id in enumerate(room_ids, start=1):
                page = pages.get(room_id)
                if not isinstance(page, ListingFacts):
                    continue
                try:
                    with self._house_costs(room_id):
                        screen = await screener.screen(page, trip)
                    verdicts = [(v.aspect, v.status, v.reason, v.quote) for v in screen.verdicts]
                    await searches.save_screen(
                        room_id, listing_url(room_id), page.title, digest, screener.model_name, screen.passed, verdicts
                    )
                    await session.commit()
                except HOUSE_ERRORS as error:
                    await session.rollback()
                    failures[room_id] = str(error)
                    self._log_house(logging.WARNING, room_id, room_id, index, total, f"failed ({error})")
                    continue
                if not screen.passed:
                    screened_out[room_id] = self._screen_reason(screen)
                    label = (page.title or "").strip() or room_id
                    self._log_house(
                        logging.INFO, room_id, label, index, total, f"screened out: {screened_out[room_id]}"
                    )
        return screened_out

    @staticmethod
    def _screen_reason(screen: ScreenOutput) -> str:
        failed = next(v for v in screen.verdicts if v.status is ScreenStatus.FAILS)
        return f'{failed.reason} ("{failed.quote}")' if failed.quote else failed.reason

    async def _store_all_facts(
        self,
        trip: TripConfig,
        room_ids: list[str],
        pages: dict[str, ListingFacts | AirbnbPageError],
        failures: dict[str, str],
        warnings: dict[str, list[str]],
    ) -> None:
        """Phase 2: name, create and commit each house on its own; a failure drops only that house from the run."""
        async with self._session_factory() as session:
            repo = HouseRepository(session)
            total = len(room_ids)
            for index, room_id in enumerate(room_ids, start=1):
                messages: list[str] = []
                try:
                    with self._house_costs(room_id):
                        house = await self._store_facts(repo, trip, room_id, pages[room_id], messages)
                    await repo.commit()
                except HOUSE_ERRORS as error:
                    await repo.rollback()
                    failures[room_id] = str(error)
                    self._log_house(logging.WARNING, room_id, room_id, index, total, f"failed ({error})")
                    continue
                log.info("[%d/%d] %s (%s): listing saved", index, total, house.name, room_id)
                if messages:
                    warnings.setdefault(room_id, []).extend(messages)

    async def _store_extras_and_analyze(
        self,
        trip: TripConfig,
        room_ids: list[str],
        reviews: dict[str, list[ReviewData] | ApifyError],
        prices: dict[str, dict[int, PriceData | ApifyError]],
        notes: dict[str, str],
        *,
        failures: dict[str, str],
        warnings: dict[str, list[str]],
    ) -> list[House]:
        """Phase 4: save reviews and prices, commit, then analyse. Returns the houses that made it."""
        houses: list[House] = []
        total = len(room_ids)
        log.info("saving reviews and prices, then analysing %d houses with %s", total, self._analyst.model_name)
        async with self._session_factory() as session:
            repo = HouseRepository(session)
            for index, room_id in enumerate(room_ids, start=1):
                messages: list[str] = []
                try:
                    house = await self._store_extras(
                        repo, trip, room_id, reviews.get(room_id), prices.get(room_id, {}), messages
                    )
                    if room_id in notes and house.extra != notes[room_id]:
                        house.extra = notes[room_id]
                    await repo.commit()  # keep paid data even if the analysis below fails
                    with self._house_costs(room_id):
                        analysis_seconds = await self._analyze_if_stale(repo, trip, house)
                except HOUSE_ERRORS as error:
                    await repo.rollback()
                    failures[room_id] = str(error)
                    self._log_house(logging.WARNING, room_id, room_id, index, total, f"failed ({error})")
                    continue
                bought = room_id in reviews or room_id in prices
                if analysis_seconds is None and not bought and self._tracker is not None:
                    status = "cached"  # nothing was paid for this house in this run
                else:
                    analysis = "cached" if analysis_seconds is None else f"{analysis_seconds:.0f}s"
                    counts = f"{len(house.reviews)} reviews, {len(house.quotes_by_guests(trip))} price quotes"
                    status = f"done - {counts}, analysis {analysis}"
                self._log_house(logging.INFO, room_id, house.name, index, total, status)
                if messages:
                    warnings.setdefault(room_id, []).extend(messages)
            for room_id in room_ids:
                if room_id in failures:
                    continue
                stored = await repo.get(room_id)
                if stored is not None:
                    houses.append(stored)
        return houses

    def _house_costs(self, room_id: str) -> AbstractContextManager[None]:
        """Attribute the LLM usage inside to this house."""
        return self._tracker.house(room_id) if self._tracker is not None else nullcontext()

    def _batch_costs(self, room_ids: list[str], estimate_usd: float) -> AbstractContextManager[None]:
        """Split the Apify cost of the one source call inside among these houses."""
        return self._tracker.batch(room_ids, estimate_usd) if self._tracker is not None and room_ids else nullcontext()

    def _log_house(self, level: int, room_id: str, name: str, index: int, total: int, status: str) -> None:
        """The one line logged when a house is done (or failed), with its cost when a tracker is present."""
        if self._tracker is not None:
            log.log(level, "%s", self._tracker.line(room_id, name, index, total, status))
        else:
            label = room_id if name == room_id else f"{name} ({room_id})"
            log.log(level, "[%d/%d] %s: %s", index, total, label, status)

    async def _fetch_reviews(
        self, room_ids: list[str], trip: TripConfig, warnings: dict[str, list[str]]
    ) -> dict[str, list[ReviewData] | ApifyError]:
        if not room_ids:
            return {}
        expected = await self._expected_reviews(room_ids)
        estimate = self._review_source.estimate_usd(len(room_ids), trip.review_cap)
        try:
            with self._batch_costs(room_ids, estimate):
                batch = await self._review_source.fetch_reviews(room_ids, trip, expect_reviews=expected)
        except ApifyError as error:
            return dict.fromkeys(room_ids, error)
        for room_id, note in batch.notes.items():
            warnings.setdefault(room_id, []).append(note)
        return dict(batch.results)

    async def _expected_reviews(self, room_ids: list[str]) -> frozenset[str]:
        """The houses whose stored facts say they have reviews; an unknown count means none are expected."""
        expected: set[str] = set()
        async with self._session_factory() as session:
            repo = HouseRepository(session)
            for room_id in room_ids:
                house = await repo.get(room_id)
                if house and house.facts and (house.facts.review_count or 0) > 0:
                    expected.add(room_id)
        return frozenset(expected)

    async def _price_requests(self, wanted: dict[str, list[int]], room_ids: list[str]) -> dict[int, list[str]]:
        """The houses to quote for each group size, without the sizes above a house's stored capacity."""
        requests: dict[int, list[str]] = {}
        async with self._session_factory() as session:
            repo = HouseRepository(session)
            for room_id in room_ids:
                house = await repo.get(room_id) if room_id in wanted else None
                capacity = house.facts.person_capacity if house and house.facts else None
                for size in wanted.get(room_id, []):
                    if capacity is None or size <= capacity:
                        requests.setdefault(size, []).append(room_id)
        return requests

    async def _fetch_prices(
        self, requests: dict[int, list[str]], trip: TripConfig, warnings: dict[str, list[str]]
    ) -> dict[str, dict[int, PriceData | ApifyError]]:
        """One actor run per group size, a few at a time. A failed run is the same error for each house in it."""
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_PRICE_RUNS)

        async def quote(size: int, room_ids: list[str]) -> tuple[int, SourceBatch[PriceData]]:
            async with semaphore:
                try:
                    with self._batch_costs(room_ids, self._price_source.estimate_usd(len(room_ids))):
                        return size, await self._price_source.fetch_prices(room_ids, trip, size)
                except ApifyError as error:
                    return size, SourceBatch(dict.fromkeys(room_ids, error))

        prices: dict[str, dict[int, PriceData | ApifyError]] = {}
        noted_sizes: dict[str, dict[str, list[int]]] = {}  # room id -> note -> the sizes it applies to
        for size, batch in await asyncio.gather(*(quote(size, room_ids) for size, room_ids in requests.items())):
            for room_id, result in batch.results.items():
                prices.setdefault(room_id, {})[size] = result
            for room_id, note in batch.notes.items():
                noted_sizes.setdefault(room_id, {}).setdefault(note, []).append(size)
        for room_id, by_note in noted_sizes.items():
            for note, sizes in by_note.items():
                warnings.setdefault(room_id, []).append(f"{', '.join(map(str, sorted(sizes)))} guests: {note}")
        return prices

    async def _store_facts(
        self,
        repo: HouseRepository,
        trip: TripConfig,
        room_id: str,
        page: ListingFacts | AirbnbPageError,
        warnings: list[str],
    ) -> House:
        """Create the house if it is new and save its facts. A failed refresh of a known house only warns."""
        house = await repo.get(room_id)
        if isinstance(page, AirbnbPageError):
            if house is None:
                raise page
            warnings.append(f"could not refresh the listing facts: {page}")
            return house
        if house is None:
            suggested = (await self._namer.name(page, trip.language)).strip()
            suggested = suggested or (page.title or "").strip() or f"Casa {room_id}"
            house = await repo.add(room_id, make_unique_name(suggested, await repo.names()))
        await repo.save_facts(house, page)
        return house

    @staticmethod
    def _house_notes(trip: TripConfig, warnings: dict[str, list[str]]) -> dict[str, str]:
        """The trip notes keyed by room id; keys that are not a listing URL or id become warnings."""
        notes: dict[str, str] = {}
        for key, note in trip.notes.items():
            try:
                notes[room_id_from_url(key)] = note.strip()
            except ValueError:
                warnings.setdefault(key, []).append("ignored note: the key is not an Airbnb listing URL or id")
        return notes

    async def _store_extras(
        self,
        repo: HouseRepository,
        trip: TripConfig,
        room_id: str,
        reviews: list[ReviewData] | ApifyError | None,
        prices: dict[int, PriceData | ApifyError],
        warnings: list[str],
    ) -> House:
        """Save reviews and the price quotes; fetch errors become warnings."""
        house = await repo.get(room_id)
        if house is None:  # facts are committed in phase 2 for every house that is not stored yet
            raise RuntimeError(f"No stored data for listing {room_id}")
        if isinstance(reviews, ApifyError):
            warnings.append(f"could not fetch reviews: {reviews}")
        elif reviews is not None:
            await repo.save_reviews(house, reviews)
        for size, price in sorted(prices.items()):
            if isinstance(price, ApifyError):
                warnings.append(f"could not fetch the price for {size} guests: {price}")
            else:
                await repo.save_quote(house, trip, size, price)
        return house

    async def _analyze_if_stale(self, repo: HouseRepository, trip: TripConfig, house: House) -> float | None:
        """Run the analyst unless the stored analysis was made from the exact same prompt.

        Returns the seconds the analysis took, or None when the stored one was reused."""
        instructions = analysis_instructions(trip)
        prompt = analysis_prompt(house, trip)
        digest = prompt_hash(instructions, prompt)
        if house.analysis is not None and house.analysis.input_hash == digest:
            return None
        log.info("%s: analysing with %s (this is the slow step)...", house.name, self._analyst.model_name)
        started = time.monotonic()
        analysis = await self._analyst.analyze(prompt, instructions, [aspect.name for aspect in trip.aspects])
        await repo.save_analysis(house, digest, self._analyst.model_name, analysis)
        await repo.commit()
        return time.monotonic() - started
