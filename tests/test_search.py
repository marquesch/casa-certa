from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from casa_certa.db.repository import HouseRepository, SearchRepository
from casa_certa.search import SearchService
from casa_certa.sources.search_sources import SearchError, SearchHit, SearchQuery
from tests.fakes import make_trip


def hit(airbnb_id: str, location: str) -> SearchHit:
    return SearchHit(
        airbnb_id=airbnb_id, url=f"https://www.airbnb.com.br/rooms/{airbnb_id}", title="Casa", location=location
    )


class FakeSearch:
    name = "fake"

    def __init__(self, results: dict[str, list[SearchHit] | str]) -> None:
        self.results = results
        self.queries: list[SearchQuery] = []

    def estimate_usd(self, results: int) -> float:
        return results * 0.004

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        self.queries.append(query)
        result = self.results[query.location]
        if isinstance(result, str):
            raise SearchError(result)
        return result


def trip_for(*locations: str):
    return make_trip(search={"locations": list(locations), "max_results": 10})


async def last_ids(factory: async_sessionmaker[AsyncSession]) -> list[str]:
    async with factory() as session:
        return await SearchRepository(session).last_search_ids()


async def test_each_location_is_searched_with_the_trip_dates_and_max_people(session_factory) -> None:
    source = FakeSearch({"A": [hit("1", "A")], "B": []})
    trip = trip_for("A", "B")

    await SearchService(source, session_factory, None).run(trip)

    assert [q.location for q in source.queries] == ["A", "B"]
    assert {(q.check_in, q.check_out, q.guests, q.max_results) for q in source.queries} == {
        (trip.check_in, trip.check_out, trip.max_people, 10)
    }


async def test_a_house_found_by_two_locations_appears_once(session_factory) -> None:
    source = FakeSearch({"A": [hit("1", "A")], "B": [hit("1", "B"), hit("2", "B")]})

    outcome = await SearchService(source, session_factory, None).run(trip_for("A", "B"))

    assert [h.airbnb_id for h in outcome.hits] == ["1", "2"]
    assert await last_ids(session_factory) == ["1", "2"]


async def test_a_failing_location_does_not_stop_the_others(session_factory) -> None:
    source = FakeSearch({"A": "boom", "B": [hit("2", "B")]})

    outcome = await SearchService(source, session_factory, None).run(trip_for("A", "B"))

    assert outcome.failures == {"A": "boom"}
    assert await last_ids(session_factory) == ["2"]


async def test_zero_hits_is_not_an_error_and_stores_an_empty_search(session_factory) -> None:
    async with session_factory() as session:
        await SearchRepository(session).replace_last_search(make_trip(), [hit("9", "A")])
        await session.commit()

    outcome = await SearchService(FakeSearch({"A": []}), session_factory, None).run(trip_for("A"))

    assert outcome.hits == [] and outcome.failures == {}
    assert await last_ids(session_factory) == []


async def test_all_locations_failing_keeps_the_previous_last_search(session_factory) -> None:
    async with session_factory() as session:
        await SearchRepository(session).replace_last_search(make_trip(), [hit("9", "A")])
        await session.commit()

    outcome = await SearchService(FakeSearch({"A": "x", "B": "y"}), session_factory, None).run(trip_for("A", "B"))

    assert set(outcome.failures) == {"A", "B"}
    assert await last_ids(session_factory) == ["9"]


async def test_hits_already_stored_as_houses_are_not_new(session_factory) -> None:
    async with session_factory() as session:
        await HouseRepository(session).add("1", "Casa Um")
        await session.commit()
    source = FakeSearch({"A": [hit("1", "A"), hit("2", "A")]})

    outcome = await SearchService(source, session_factory, None).run(trip_for("A"))

    assert [h.airbnb_id for h in outcome.hits] == ["1", "2"]
    assert [h.airbnb_id for h in outcome.new_hits] == ["2"]


async def test_dry_run_lines_call_nothing(session_factory) -> None:
    source = FakeSearch({})

    lines = SearchService(source, session_factory, None).plan_lines(trip_for("Itapoá, SC"))

    assert lines == ['search "Itapoá, SC": up to 10 results, ~$0.04']
    assert source.queries == []
