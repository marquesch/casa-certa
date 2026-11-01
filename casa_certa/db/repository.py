"""All database reads and writes. Callers own the transaction and call `session.commit()`."""

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import delete, select
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
    ScreenResult,
    ScreenVerdictRow,
    SearchHitRow,
    SearchRun,
    utcnow,
)
from casa_certa.enums import PointKind, RuleKind, ScreenStatus
from casa_certa.sources.airbnb_page import ListingFacts
from casa_certa.sources.apify import PriceData, ReviewData
from casa_certa.sources.search_sources import SearchHit


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
        # Relationships are set explicitly: a new object's unset ones would lazy-load (IO) on first access.
        house = House(
            airbnb_id=airbnb_id,
            name=name,
            extra="",
            facts=None,
            amenities=[],
            rooms=[],
            highlights=[],
            rules=[],
            reviews=[],
            quotes=[],
            analysis=None,
        )
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
            HouseRule(kind=RuleKind.SAFETY, type=r.type, title=r.title, description=r.description) for r in facts.safety
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

    async def save_quote(self, house: House, trip: TripConfig, guests: int, price: PriceData) -> None:
        """Insert or update the quote for one group size; an unavailable answer is stored with no total."""
        quote = house.quote_for(trip, guests)
        if quote is None:
            house.quotes.append(
                PriceQuote(
                    check_in=trip.check_in,
                    check_out=trip.check_out,
                    guests=guests,
                    infants=trip.infants,
                    nights=trip.nights,
                    total_cents=price.total_cents,
                    original_total_cents=price.original_total_cents,
                    source=price.source,
                )
            )
        else:
            quote.total_cents = price.total_cents
            quote.original_total_cents = price.original_total_cents
            quote.source = price.source
            quote.fetched_at = utcnow()
        await self._session.flush()

    async def save_analysis(self, house: House, input_hash: str, model: str, analysis: HouseAnalysis) -> None:
        """Replace the current analysis. The old one is deleted first because of the unique house_id."""
        if house.analysis is not None:
            await self._session.delete(house.analysis)
            await self._session.flush()
        points = [
            AnalysisPoint(kind=PointKind.PRO, text=text, position=position)
            for position, text in enumerate(analysis.pros)
        ] + [
            AnalysisPoint(kind=PointKind.CON, text=text, position=position)
            for position, text in enumerate(analysis.cons)
        ]
        evidence = analysis.beach_distance_evidence if analysis.beach_distance_m is not None else None
        house.analysis = Analysis(
            input_hash=input_hash,
            summary=analysis.summary,
            confidence_note=analysis.confidence_note,
            model=model,
            beach_distance_m=analysis.beach_distance_m,
            beach_distance_source=evidence.source if evidence else None,
            beach_distance_quote=evidence.quote if evidence else None,
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

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()


@dataclass(frozen=True)
class SearchInfo:
    """What the stored last search was run for."""

    check_in: date
    check_out: date
    guests: int


class SearchRepository:
    """The last search and the screen results. Callers own the transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replace_last_search(self, trip: TripConfig, hits: list[SearchHit]) -> None:
        """Store these hits as the only search, once per house (the first location wins), in order."""
        await self.session.execute(delete(SearchHitRow))
        await self.session.execute(delete(SearchRun))
        run = SearchRun(check_in=trip.check_in, check_out=trip.check_out, guests=trip.max_people)
        seen: set[str] = set()
        for hit in hits:
            if hit.airbnb_id in seen:
                continue
            seen.add(hit.airbnb_id)
            run.hits.append(
                SearchHitRow(
                    location=hit.location, airbnb_id=hit.airbnb_id, url=hit.url, title=hit.title, position=len(seen)
                )
            )
        self.session.add(run)
        await self.session.flush()

    async def last_search_info(self) -> SearchInfo | None:
        run = await self.session.scalar(select(SearchRun).order_by(SearchRun.id.desc()).limit(1))
        return SearchInfo(run.check_in, run.check_out, run.guests) if run else None

    async def last_search_ids(self) -> list[str]:
        return [hit.airbnb_id for hit in await self.last_search_hits()]

    async def last_search_hits(self) -> list[SearchHit]:
        rows = await self.session.scalars(select(SearchHitRow).order_by(SearchHitRow.position))
        return [
            SearchHit(airbnb_id=row.airbnb_id, url=row.url, title=row.title, location=row.location, rating=None)
            for row in rows
        ]

    async def save_screen(
        self,
        airbnb_id: str,
        url: str,
        title: str | None,
        input_hash: str,
        model: str,
        passed: bool,
        verdicts: list[tuple[str, ScreenStatus, str, str]],
    ) -> None:
        """Store the screen result of a house, replacing any previous one."""
        previous = await self.get_screen(airbnb_id)
        if previous is not None:
            await self.session.delete(previous)
            await self.session.flush()
        self.session.add(
            ScreenResult(
                airbnb_id=airbnb_id,
                url=url,
                title=title,
                passed=passed,
                input_hash=input_hash,
                model=model,
                verdicts=[
                    ScreenVerdictRow(aspect_name=name, status=status, reason=reason, quote=quote)
                    for name, status, reason, quote in verdicts
                ],
            )
        )
        await self.session.flush()

    async def get_screen(self, airbnb_id: str) -> ScreenResult | None:
        return await self.session.scalar(select(ScreenResult).where(ScreenResult.airbnb_id == airbnb_id))
