import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from casa_certa.ai.agents import HouseAnalyst, HouseNamer
from casa_certa.ai.schemas import ScreenOutput, ScreenVerdictOut
from casa_certa.ai.screener import HouseScreener
from casa_certa.config import TripConfig
from casa_certa.costs import CostTracker
from casa_certa.db.repository import HouseRepository, SearchRepository
from casa_certa.enums import ScreenStatus
from casa_certa.pipeline import Pipeline, RoomRef, RunPlan, RunReport, rooms_from
from casa_certa.sources.airbnb_page import listing_url
from casa_certa.sources.apify import ApifyError, PriceData, ReviewData
from casa_certa.sources.fallback import FallbackPriceSource
from casa_certa.sources.price_sources import ThelastscraperPrice, TriAnglePrice
from casa_certa.sources.protocols import PriceSource, ReviewSource, SourceBatch
from casa_certa.sources.registry import build_price_source, build_review_source
from casa_certa.sources.review_sources import KaixReviews, SteadyscrapeReviews, TriAngleReviews
from tests.fakes import (
    ITAPOA_TOTALS,
    FakeRunner,
    answer_prices,
    load_html,
    load_json,
    make_trip,
    priced_item,
    sample_analysis,
)

PRICE_ACTOR = TriAnglePrice.ACTOR
REVIEWS_ACTOR = TriAngleReviews.ACTOR
PAGES = {"46397403": "room_46397403.html", "52584351": "room_52584351.html"}
SIZES = [9, 10, 11, 12, 13]  # the trip's group sizes; Albatroz (capacity 12) is never asked about 13
SCREEN_REASON = "Fica a 3 km do mar"
SCREEN_QUOTE = "3 km da praia"


@dataclass
class World:
    """The pipeline plus every fake it talks to, so tests can count what was fetched."""

    pipeline: Pipeline
    runner: FakeRunner
    blocked: set[str] = field(default_factory=set)
    page_requests: list[str] = field(default_factory=list)
    namer_calls: list[str] = field(default_factory=list)
    analyst_calls: list[str] = field(default_factory=list)
    screen_calls: list[str] = field(default_factory=list)  # room ids, in order
    screen_statuses: dict[str, ScreenStatus] = field(default_factory=dict)  # room id -> verdict (default: meets)
    screen_errors: set[str] = field(default_factory=set)  # room ids whose screening raises

    async def run(self, trip: TripConfig, hits: list[str] | None = None, *, refresh: bool = False) -> RunReport:
        return await self.pipeline.run(trip, rooms_from(trip, hits or []), refresh=refresh)

    async def plan(self, trip: TripConfig, hits: list[str] | None = None, *, refresh: bool = False) -> RunPlan:
        return await self.pipeline.plan(trip, rooms_from(trip, hits or []), refresh=refresh)


async def no_sleep(seconds: float) -> None:
    return None


def make_world(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    blocked: frozenset[str] = frozenset(),
    analyst_fails: bool = False,
    namer_reply: str | None = None,
    namer_fails: bool = False,
    fail_once: set[str] | None = None,
    price_source: Callable[[FakeRunner], PriceSource] | None = None,
    review_source: Callable[[FakeRunner], ReviewSource] | None = None,
    tracker: CostTracker | None = None,
    screener: bool = True,
) -> World:
    reviews = load_json("apify_reviews_46397403.json")
    other_reviews = [
        {**item, "id": f"other-{item['id']}", "startUrl": listing_url("52584351")} for item in reviews
    ]  # every house has reviews, otherwise it is (rightly) asked again on each run
    world_runner = FakeRunner(
        {
            PRICE_ACTOR: answer_prices,
            REVIEWS_ACTOR: reviews + other_reviews,
        },
        fail_once=fail_once,
    )
    blocked_ids = set(blocked)
    page_requests: list[str] = []
    namer_calls: list[str] = []
    analyst_calls: list[str] = []
    screen_calls: list[str] = []
    screen_statuses: dict[str, ScreenStatus] = {}
    screen_errors: set[str] = set()

    def serve_page(request: httpx.Request) -> httpx.Response:
        room_id = request.url.path.rsplit("/", 1)[-1]
        page_requests.append(room_id)
        if room_id in blocked_ids:
            return httpx.Response(403)
        return httpx.Response(200, text=load_html(PAGES[room_id]))

    def name_house(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        namer_calls.append(str(messages))
        if namer_fails:
            raise UnexpectedModelBehavior("the namer is down")
        name = namer_reply or ("Casa do Albatroz" if "Albatroz" in str(messages) else "Casa do Campo")
        return ModelResponse(parts=[TextPart(f'{{"name": "{name}"}}')])

    def analyse_house(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        analyst_calls.append(str(messages))
        if analyst_fails:
            raise UnexpectedModelBehavior("the model returned garbage")
        return ModelResponse(parts=[TextPart(sample_analysis(["Perto da praia", "Piscina"]).model_dump_json())])

    def screen_house(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        room_id = "46397403" if "Albatroz" in str(messages) else "52584351"
        screen_calls.append(room_id)
        if room_id in screen_errors:
            raise UnexpectedModelBehavior("the screener is down")
        status = screen_statuses.get(room_id, ScreenStatus.MEETS)
        verdict = ScreenVerdictOut(aspect="Perto da praia", status=status, reason=SCREEN_REASON, quote=SCREEN_QUOTE)
        return ModelResponse(parts=[TextPart(ScreenOutput(verdicts=[verdict]).model_dump_json())])

    pipeline = Pipeline(
        session_factory=session_factory,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(serve_page)),
        price_source=price_source(world_runner) if price_source else TriAnglePrice(world_runner),
        review_source=review_source(world_runner) if review_source else TriAngleReviews(world_runner),
        namer=HouseNamer(FunctionModel(name_house), usage_sink=tracker),
        analyst=HouseAnalyst(FunctionModel(analyse_house), usage_sink=tracker),
        page_delay=(0, 0),
        sleep=no_sleep,
        screener=HouseScreener(FunctionModel(screen_house), usage_sink=tracker) if screener else None,
        tracker=tracker,
    )
    return World(
        pipeline,
        world_runner,
        blocked_ids,
        page_requests,
        namer_calls,
        analyst_calls,
        screen_calls,
        screen_statuses,
        screen_errors,
    )


async def test_plan_before_the_first_run_lists_everything_and_estimates_cost(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(
        session_factory,
        price_source=lambda runner: build_price_source(["thelastscraper", "tri_angle"], runner),
        review_source=lambda runner: build_review_source(["kaix", "tri_angle"], runner),
    )
    plan = await world.plan(make_trip())

    assert plan.fetch_facts == plan.fetch_reviews == ["46397403", "52584351"]
    assert plan.fetch_prices == {"46397403": SIZES, "52584351": SIZES}  # capacity is unknown before the first run
    # only the first source of each chain counts: 10 quotes x $0.0015 + kaix for 2 houses of 30 reviews
    assert plan.estimated_cost_usd == pytest.approx(10 * 0.0015 + 0.00005 + 2 * (0.0001 + 30 * 0.00002))
    assert world.page_requests == []  # planning never touches the network


async def test_first_run_creates_named_houses_with_prices_reviews_and_analysis(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory)
    trip: TripConfig = make_trip()

    report = await world.run(trip)

    assert report.failures == {}
    assert [h.name for h in report.houses] == ["Casa do Albatroz", "Casa do Campo"]
    albatroz = report.houses[0]
    assert albatroz.facts is not None
    assert albatroz.facts.person_capacity == 12
    assert len(albatroz.reviews) == 4
    assert {size: q.total_cents for size, q in albatroz.quotes_by_guests(trip).items()} == dict.fromkeys(
        [9, 10, 11, 12], 367500
    )  # 13 exceeds the capacity of 12: never asked
    itapoa = report.houses[1]
    assert {size: (q.total_cents, q.original_total_cents) for size, q in itapoa.quotes_by_guests(trip).items()} == (
        ITAPOA_TOTALS
    )
    assert {q.source for q in itapoa.quotes} == {"tri_angle"}
    assert albatroz.analysis is not None
    assert albatroz.analysis.input_hash
    assert sorted(run_input["adults"] for run_input in world.runner.calls_to(PRICE_ACTOR)) == SIZES  # one run per size
    assert len(world.runner.calls_to(REVIEWS_ACTOR)) == 1
    assert world.page_requests == ["46397403", "52584351"]


async def test_second_run_fetches_and_analyses_nothing(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory)
    trip = make_trip()
    await world.run(trip)
    calls_before = (len(world.runner.calls), len(world.page_requests), len(world.namer_calls), len(world.analyst_calls))

    report = await world.run(trip)

    assert (len(world.runner.calls), len(world.page_requests), len(world.namer_calls), len(world.analyst_calls)) == (
        calls_before
    )
    assert [h.name for h in report.houses] == ["Casa do Albatroz", "Casa do Campo"]
    plan = await world.plan(trip)
    assert plan.fetch_facts == plan.fetch_reviews == []
    assert plan.fetch_prices == {}


async def test_refresh_fetches_everything_again_but_keeps_names(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory)
    trip = make_trip()
    await world.run(trip)

    report = await world.run(trip, refresh=True)

    assert len(world.runner.calls_to(PRICE_ACTOR)) == 2 * len(SIZES)
    assert len(world.runner.calls_to(REVIEWS_ACTOR)) == 2
    assert world.page_requests == ["46397403", "52584351"] * 2
    assert len(world.namer_calls) == 2  # existing houses are never renamed
    assert [h.name for h in report.houses] == ["Casa do Albatroz", "Casa do Campo"]


async def test_new_dates_only_fetch_prices(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory)
    await world.run(make_trip())

    report = await world.run(make_trip(check_in="2027-01-10", check_out="2027-01-14"))

    assert len(world.runner.calls_to(PRICE_ACTOR)) == 2 * len(SIZES)  # every size again for the new dates
    assert len(world.runner.calls_to(REVIEWS_ACTOR)) == 1
    assert len(world.page_requests) == 2
    assert [len(h.quotes) for h in report.houses] == [8, 10]  # 4 + 4 for Albatroz, 5 + 5 for Itapoá


async def test_changing_the_extra_note_reanalyses_only_that_house(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory)
    trip = make_trip()
    await world.run(trip)
    async with session_factory() as session:
        repo = HouseRepository(session)
        house = await repo.get("46397403")
        assert house is not None
        house.extra = "Vimos nas fotos que a praia é a 2 minutos."
        await session.commit()
    analyst_calls_before = len(world.analyst_calls)

    await world.run(trip)

    assert len(world.analyst_calls) == analyst_calls_before + 1


async def test_one_blocked_house_does_not_stop_the_run(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory, blocked=frozenset({"46397403"}))

    report = await world.run(make_trip())

    assert list(report.failures) == ["46397403"]
    assert "HTTP 403" in report.failures["46397403"]
    assert [h.name for h in report.houses] == ["Casa do Campo"]


async def test_failed_analysis_keeps_the_fetched_data(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory, analyst_fails=True)

    report = await world.run(make_trip())

    assert set(report.failures) == {"46397403", "52584351"}
    async with session_factory() as session:
        house = await HouseRepository(session).get("46397403")
    assert house is not None
    assert house.facts is not None
    assert len(house.reviews) == 4
    assert house.analysis is None
    # the next run must not pay for the data again
    plan = await world.plan(make_trip())
    assert plan.fetch_facts == plan.fetch_reviews == []
    assert plan.fetch_prices == {}


async def test_empty_suggested_name_falls_back_to_the_listing_title(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory, namer_reply="  ")

    report = await world.run(make_trip())

    assert report.failures == {}
    assert all(house.facts is not None and house.name == (house.facts.title or "").strip() for house in report.houses)
    assert all(house.name for house in report.houses)


async def test_missing_reviews_are_retried_and_trigger_a_single_reanalysis(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory, fail_once={REVIEWS_ACTOR})
    trip = make_trip(urls=["https://www.airbnb.com.br/rooms/46397403"])

    first = await world.run(trip)

    assert first.failures == {}
    assert "46397403" in first.warnings
    assert any("reviews" in message for message in first.warnings["46397403"])
    assert first.houses[0].reviews == []
    assert len(world.analyst_calls) == 1
    plan = await world.plan(trip)
    assert plan.fetch_facts == [] and plan.fetch_prices == {}
    assert plan.fetch_reviews == ["46397403"]

    second = await world.run(trip)

    assert second.warnings == {}
    assert len(second.houses[0].reviews) == 4
    assert world.page_requests == ["46397403"]  # facts were not fetched again
    assert len(world.runner.calls_to(PRICE_ACTOR)) == 4  # 9 to 12 guests; the prices were not fetched again
    assert len(world.runner.calls_to(REVIEWS_ACTOR)) == 2
    assert len(world.analyst_calls) == 2  # new reviews change the prompt hash

    await world.run(trip)

    assert len(world.runner.calls_to(REVIEWS_ACTOR)) == 2
    assert len(world.analyst_calls) == 2
    assert (await world.plan(trip)).is_empty


async def test_zero_review_cap_never_plans_reviews(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory)
    trip = make_trip(review_cap=0)

    plan = await world.plan(trip)
    assert plan.fetch_reviews == []
    assert plan.estimated_cost_usd == pytest.approx(10 * 0.004)  # the default fakes use tri_angle for prices

    await world.run(trip)

    assert world.runner.calls_to(REVIEWS_ACTOR) == []
    assert (await world.plan(trip)).is_empty


async def test_duplicate_references_to_one_house_are_processed_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory)
    trip = make_trip(
        urls=[
            "https://www.airbnb.com.br/rooms/46397403",
            "46397403",
            "https://www.airbnb.com.br/rooms/46397403?check_in=2026-12-05&adults=2",
            "https://www.airbnb.com.br/rooms/46397403",
            "https://www.airbnb.com.br/rooms/52584351",
        ]
    )

    report = await world.run(trip)

    assert report.failures == {}
    assert len(report.houses) == 2
    assert world.page_requests == ["46397403", "52584351"]
    price_inputs = world.runner.calls_to(PRICE_ACTOR)
    assert len(price_inputs) == len(SIZES)
    assert price_inputs[0]["startUrls"] == [
        {"url": listing_url("46397403")},
        {"url": listing_url("52584351")},
    ]
    assert len(world.namer_calls) == 2


async def test_refresh_with_a_failing_page_keeps_the_old_data_and_warns(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory)
    trip = make_trip()
    await world.run(trip)
    world.blocked.add("46397403")

    report = await world.run(trip, refresh=True)

    assert report.failures == {}
    assert any("listing facts" in message for message in report.warnings["46397403"])
    assert "52584351" not in report.warnings
    albatroz = next(house for house in report.houses if house.airbnb_id == "46397403")
    assert albatroz.name == "Casa do Albatroz"
    assert albatroz.facts is not None
    assert albatroz.facts.person_capacity == 12


async def test_failing_review_and_price_batches_become_warnings_for_every_house(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory, fail_once={REVIEWS_ACTOR, PRICE_ACTOR})
    trip = make_trip()

    report = await world.run(trip)

    assert report.failures == {}
    assert set(report.warnings) == {"46397403", "52584351"}
    for messages in report.warnings.values():
        assert any("reviews" in m for m in messages)
        assert any("price" in m for m in messages)
    assert len(report.houses) == 2
    assert all(house.facts is not None and house.analysis is not None for house in report.houses)
    assert all(house.reviews == [] for house in report.houses)
    assert [len(house.quotes) for house in report.houses] == [3, 4]  # the one failed run cost one size per house
    assert len(world.analyst_calls) == 2


async def test_a_house_without_a_price_is_created_analysed_and_still_wants_its_price(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory)
    world.runner.outputs[PRICE_ACTOR] = lambda run_input: answer_prices(run_input)[-1:]  # nothing for 46397403
    trip = make_trip()

    report = await world.run(trip)

    assert report.failures == {}
    assert any("price" in message for message in report.warnings["46397403"])
    albatroz = next(house for house in report.houses if house.airbnb_id == "46397403")
    assert albatroz.analysis is not None
    assert albatroz.quotes_by_guests(trip) == {}
    plan = await world.plan(trip)
    assert plan.fetch_prices == {"46397403": [9, 10, 11, 12]}  # 13 is above the stored capacity
    assert plan.fetch_facts == []


async def test_a_failed_house_is_rolled_back_without_hurting_the_next_one(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory, blocked=frozenset({"46397403"}))

    report = await world.run(make_trip())

    assert list(report.failures) == ["46397403"]
    assert [house.airbnb_id for house in report.houses] == ["52584351"]
    house = report.houses[0]  # the session is closed: every relationship must already be loaded
    assert house.facts is not None and house.facts.title
    assert house.reviews == [] or all(review.text is not None for review in house.reviews)
    assert len(house.quotes) == len(SIZES)
    assert house.analysis is not None
    assert len(house.analysis.verdicts) == 2
    assert all(verdict.evidence for verdict in house.analysis.verdicts)
    assert house.analysis.points


def sent_to_apify(world: World, actor_id: str) -> list[str]:
    return [entry["url"] for run_input in world.runner.calls_to(actor_id) for entry in run_input["startUrls"]]


async def test_a_blocked_new_house_is_never_sent_to_apify(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory, blocked=frozenset({"46397403"}))
    trip = make_trip()

    report = await world.run(trip)

    assert list(report.failures) == ["46397403"]
    assert [h.airbnb_id for h in report.houses] == ["52584351"]
    assert sent_to_apify(world, PRICE_ACTOR) == [listing_url("52584351")] * len(SIZES)
    assert sent_to_apify(world, REVIEWS_ACTOR) == [listing_url("52584351")]

    await world.run(trip)  # the page is still blocked

    for actor in (PRICE_ACTOR, REVIEWS_ACTOR):
        assert listing_url("46397403") not in sent_to_apify(world, actor)
    assert len(world.runner.calls_to(PRICE_ACTOR)) == len(SIZES)  # the other house is complete, nothing else to buy


async def test_a_failing_namer_for_a_new_house_never_reaches_apify(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory, namer_fails=True)
    trip = make_trip()

    report = await world.run(trip)

    assert set(report.failures) == {"46397403", "52584351"}
    assert report.houses == []
    assert world.runner.calls == []
    await world.run(trip)
    assert world.runner.calls == []


async def test_a_note_reanalyses_only_its_house_and_an_unchanged_note_does_not(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory)
    await world.run(make_trip())
    before = len(world.analyst_calls)
    note = "Vimos nas fotos que a praia é a 2 minutos."
    trip = make_trip(notes={"https://www.airbnb.com.br/rooms/46397403?adults=2": note})

    report = await world.run(trip)

    assert len(world.analyst_calls) == before + 1
    assert "2 minutos" in world.analyst_calls[-1]
    extras = {house.airbnb_id: house.extra for house in report.houses}
    assert extras == {"46397403": note, "52584351": ""}

    await world.run(trip)

    assert len(world.analyst_calls) == before + 1


async def test_id_form_notes_work_and_never_touch_other_houses(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory)
    await world.run(make_trip(notes={"46397403": "Primeira nota", "52584351": "Segunda nota"}))

    report = await world.run(make_trip(notes={"52584351": "Segunda nota alterada"}))

    extras = {house.airbnb_id: house.extra for house in report.houses}
    assert extras == {"46397403": "Primeira nota", "52584351": "Segunda nota alterada"}


async def test_notes_for_unknown_or_invalid_keys_only_warn_when_invalid(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory)
    trip = make_trip(notes={"999": "Casa que não está na viagem", "https://example.com/nada": "Link ruim"})

    report = await world.run(trip)

    assert report.failures == {}
    assert set(report.warnings) == {"https://example.com/nada"}
    assert all(house.extra == "" for house in report.houses)


def price_runs(world: World) -> dict[int, list[str]]:
    """The houses asked about in each price run, keyed by the guests of that run."""
    return {
        run_input["adults"]: [entry["url"].rsplit("/", 1)[-1] for entry in run_input["startUrls"]]
        for run_input in world.runner.calls_to(PRICE_ACTOR)
    }


async def test_first_run_makes_one_price_run_per_size_and_never_asks_about_sizes_over_the_capacity(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory)

    await world.run(make_trip())

    both = ["46397403", "52584351"]
    assert price_runs(world) == {9: both, 10: both, 11: both, 12: both, 13: ["52584351"]}
    assert all(run_input["children"] == 0 for run_input in world.runner.calls_to(PRICE_ACTOR))


async def test_only_the_missing_sizes_are_fetched(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory)
    trip = make_trip()
    await world.run(trip)
    async with session_factory() as session:
        house = await HouseRepository(session).get("52584351")
        assert house is not None
        for quote in [q for q in house.quotes if q.guests in (10, 13)]:
            await session.delete(quote)
        await session.commit()
    runs_before = len(world.runner.calls_to(PRICE_ACTOR))

    plan = await world.plan(trip)
    report = await world.run(trip)

    assert plan.fetch_prices == {"52584351": [10, 13]}
    assert plan.is_empty is False
    new_runs = world.runner.calls_to(PRICE_ACTOR)[runs_before:]
    assert sorted((run["adults"], len(run["startUrls"])) for run in new_runs) == [(10, 1), (13, 1)]
    assert len(report.houses[1].quotes) == len(SIZES)
    assert (await world.plan(trip)).is_empty


async def test_an_unavailable_answer_is_stored_and_not_asked_again(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory)
    world.runner.outputs[PRICE_ACTOR] = lambda run_input: (
        [priced_item("52584351", None)] if run_input["adults"] == 12 else answer_prices(run_input)
    )
    trip = make_trip(urls=["https://www.airbnb.com.br/rooms/52584351"])

    report = await world.run(trip)

    assert report.failures == {} and report.warnings == {}
    quotes = report.houses[0].quotes_by_guests(trip)
    assert quotes[12].total_cents is None
    assert quotes[11].total_cents == 369100
    runs_before = len(world.runner.calls_to(PRICE_ACTOR))
    assert (await world.plan(trip)).fetch_prices == {}

    await world.run(trip)

    assert len(world.runner.calls_to(PRICE_ACTOR)) == runs_before


async def test_infants_are_passed_unchanged_to_every_quote(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory)

    await world.run(make_trip(infants=2))

    assert {run_input["infants"] for run_input in world.runner.calls_to(PRICE_ACTOR)} == {2}


async def test_price_runs_are_limited_to_three_at_a_time(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory)
    running = peak = 0
    answer = world.runner.run_actor

    async def tracked(actor_id: str, run_input: dict[str, Any], **limits: Any) -> list[dict[str, Any]]:
        nonlocal running, peak
        if actor_id == PRICE_ACTOR:
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1
        return await answer(actor_id, run_input, **limits)

    world.runner.run_actor = tracked  # type: ignore[method-assign,assignment]

    await world.run(make_trip())

    assert peak == 3


async def test_one_price_warning_per_house_when_the_first_source_fails_for_every_size(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(
        session_factory,
        price_source=lambda runner: FallbackPriceSource([ThelastscraperPrice(runner), TriAnglePrice(runner)]),
    )

    report = await world.run(make_trip())

    assert report.failures == {}
    for room_id, sizes in (("46397403", "9, 10, 11, 12"), ("52584351", "9, 10, 11, 12, 13")):
        price_warnings = [m for m in report.warnings[room_id] if "price from tri_angle" in m]
        assert len(price_warnings) == 1
        assert price_warnings[0].startswith(f"{sizes} guests: price from tri_angle because thelastscraper failed: ")
        assert "No price result" in price_warnings[0]


class NotedPrices:
    name = "noted"

    async def fetch_prices(self, room_ids: list[str], trip: TripConfig, guests: int) -> SourceBatch[PriceData]:
        return SourceBatch(
            {room_id: PriceData(total_cents=100) for room_id in room_ids},
            {room_id: f"price note {room_id}" for room_id in room_ids},
        )

    def estimate_usd(self, quotes: int) -> float:
        return 0.0


class NotedReviews:
    name = "noted"

    async def fetch_reviews(
        self, room_ids: list[str], trip: TripConfig, *, expect_reviews: frozenset[str] = frozenset()
    ) -> SourceBatch[list[ReviewData]]:
        return SourceBatch(
            {room_id: [] for room_id in room_ids}, {room_id: f"review note {room_id}" for room_id in room_ids}
        )

    def estimate_usd(self, houses: int, review_cap: int) -> float:
        return 0.0


async def test_batch_notes_become_warnings_keyed_by_room_id(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(
        session_factory, price_source=lambda runner: NotedPrices(), review_source=lambda runner: NotedReviews()
    )

    report = await world.run(make_trip())

    for room_id in ("46397403", "52584351"):
        assert "review note " + room_id in report.warnings[room_id]
        assert any(m.endswith(f"price note {room_id}") for m in report.warnings[room_id])


ALBATROZ_URL = ["https://www.airbnb.com.br/rooms/46397403"]


def fail_actor(world: World, actor_id: str) -> None:
    def refuse(run_input: dict[str, Any]) -> list[dict[str, Any]]:
        raise ApifyError(f"Actor {actor_id} is down")

    world.runner.outputs[actor_id] = refuse


async def test_a_first_source_with_no_reviews_for_a_house_that_has_some_falls_back(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory, review_source=lambda runner: build_review_source(["kaix", "tri_angle"], runner))
    world.runner.outputs[KaixReviews.ACTOR] = []
    trip = make_trip(urls=ALBATROZ_URL)

    report = await world.run(trip)

    assert report.failures == {}
    assert len(report.houses[0].reviews) == 4
    assert len(world.runner.calls_to(KaixReviews.ACTOR)) == 1
    assert len(world.runner.calls_to(REVIEWS_ACTOR)) == 1
    assert any("tri_angle" in note and "no reviews" in note for note in report.warnings["46397403"])


async def test_a_house_without_reviews_accepts_an_empty_answer_without_a_fallback(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory, review_source=lambda runner: build_review_source(["kaix", "tri_angle"], runner))
    world.runner.outputs[KaixReviews.ACTOR] = []
    trip = make_trip(urls=ALBATROZ_URL)
    await world.run(trip)
    async with session_factory() as session:
        house = await HouseRepository(session).get("46397403")
        assert house is not None and house.facts is not None
        house.facts.review_count = 0
        for review in list(house.reviews):
            await session.delete(review)
        await session.commit()
    fallback_calls_before = len(world.runner.calls_to(REVIEWS_ACTOR))

    report = await world.run(trip)

    assert report.failures == {}
    assert report.warnings == {}
    assert len(world.runner.calls_to(KaixReviews.ACTOR)) == 2
    assert len(world.runner.calls_to(REVIEWS_ACTOR)) == fallback_calls_before


async def test_a_run_survives_failing_first_sources_and_stores_everything_from_the_fallbacks(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(
        session_factory,
        price_source=lambda runner: build_price_source(["thelastscraper", "tri_angle"], runner),
        review_source=lambda runner: build_review_source(["kaix", "steadyscrape"], runner),
    )
    fail_actor(world, ThelastscraperPrice.ACTOR)
    fail_actor(world, KaixReviews.ACTOR)
    world.runner.outputs[SteadyscrapeReviews.ACTOR] = load_json("steadyscrape_46397403.json")
    trip = make_trip(urls=ALBATROZ_URL)

    report = await world.run(trip)

    assert report.failures == {}
    house = report.houses[0]
    assert len(house.reviews) == 15
    assert {q.source for q in house.quotes} == {"tri_angle"}
    assert sorted(house.quotes_by_guests(trip)) == [9, 10, 11, 12]
    notes = report.warnings["46397403"]
    assert any("reviews from steadyscrape because kaix failed" in note for note in notes)
    assert any("guests: price from tri_angle because thelastscraper failed" in note for note in notes)
    calls_before = len(world.runner.calls)

    again = await world.run(trip)

    assert len(world.runner.calls) == calls_before
    assert again.warnings == {}
    assert (await world.plan(trip)).is_empty


# Screening: Albatroz (46397403) is listed in the yaml, Campo (52584351) comes from a search.
ALBATROZ, CAMPO = "46397403", "52584351"
CAMPO_URL = ["https://www.airbnb.com.br/rooms/52584351"]


async def stored_screen(session_factory: async_sessionmaker[AsyncSession], room_id: str) -> Any:
    async with session_factory() as session:
        return await SearchRepository(session).get_screen(room_id)


async def stored_house(session_factory: async_sessionmaker[AsyncSession], room_id: str) -> Any:
    async with session_factory() as session:
        return await HouseRepository(session).get(room_id)


def test_rooms_from_puts_the_yaml_urls_first_and_marks_only_new_hits_for_screening() -> None:
    trip = make_trip(urls=[*ALBATROZ_URL, ALBATROZ])

    assert rooms_from(trip, [CAMPO, ALBATROZ, CAMPO]) == [RoomRef(ALBATROZ, screen=False), RoomRef(CAMPO, screen=True)]


async def test_urls_from_the_yaml_are_never_screened(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory)
    world.screen_statuses.update({ALBATROZ: ScreenStatus.FAILS, CAMPO: ScreenStatus.FAILS})
    trip = make_trip()  # both houses are in the yaml

    report = await world.run(trip, [ALBATROZ, CAMPO])

    assert world.screen_calls == []
    assert report.screened_out == {}
    assert [h.airbnb_id for h in report.houses] == [ALBATROZ, CAMPO]

    # a hit that is already a stored house is not screened either
    report = await world.run(make_trip(urls=ALBATROZ_URL), [CAMPO])

    assert world.screen_calls == []
    assert [h.airbnb_id for h in report.houses] == [ALBATROZ, CAMPO]


async def test_a_searched_hit_failing_a_must_have_is_dropped_before_any_paid_call(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory)
    world.screen_statuses[CAMPO] = ScreenStatus.FAILS
    trip = make_trip(urls=ALBATROZ_URL)

    report = await world.run(trip, [CAMPO])

    assert world.screen_calls == [CAMPO]
    assert report.screened_out == {CAMPO: f'{SCREEN_REASON} ("{SCREEN_QUOTE}")'}
    assert report.failures == {}
    assert [h.airbnb_id for h in report.houses] == [ALBATROZ]
    assert listing_url(CAMPO) not in sent_to_apify(world, PRICE_ACTOR) + sent_to_apify(world, REVIEWS_ACTOR)
    assert len(world.namer_calls) == len(world.analyst_calls) == 1  # only Albatroz
    screen = await stored_screen(session_factory, CAMPO)
    assert screen is not None and screen.passed is False
    assert [(v.aspect_name, v.status, v.reason, v.quote) for v in screen.verdicts] == [
        ("Perto da praia", ScreenStatus.FAILS, SCREEN_REASON, SCREEN_QUOTE)
    ]
    assert await stored_house(session_factory, CAMPO) is None


async def test_unclear_passes_and_is_analysed(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory)
    world.screen_statuses[CAMPO] = ScreenStatus.UNCLEAR

    report = await world.run(make_trip(urls=ALBATROZ_URL), [CAMPO])

    assert world.screen_calls == [CAMPO]
    assert report.screened_out == {}
    assert [h.airbnb_id for h in report.houses] == [ALBATROZ, CAMPO]
    assert report.houses[1].analysis is not None
    screen = await stored_screen(session_factory, CAMPO)
    assert screen is not None and screen.passed is True


async def test_second_run_does_not_fetch_or_screen_a_stored_failure(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory)
    world.screen_statuses[CAMPO] = ScreenStatus.FAILS
    trip = make_trip(urls=ALBATROZ_URL)
    await world.run(trip, [CAMPO])
    pages_before, calls_before = list(world.page_requests), len(world.runner.calls)

    plan = await world.plan(trip, [CAMPO])
    report = await world.run(trip, [CAMPO])

    assert plan.skipped_screened == [CAMPO]
    assert plan.screen_rooms == []
    assert CAMPO not in plan.room_ids
    assert plan.is_empty
    assert world.page_requests == pages_before
    assert world.screen_calls == [CAMPO]
    assert len(world.runner.calls) == calls_before
    assert [h.airbnb_id for h in report.houses] == [ALBATROZ]


async def test_refresh_screens_again(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory)
    world.screen_statuses[CAMPO] = ScreenStatus.FAILS
    trip = make_trip(urls=ALBATROZ_URL)
    await world.run(trip, [CAMPO])
    world.screen_statuses[CAMPO] = ScreenStatus.MEETS  # say the host fixed the listing

    report = await world.run(trip, [CAMPO], refresh=True)

    assert world.screen_calls == [CAMPO, CAMPO]
    assert world.page_requests.count(CAMPO) == 2
    assert [h.airbnb_id for h in report.houses] == [ALBATROZ, CAMPO]
    screen = await stored_screen(session_factory, CAMPO)
    assert screen is not None and screen.passed is True


async def test_changed_must_have_details_rescreens(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory)
    world.screen_statuses[CAMPO] = ScreenStatus.FAILS
    await world.run(make_trip(urls=ALBATROZ_URL), [CAMPO])
    wider = make_trip(
        urls=ALBATROZ_URL,
        aspects=[
            {"name": "Perto da praia", "kind": "must_have", "details": "até 5 km"},
            {"name": "Piscina", "kind": "nice_to_have"},
        ],
    )
    world.screen_statuses[CAMPO] = ScreenStatus.MEETS

    plan = await world.plan(wider, [CAMPO])
    report = await world.run(wider, [CAMPO])

    assert plan.screen_rooms == [CAMPO] and plan.skipped_screened == []
    assert world.screen_calls == [CAMPO, CAMPO]
    assert [h.airbnb_id for h in report.houses] == [ALBATROZ, CAMPO]


async def test_no_must_haves_skips_the_screen(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory)
    world.screen_statuses[CAMPO] = ScreenStatus.FAILS
    aspects = [{"name": "Perto da praia", "kind": "nice_to_have"}, {"name": "Piscina", "kind": "nice_to_have"}]
    trip = make_trip(urls=ALBATROZ_URL, aspects=aspects)

    plan = await world.plan(trip, [CAMPO])
    report = await world.run(trip, [CAMPO])

    assert plan.screen_rooms == []
    assert world.screen_calls == []
    assert [h.airbnb_id for h in report.houses] == [ALBATROZ, CAMPO]
    assert await stored_screen(session_factory, CAMPO) is None


async def test_without_a_screener_nothing_is_screened(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory, screener=False)

    report = await world.run(make_trip(urls=ALBATROZ_URL), [CAMPO])

    assert report.screened_out == {}
    assert [h.airbnb_id for h in report.houses] == [ALBATROZ, CAMPO]


async def test_a_screening_error_fails_only_that_house(session_factory: async_sessionmaker[AsyncSession]) -> None:
    world = make_world(session_factory)
    world.screen_errors.add(CAMPO)
    trip = make_trip(urls=ALBATROZ_URL)

    report = await world.run(trip, [CAMPO])

    assert set(report.failures) == {CAMPO}
    assert "screener is down" in report.failures[CAMPO]
    assert report.screened_out == {}
    assert [h.airbnb_id for h in report.houses] == [ALBATROZ]
    assert listing_url(CAMPO) not in sent_to_apify(world, PRICE_ACTOR) + sent_to_apify(world, REVIEWS_ACTOR)
    assert await stored_screen(session_factory, CAMPO) is None
    assert await stored_house(session_factory, CAMPO) is None
    assert (await world.plan(trip, [CAMPO])).screen_rooms == [CAMPO]  # tried again next time


def tracker() -> CostTracker:
    return CostTracker(input_usd_per_mtok=0.27, output_usd_per_mtok=1.1)


def run_totals(lines: list[str]) -> list[float]:
    return [float(line.split("run total $")[1].split(" ")[0]) for line in lines if "run total $" in line]


async def test_cached_house_logs_a_zero_cost_line(
    session_factory: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
) -> None:
    trip = make_trip()
    await make_world(session_factory, tracker=tracker()).run(trip)
    world = make_world(session_factory, tracker=tracker())  # a new run: a fresh tracker
    caplog.clear()

    with caplog.at_level(logging.INFO):
        await world.run(trip)

    lines = [record.getMessage() for record in caplog.records]
    assert any(line.startswith("[1/2] Casa do Albatroz: cached, $0, run total $0.00") for line in lines)
    assert any(line.startswith("[2/2] Casa do Campo: cached, $0, run total $0.00") for line in lines)


async def test_each_analysed_house_logs_a_cost_line_with_running_total(
    session_factory: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
) -> None:
    costs = tracker()
    world = make_world(session_factory, tracker=costs)
    world.screen_statuses[CAMPO] = ScreenStatus.FAILS
    trip = make_trip(urls=[*ALBATROZ_URL, "https://www.airbnb.com.br/rooms/999"])
    world.blocked.add("999")

    with caplog.at_level(logging.INFO):
        report = await world.run(trip, [CAMPO])

    lines = [record.getMessage() for record in caplog.records]
    screened = [line for line in lines if "screened out: " in line]
    assert len(screened) == 1
    assert f"screened out: {SCREEN_REASON}" in screened[0] and "DeepSeek ~$" in screened[0]
    analysed = [line for line in lines if line.startswith("[") and "Casa do Albatroz:" in line and "Apify" in line]
    assert len(analysed) == 1
    assert "4 reviews, 4 price quotes" in analysed[0]
    assert "Apify ~$" in analysed[0] and "DeepSeek ~$" in analysed[0]
    assert any("] 999: failed (" in line for line in lines)
    totals = run_totals(lines)
    assert totals == sorted(totals) and totals[-1] > 0
    assert costs.cost_of(ALBATROZ).apify_usd > 0 and costs.cost_of(ALBATROZ).llm_usd > 0
    assert costs.cost_of(CAMPO).apify_usd == 0 and costs.cost_of(CAMPO).llm_usd > 0
    assert set(report.failures) == {"999"}


async def test_plan_reports_pages_to_screen_and_assumes_all_pass(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    world = make_world(session_factory)
    trip = make_trip(urls=ALBATROZ_URL)

    with_hit = await world.plan(trip, [CAMPO])
    listed = await world.plan(make_trip())

    assert with_hit.screen_rooms == [CAMPO]
    assert with_hit.skipped_screened == []
    assert with_hit.room_ids == with_hit.fetch_facts == with_hit.fetch_reviews == [ALBATROZ, CAMPO]
    assert with_hit.fetch_prices == {ALBATROZ: SIZES, CAMPO: SIZES}
    assert with_hit.estimated_cost_usd == pytest.approx(listed.estimated_cost_usd)
    assert listed.screen_rooms == []
    assert world.page_requests == world.screen_calls == []
