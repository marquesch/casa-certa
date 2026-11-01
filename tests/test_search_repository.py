from sqlalchemy.ext.asyncio import AsyncSession

from casa_certa.db.repository import SearchRepository
from casa_certa.enums import ScreenStatus
from casa_certa.sources.search_sources import SearchHit
from tests.fakes import make_trip


def hit(airbnb_id: str, location: str = "Praia A", title: str | None = "Casa") -> SearchHit:
    return SearchHit(
        airbnb_id=airbnb_id,
        url=f"https://www.airbnb.com.br/rooms/{airbnb_id}",
        title=title,
        location=location,
        rating=None,
    )


async def test_replacing_the_last_search_drops_the_old_one(session: AsyncSession) -> None:
    repo = SearchRepository(session)
    await repo.replace_last_search(make_trip(), [hit("1"), hit("2")])
    await session.commit()

    await repo.replace_last_search(make_trip(), [hit("3")])
    await session.commit()

    assert await repo.last_search_ids() == ["3"]


async def test_a_house_found_by_two_locations_is_stored_once_with_the_first_location(session: AsyncSession) -> None:
    repo = SearchRepository(session)
    await repo.replace_last_search(make_trip(), [hit("1", "Praia A"), hit("1", "Praia B"), hit("2", "Praia B")])
    await session.commit()

    hits = await repo.last_search_hits()
    assert [(h.airbnb_id, h.location) for h in hits] == [("1", "Praia A"), ("2", "Praia B")]


async def test_last_search_hits_keep_order(session: AsyncSession) -> None:
    repo = SearchRepository(session)
    await repo.replace_last_search(make_trip(), [hit("9"), hit("3"), hit("5")])
    await session.commit()

    assert await repo.last_search_ids() == ["9", "3", "5"]
    assert next(h.url for h in await repo.last_search_hits()).endswith("/9")


async def test_save_screen_replaces_the_previous_result_and_keeps_verdicts(session: AsyncSession) -> None:
    repo = SearchRepository(session)
    await repo.save_screen("1", "u", "Casa", "h1", "m", True, [("Praia", ScreenStatus.UNCLEAR, "r", "q")])
    await session.commit()
    await repo.save_screen(
        "1",
        "u",
        "Casa",
        "h2",
        "m",
        False,
        [("Praia", ScreenStatus.FAILS, "longe", "a 5 km"), ("Piscina", ScreenStatus.MEETS, "tem", "piscina")],
    )
    await session.commit()

    screen = await repo.get_screen("1")
    assert screen is not None
    assert (screen.passed, screen.input_hash) == (False, "h2")
    assert [(v.aspect_name, v.status, v.reason, v.quote) for v in screen.verdicts] == [
        ("Praia", ScreenStatus.FAILS, "longe", "a 5 km"),
        ("Piscina", ScreenStatus.MEETS, "tem", "piscina"),
    ]


async def test_get_screen_returns_none_for_an_unknown_house(session: AsyncSession) -> None:
    assert await SearchRepository(session).get_screen("nope") is None


async def test_last_search_info_holds_the_dates_and_guests_of_the_run(session: AsyncSession) -> None:
    repo = SearchRepository(session)
    assert await repo.last_search_info() is None
    trip = make_trip()
    await repo.replace_last_search(trip, [hit("1")])
    await session.commit()

    info = await repo.last_search_info()

    assert info is not None
    assert (info.check_in, info.check_out, info.guests) == (trip.check_in, trip.check_out, trip.max_people)
