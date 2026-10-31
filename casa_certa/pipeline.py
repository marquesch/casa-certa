"""Orchestration: decide what to fetch (cache-first), fetch it, persist it, analyze it."""

import asyncio
from dataclasses import dataclass, field

import httpx
from pydantic_ai.exceptions import AgentRunError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from casa_certa.ai.agents import HouseAnalyst, HouseNamer, make_unique_name
from casa_certa.ai.prompts import analysis_instructions, analysis_prompt, prompt_hash
from casa_certa.config import TripConfig
from casa_certa.db.models import House
from casa_certa.db.repository import HouseRepository
from casa_certa.sources.airbnb_page import (
    AirbnbPageError,
    ListingFacts,
    Sleep,
    fetch_listings,
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

# Errors that fail one house but not the whole run.
HOUSE_ERRORS = (AirbnbPageError, ApifyError, AgentRunError, SQLAlchemyError)


@dataclass(frozen=True)
class RunPlan:
    """What a run would fetch. Houses already stored are not listed unless `refresh` was requested."""

    room_ids: list[str]
    fetch_facts: list[str]
    fetch_reviews: list[str]
    fetch_prices: dict[str, list[int]]  # room id -> the group sizes that still need a quote
    review_cap: int
    estimated_cost_usd: float  # from the first source of each chain; fallback sources may add to it

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
    ) -> None:
        self._session_factory = session_factory
        self._http = http_client
        self._price_source = price_source
        self._review_source = review_source
        self._namer = namer
        self._analyst = analyst
        self._page_delay = page_delay
        self._sleep = sleep

    async def plan(self, trip: TripConfig, *, refresh: bool = False) -> RunPlan:
        """Decide what is missing. Reads the database only; never touches the network."""
        room_ids = list(dict.fromkeys(room_id_from_url(url) for url in trip.urls))
        fetch_facts: list[str] = []
        fetch_reviews: list[str] = []
        fetch_prices: dict[str, list[int]] = {}
        async with self._session_factory() as session:
            repo = HouseRepository(session)
            for room_id in room_ids:
                house = await repo.get(room_id)
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
        )

    @staticmethod
    def _missing_sizes(house: House | None, trip: TripConfig, *, refresh: bool) -> list[int]:
        """Group sizes without a stored quote (unavailable answers count as stored). A new house, whose capacity is
        unknown, or a refresh wants every size; the run drops those above the capacity once the facts are stored."""
        if house is None or refresh:
            return list(trip.group_sizes())
        stored = house.quotes_by_guests(trip)
        capacity = house.facts.person_capacity if house.facts else None
        return [size for size in trip.group_sizes() if size not in stored and (capacity is None or size <= capacity)]

    async def run(self, trip: TripConfig, *, refresh: bool = False) -> RunReport:
        """Free page fetch, then store facts, then pay Apify only for houses we can store, then store the rest."""
        plan = await self.plan(trip, refresh=refresh)
        failures: dict[str, str] = {}
        warnings: dict[str, list[str]] = {}
        pages: dict[str, ListingFacts | AirbnbPageError] = {}
        if plan.fetch_facts:
            pages = await fetch_listings(plan.fetch_facts, client=self._http, delay=self._page_delay, sleep=self._sleep)
        await self._store_all_facts(trip, plan, pages, failures, warnings)
        in_play = [room_id for room_id in plan.room_ids if room_id not in failures]
        reviews = await self._fetch_reviews([r for r in plan.fetch_reviews if r in in_play], trip, warnings)
        requests = await self._price_requests(plan.fetch_prices, in_play)
        prices = await self._fetch_prices(requests, trip, warnings)
        notes = self._house_notes(trip, warnings)
        houses = await self._store_extras_and_analyze(
            trip, in_play, reviews, prices, notes, failures=failures, warnings=warnings
        )
        return RunReport(plan=plan, houses=houses, failures=failures, warnings=warnings)

    async def _store_all_facts(
        self,
        trip: TripConfig,
        plan: RunPlan,
        pages: dict[str, ListingFacts | AirbnbPageError],
        failures: dict[str, str],
        warnings: dict[str, list[str]],
    ) -> None:
        """Phase 2: name, create and commit each house on its own; a failure drops only that house from the run."""
        async with self._session_factory() as session:
            repo = HouseRepository(session)
            for room_id in plan.fetch_facts:
                messages: list[str] = []
                try:
                    await self._store_facts(repo, trip, room_id, pages[room_id], messages)
                    await repo.commit()
                except HOUSE_ERRORS as error:
                    await repo.rollback()
                    failures[room_id] = str(error)
                    continue
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
        async with self._session_factory() as session:
            repo = HouseRepository(session)
            for room_id in room_ids:
                messages: list[str] = []
                try:
                    house = await self._store_extras(
                        repo, trip, room_id, reviews.get(room_id), prices.get(room_id, {}), messages
                    )
                    if room_id in notes and house.extra != notes[room_id]:
                        house.extra = notes[room_id]
                    await repo.commit()  # keep paid data even if the analysis below fails
                    await self._analyze_if_stale(repo, trip, house)
                except HOUSE_ERRORS as error:
                    await repo.rollback()
                    failures[room_id] = str(error)
                    continue
                if messages:
                    warnings.setdefault(room_id, []).extend(messages)
            for room_id in room_ids:
                if room_id in failures:
                    continue
                stored = await repo.get(room_id)
                if stored is not None:
                    houses.append(stored)
        return houses

    async def _fetch_reviews(
        self, room_ids: list[str], trip: TripConfig, warnings: dict[str, list[str]]
    ) -> dict[str, list[ReviewData] | ApifyError]:
        if not room_ids:
            return {}
        expected = await self._expected_reviews(room_ids)
        try:
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
    ) -> None:
        """Create the house if it is new and save its facts. A failed refresh of a known house only warns."""
        house = await repo.get(room_id)
        if isinstance(page, AirbnbPageError):
            if house is None:
                raise page
            warnings.append(f"could not refresh the listing facts: {page}")
        else:
            if house is None:
                suggested = (await self._namer.name(page, trip.language)).strip()
                suggested = suggested or (page.title or "").strip() or f"Casa {room_id}"
                house = await repo.add(room_id, make_unique_name(suggested, await repo.names()))
            await repo.save_facts(house, page)

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

    async def _analyze_if_stale(self, repo: HouseRepository, trip: TripConfig, house: House) -> None:
        """Run the analyst unless the stored analysis was made from the exact same prompt."""
        instructions = analysis_instructions(trip)
        prompt = analysis_prompt(house, trip)
        digest = prompt_hash(instructions, prompt)
        if house.analysis is None or house.analysis.input_hash != digest:
            analysis = await self._analyst.analyze(prompt, instructions, [aspect.name for aspect in trip.aspects])
            await repo.save_analysis(house, digest, self._analyst.model_name, analysis)
            await repo.commit()
