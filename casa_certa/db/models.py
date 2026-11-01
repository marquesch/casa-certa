"""ORM tables. Everything is a typed column; child data lives in child tables (no JSON columns)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy import Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from casa_certa.config import TripConfig
from casa_certa.enums import AspectStatus, EvidenceSource, PointKind, RuleKind, ScreenStatus
from casa_certa.names import normalize_name


def utcnow() -> datetime:
    """Naive UTC timestamp; the database stores every datetime as naive UTC."""
    return datetime.now(UTC).replace(tzinfo=None)


def _enum_column(enum_class: type[StrEnum]) -> Enum:
    """Store enum values as short strings guarded by a check constraint."""
    return Enum(
        enum_class,
        native_enum=False,
        length=16,
        create_constraint=True,
        values_callable=lambda members: [member.value for member in members],
    )


class Base(DeclarativeBase):
    pass


class House(Base):
    __tablename__ = "house"

    id: Mapped[int] = mapped_column(primary_key=True)
    airbnb_id: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    extra: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    facts: Mapped[Facts | None] = relationship(cascade="all, delete-orphan", lazy="selectin")
    amenities: Mapped[list[Amenity]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="Amenity.id"
    )
    rooms: Mapped[list[Room]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="Room.position")
    highlights: Mapped[list[Highlight]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="Highlight.id"
    )
    rules: Mapped[list[HouseRule]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="HouseRule.id"
    )
    reviews: Mapped[list[Review]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="Review.id")
    quotes: Mapped[list[PriceQuote]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    analysis: Mapped[Analysis | None] = relationship(cascade="all, delete-orphan", lazy="selectin")

    def quote_for(self, trip: TripConfig, guests: int) -> PriceQuote | None:
        """The stored price for exactly this trip's dates, infants and the given number of paying guests, if any."""
        return self.quotes_by_guests(trip).get(guests)

    def quotes_by_guests(self, trip: TripConfig) -> dict[int, PriceQuote]:
        """The stored prices for this trip's dates and infants, keyed by the number of paying guests."""
        return {
            quote.guests: quote
            for quote in self.quotes
            if (quote.check_in, quote.check_out, quote.infants) == (trip.check_in, trip.check_out, trip.infants)
        }


class Facts(Base):
    __tablename__ = "house_facts"

    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("house.id"), unique=True)
    title: Mapped[str | None] = mapped_column(Text)
    property_type: Mapped[str | None] = mapped_column(Text)
    person_capacity: Mapped[int | None] = mapped_column(Integer)
    bedrooms: Mapped[int | None] = mapped_column(Integer)
    beds: Mapped[int | None] = mapped_column(Integer)
    bathrooms: Mapped[float | None] = mapped_column(Float)
    rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    location_name: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    is_guest_favorite: Mapped[bool | None] = mapped_column(Boolean)
    rating_cleanliness: Mapped[float | None] = mapped_column(Float)
    rating_accuracy: Mapped[float | None] = mapped_column(Float)
    rating_checkin: Mapped[float | None] = mapped_column(Float)
    rating_communication: Mapped[float | None] = mapped_column(Float)
    rating_location: Mapped[float | None] = mapped_column(Float)
    rating_value: Mapped[float | None] = mapped_column(Float)
    host_name: Mapped[str | None] = mapped_column(Text)
    host_is_superhost: Mapped[bool | None] = mapped_column(Boolean)
    host_rating: Mapped[float | None] = mapped_column(Float)
    host_review_count: Mapped[int | None] = mapped_column(Integer)
    host_years: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Amenity(Base):
    __tablename__ = "amenity"

    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("house.id"))
    group: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    available: Mapped[bool] = mapped_column(Boolean)
    note: Mapped[str] = mapped_column(Text, default="")


class Room(Base):
    __tablename__ = "room"

    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("house.id"))
    name: Mapped[str] = mapped_column(Text)
    beds_text: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer)


class Highlight(Base):
    __tablename__ = "highlight"

    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("house.id"))
    type: Mapped[str] = mapped_column(Text)
    headline: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)


class HouseRule(Base):
    __tablename__ = "house_rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("house.id"))
    kind: Mapped[RuleKind] = mapped_column(_enum_column(RuleKind))
    type: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")


class Review(Base):
    __tablename__ = "review"

    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("house.id"))
    airbnb_review_id: Mapped[str] = mapped_column(String(40), unique=True)
    rating: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    language: Mapped[str | None] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    host_response: Mapped[str | None] = mapped_column(Text)


class PriceQuote(Base):
    __tablename__ = "price_quote"
    __table_args__ = (
        UniqueConstraint("house_id", "check_in", "check_out", "guests", "infants", name="uq_price_quote_guests"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("house.id"))
    check_in: Mapped[date] = mapped_column(Date)
    check_out: Mapped[date] = mapped_column(Date)
    guests: Mapped[int] = mapped_column(Integer)  # paying guests, i.e. the group size; infants are separate
    infants: Mapped[int] = mapped_column(Integer)
    nights: Mapped[int] = mapped_column(Integer)
    total_cents: Mapped[int | None] = mapped_column(Integer)  # None: not available for these dates and guests
    original_total_cents: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str | None] = mapped_column(String(40))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Analysis(Base):
    __tablename__ = "analysis"

    id: Mapped[int] = mapped_column(primary_key=True)
    house_id: Mapped[int] = mapped_column(ForeignKey("house.id"), unique=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    confidence_note: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    beach_distance_m: Mapped[int | None] = mapped_column(Integer)
    beach_distance_source: Mapped[EvidenceSource | None] = mapped_column(
        Enum(
            EvidenceSource,
            native_enum=False,
            length=16,
            create_constraint=True,
            name="ck_analysis_beach_distance_source",
            values_callable=lambda members: [member.value for member in members],
        )
    )
    beach_distance_quote: Mapped[str | None] = mapped_column(Text)

    verdicts: Mapped[list[AspectVerdict]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="AspectVerdict.id"
    )
    points: Mapped[list[AnalysisPoint]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="AnalysisPoint.id"
    )

    def verdict_for(self, aspect_name: str) -> AspectVerdict | None:
        """The verdict for an aspect, matched ignoring case and spacing."""
        wanted = normalize_name(aspect_name)
        return next((v for v in self.verdicts if normalize_name(v.aspect_name) == wanted), None)


class AspectVerdict(Base):
    __tablename__ = "aspect_verdict"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analysis.id"))
    aspect_name: Mapped[str] = mapped_column(Text)
    status: Mapped[AspectStatus] = mapped_column(_enum_column(AspectStatus))
    reason: Mapped[str] = mapped_column(Text)

    evidence: Mapped[list[Evidence]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="Evidence.id"
    )


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    verdict_id: Mapped[int] = mapped_column(ForeignKey("aspect_verdict.id"))
    source: Mapped[EvidenceSource] = mapped_column(_enum_column(EvidenceSource))
    quote: Mapped[str] = mapped_column(Text)


class AnalysisPoint(Base):
    __tablename__ = "analysis_point"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analysis.id"))
    kind: Mapped[PointKind] = mapped_column(_enum_column(PointKind))
    text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)


class SearchRun(Base):
    """The latest search; a new one replaces it."""

    __tablename__ = "search_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    check_in: Mapped[date] = mapped_column(Date)
    check_out: Mapped[date] = mapped_column(Date)
    guests: Mapped[int] = mapped_column(Integer)

    hits: Mapped[list[SearchHitRow]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="SearchHitRow.position"
    )


class SearchHitRow(Base):
    __tablename__ = "search_hit"
    __table_args__ = (UniqueConstraint("search_run_id", "airbnb_id", name="uq_search_hit_run_house"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    search_run_id: Mapped[int] = mapped_column(ForeignKey("search_run.id"))
    location: Mapped[str] = mapped_column(Text)
    airbnb_id: Mapped[str] = mapped_column(String(32))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)


class ScreenResult(Base):
    """The outcome of screening one searched house against the must-haves; kept so it is not screened twice."""

    __tablename__ = "screen_result"

    id: Mapped[int] = mapped_column(primary_key=True)
    airbnb_id: Mapped[str] = mapped_column(String(32), unique=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    passed: Mapped[bool] = mapped_column(Boolean)
    input_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    verdicts: Mapped[list[ScreenVerdictRow]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="ScreenVerdictRow.id"
    )


class ScreenVerdictRow(Base):
    __tablename__ = "screen_verdict"

    id: Mapped[int] = mapped_column(primary_key=True)
    screen_result_id: Mapped[int] = mapped_column(ForeignKey("screen_result.id"))
    aspect_name: Mapped[str] = mapped_column(Text)
    status: Mapped[ScreenStatus] = mapped_column(_enum_column(ScreenStatus))
    reason: Mapped[str] = mapped_column(Text)
    quote: Mapped[str] = mapped_column(Text)
