from typing import Any

import pytest

from casa_certa.sources.apify import ApifyError
from casa_certa.sources.protocols import charge_ceiling
from casa_certa.sources.search_sources import SearchError, SearchHit, SearchQuery, TriAngleSearch
from tests.fakes import FakeRunner, load_json, make_trip

ACTOR = TriAngleSearch.ACTOR


def make_query(**overrides) -> SearchQuery:
    values = {
        "location": "Itapoá, SC",
        "check_in": "2027-01-10",
        "check_out": "2027-01-14",
        "guests": 6,
        "infants": 1,
        "max_results": 5,
        "max_price_per_night": 900,
        "min_rating": 4.5,
    }
    values.update(overrides)
    return SearchQuery.model_validate(values)


def test_for_location_uses_the_trip_and_its_search_limits() -> None:
    trip = make_trip(
        urls=[],
        search={"locations": ["Itapoá, SC"], "max_results": 7, "max_price_per_night": 800, "min_rating": 4.6},
        max_people=11,
    )
    query = SearchQuery.for_location(trip, "Itapoá, SC")
    assert (query.guests, query.infants, query.max_results) == (11, trip.infants, 7)
    assert (query.max_price_per_night, query.min_rating, query.location) == (800, 4.6, "Itapoá, SC")
    assert (query.check_in, query.check_out) == (trip.check_in, trip.check_out)


def test_build_input_carries_place_dates_guests_and_limits() -> None:
    run_input = TriAngleSearch(FakeRunner({})).build_input(make_query())
    assert run_input["locationQueries"] == ["Itapoá, SC"]
    assert (run_input["checkIn"], run_input["checkOut"]) == ("2027-01-10", "2027-01-14")
    assert (run_input["adults"], run_input["infants"], run_input["children"]) == (6, 1, 0)
    assert run_input["maxResults"] == 5 and run_input["priceMax"] == 900


def test_build_input_omits_the_price_limit_when_unset() -> None:
    assert "priceMax" not in TriAngleSearch(FakeRunner({})).build_input(make_query(max_price_per_night=None))


async def test_search_returns_the_fixture_hits() -> None:
    items = load_json("search_tri_angle.json")
    hits = await TriAngleSearch(FakeRunner({ACTOR: items})).search(make_query())
    assert len(hits) == len(items)
    assert hits[0] == SearchHit(
        airbnb_id=items[0]["id"],
        url=f"https://www.airbnb.com.br/rooms/{items[0]['id']}",
        title=items[0]["title"],
        location="Itapoá, SC",
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://pt.airbnb.com/rooms/123?check_in=2027-01-10",
        "https://www.airbnb.com/rooms/plus/123",
        "https://www.airbnb.com.br/rooms/123",
    ],
)
async def test_parse_items_returns_hits_with_normalised_ids(url) -> None:
    hits = await TriAngleSearch(FakeRunner({ACTOR: [{"url": url, "title": "Casa"}]})).search(make_query())
    assert [h.airbnb_id for h in hits] == ["123"]


async def test_items_without_a_room_id_are_skipped() -> None:
    items: list[Any] = [
        {"title": "no url"},
        {"url": "https://www.airbnb.com/help"},
        "junk",
        {"url": "https://x/rooms/5"},
    ]
    hits = await TriAngleSearch(FakeRunner({ACTOR: items})).search(make_query())
    assert [h.airbnb_id for h in hits] == ["5"]


async def test_run_is_capped() -> None:
    runner = FakeRunner({ACTOR: []})
    source = TriAngleSearch(runner)
    await source.search(make_query(max_results=5))
    assert runner.max_items_for(ACTOR) == [5]
    assert runner.max_charge_for(ACTOR) == [charge_ceiling(source.estimate_usd(5))]


async def test_actor_failure_raises_search_error() -> None:
    with pytest.raises(SearchError) as error:
        await TriAngleSearch(FakeRunner({}, fail_once={ACTOR})).search(make_query())
    assert isinstance(error.value, ApifyError)


def test_estimate_usd_is_per_result() -> None:
    source = TriAngleSearch(FakeRunner({}))
    assert source.estimate_usd(10) == pytest.approx(10 * source.RESULT_COST_USD)
