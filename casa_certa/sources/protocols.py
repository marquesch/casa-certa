from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

from casa_certa.config import TripConfig
from casa_certa.sources.apify import ApifyError, PriceData, ReviewData

if TYPE_CHECKING:
    from casa_certa.sources.search_sources import SearchHit, SearchQuery

T = TypeVar("T")

# The charge ceiling of a run, as a multiple of what the adapter expects to pay.
CHARGE_CEILING_FACTOR = 3


def charge_ceiling(estimate_usd: float) -> float:
    """The most an actor run may cost, rounded so Apify never sees float noise like 0.0045000000000000005."""
    return round(CHARGE_CEILING_FACTOR * estimate_usd, 6)


@dataclass
class SourceBatch(Generic[T]):
    """Per-house answers from a source (a value or an ApifyError) plus human-readable notes (e.g. fallback used)."""

    results: dict[str, T | ApifyError]
    notes: dict[str, str] = field(default_factory=dict)


class PriceSource(Protocol):
    name: str

    async def fetch_prices(self, room_ids: list[str], trip: TripConfig, guests: int) -> SourceBatch[PriceData]: ...

    def estimate_usd(self, quotes: int) -> float: ...


class ReviewSource(Protocol):
    name: str

    async def fetch_reviews(
        self, room_ids: list[str], trip: TripConfig, *, expect_reviews: frozenset[str] = frozenset()
    ) -> SourceBatch[list[ReviewData]]: ...

    def estimate_usd(self, houses: int, review_cap: int) -> float: ...


class SearchSource(Protocol):
    name: str

    async def search(self, query: SearchQuery) -> list[SearchHit]: ...

    def estimate_usd(self, results: int) -> float: ...
