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


async def test_save_quote_upserts_per_dates_group_size_and_infants(session: AsyncSession) -> None:
    repo = HouseRepository(session)
    house = await repo.add("1", "Casa Um")
    trip = make_trip()

    await repo.save_quote(house, trip, 11, PriceData(total_cents=367500, source="actor-a"))
    await repo.save_quote(house, trip, 11, PriceData(total_cents=360000, original_total_cents=367500, source="actor-b"))
    await repo.save_quote(house, trip, 12, PriceData(total_cents=None, source="actor-b"))
    other_dates = make_trip(check_out="2026-12-10")
    await repo.save_quote(house, other_dates, 11, PriceData(total_cents=450000))
    await repo.save_quote(house, make_trip(infants=0), 11, PriceData(total_cents=350000))
    await session.commit()

    loaded = await repo.get("1")
    assert loaded is not None
    assert len(loaded.quotes) == 4
    quote = loaded.quote_for(trip, 11)
    assert quote is not None
    assert (quote.total_cents, quote.original_total_cents, quote.nights, quote.source) == (
        360000,
        367500,
        4,
        "actor-b",
    )
    unavailable = loaded.quote_for(trip, 12)
    assert unavailable is not None
    assert (unavailable.total_cents, unavailable.source) == (None, "actor-b")
    assert set(loaded.quotes_by_guests(trip)) == {11, 12}
    other = loaded.quote_for(other_dates, 11)
    assert other is not None
    assert other.total_cents == 450000
    assert loaded.quote_for(trip, 9) is None
    infantless = loaded.quote_for(make_trip(infants=0), 11)
    assert infantless is not None
    assert infantless.total_cents == 350000


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
