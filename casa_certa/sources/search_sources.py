"""Search adapters: find candidate houses for a place and dates. Pure `build_input` and `parse_item`."""

import logging
from datetime import date
from typing import Any, ClassVar

from pydantic import BaseModel

from casa_certa.config import TripConfig
from casa_certa.sources.airbnb_page import listing_url, room_id_from_url
from casa_certa.sources.apify import ActorRunner, ApifyError
from casa_certa.sources.protocols import charge_ceiling

log = logging.getLogger(__name__)


class SearchError(ApifyError):
    """A search could not be run or returned nothing usable."""


class SearchQuery(BaseModel):
    location: str
    check_in: date
    check_out: date
    guests: int
    infants: int
    max_results: int
    max_price_per_night: int | None = None
    min_rating: float | None = None

    @classmethod
    def for_location(cls, trip: TripConfig, location: str) -> "SearchQuery":
        """The query for one place of the trip; the group size is the largest the trip allows."""
        search = trip.search
        return cls(
            location=location,
            check_in=trip.check_in,
            check_out=trip.check_out,
            guests=trip.max_people,
            infants=trip.infants,
            max_results=search.max_results if search else 40,
            max_price_per_night=search.max_price_per_night if search else None,
            min_rating=search.min_rating if search else None,
        )


class SearchHit(BaseModel):
    airbnb_id: str
    url: str
    title: str | None = None
    location: str  # the query text that found it


class TriAngleSearch:
    name = "tri_angle"
    ACTOR: ClassVar[str] = "tri_angle/airbnb-scraper"
    # Measured: $0.0201 billed for a 5-result run (about $0.004 per result including the start event).
    RESULT_COST_USD: ClassVar[float] = 0.004

    def __init__(self, runner: ActorRunner) -> None:
        self._runner = runner

    def estimate_usd(self, results: int) -> float:
        return results * self.RESULT_COST_USD

    def build_input(self, query: SearchQuery) -> dict[str, Any]:
        run_input: dict[str, Any] = {
            "locationQueries": [query.location],
            "checkIn": query.check_in.isoformat(),
            "checkOut": query.check_out.isoformat(),
            "adults": query.guests,
            "children": 0,
            "infants": query.infants,
            "pets": 0,
            "maxResults": query.max_results,
            "currency": "BRL",
            "locale": "pt-BR",
        }
        if query.max_price_per_night is not None:
            run_input["priceMax"] = query.max_price_per_night
        return run_input

    def parse_item(self, item: Any, query: SearchQuery) -> SearchHit | None:
        """None when the item is not a usable listing (no room id in its url)."""
        if not isinstance(item, dict):
            return None
        try:
            room_id = room_id_from_url(str(item.get("url") or item.get("id") or ""))
        except ValueError:
            return None
        title = item.get("title")
        return SearchHit(
            airbnb_id=room_id,
            url=listing_url(room_id),
            title=title if isinstance(title, str) else None,
            location=query.location,
        )

    async def search(self, query: SearchQuery) -> list[SearchHit]:
        log.info("search: %s for %r, %d guests", self.name, query.location, query.guests)
        try:
            items = await self._runner.run_actor(
                self.ACTOR,
                self.build_input(query),
                max_items=query.max_results,
                max_total_charge_usd=charge_ceiling(self.estimate_usd(query.max_results)),
            )
        except SearchError:
            raise
        except ApifyError as error:
            raise SearchError(str(error)) from error
        hits = (self.parse_item(item, query) for item in items)
        return list({hit.airbnb_id: hit for hit in hits if hit is not None}.values())
