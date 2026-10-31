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


def default_sort_key(card: HouseCard, people: int) -> tuple[bool, bool, int, float, str]:
    """Analysed houses first; then cheapest per person at `people`, houses without a price last, best rated, name."""
    price = card.per_person_cents(people)
    return (not card.analysed, price is None, price or 0, -(card.rating or 0.0), card.name)


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
