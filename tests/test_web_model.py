from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from casa_certa.db.models import House
from casa_certa.db.repository import HouseRepository
from casa_certa.enums import AspectStatus, EvidenceSource
from casa_certa.pricing import PriceRow
from casa_certa.sources.airbnb_page import ListingFacts
from casa_certa.sources.apify import PriceData
from casa_certa.web.model import HouseCard, build_site, city_of, default_sort_key
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

    site = build_site([house], make_trip(urls=["https://www.airbnb.com.br/rooms/46397403"]))

    assert [c.name for c in site.complete] == ["Casa do Albatroz"]
    assert site.incomplete == ()
    card = site.complete[0]
    assert (card.city, card.rating, card.review_count) == ("Matinhos", 4.95, 87)
    assert card.url == "https://www.airbnb.com.br/rooms/46397403"
    assert card.per_person_cents(9) == 40834  # 367500 / 9 rounded up
    assert card.per_person_cents(13) is None  # unavailable


@pytest.mark.parametrize("status", [AspectStatus.UNMET, AspectStatus.PARTIAL, AspectStatus.UNKNOWN])
async def test_a_must_have_that_is_not_met_moves_the_house_down_and_is_listed(
    session: AsyncSession, matinhos_facts: ListingFacts, status: AspectStatus
) -> None:
    house = await store(session, "46397403", "Casa", matinhos_facts, statuses={MUST: status})

    site = build_site([house], make_trip())

    assert site.complete == ()
    assert [(a.name, a.status) for a in site.incomplete[0].missing_must_haves] == [(MUST, status)]


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


async def test_site_header_data(
    session: AsyncSession, matinhos_facts: ListingFacts, itapoa_facts: ListingFacts
) -> None:
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


def test_unanalysed_cards_sort_after_analysed_ones_even_when_cheaper() -> None:
    cheap_row = PriceRow(people=13, total_cents=100, original_total_cents=None, per_person_cents=10)
    dear_row = PriceRow(people=13, total_cents=900, original_total_cents=None, per_person_cents=90)
    pending = HouseCard(name="a", url="u", analysed=False, prices=(cheap_row,))
    analysed = HouseCard(name="b", url="u", analysed=True, prices=(dear_row,))
    unpriced = HouseCard(name="c", url="u", analysed=True)

    ordered = sorted([pending, unpriced, analysed], key=lambda card: default_sort_key(card, 13))

    assert [card.name for card in ordered] == ["b", "c", "a"]
