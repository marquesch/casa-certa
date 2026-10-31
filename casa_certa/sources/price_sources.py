"""Price adapters: one per Apify actor, each with a pure `build_input` and `parse_item`."""

import re
from abc import ABC, abstractmethod
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, ClassVar

from casa_certa.config import TripConfig
from casa_certa.sources.airbnb_page import listing_url
from casa_certa.sources.apify import ActorRunner, ApifyError, PriceData, PriceParseError
from casa_certa.sources.protocols import SourceBatch, charge_ceiling

_BRL_RE = re.compile(r"R\$\s*([\d.]+(?:,\d{1,2})?)")


def parse_brl_cents(text: str) -> int:
    """'R$ 6.431,69' -> 643169; 'R$ 3.675' -> 367500."""
    match = _BRL_RE.search(text) if isinstance(text, str) else None
    if match is None:
        raise PriceParseError(f"Cannot read a BRL amount from {text!r}")
    whole, _, fraction = match.group(1).replace(".", "").partition(",")
    if not whole:
        raise PriceParseError(f"Cannot read a BRL amount from {text!r}")
    return int(whole) * 100 + int((fraction + "00")[:2])


def _positive_brl_cents(text: Any) -> int:
    """Like `parse_brl_cents`, but a price of zero is not a price."""
    cents = parse_brl_cents(text)
    if cents <= 0:
        raise PriceParseError(f"Not a positive amount: {text!r}")
    return cents


def _cents(amount: Any) -> int:
    """A positive, finite amount in reais (number or numeric string) as cents; anything else is a PriceParseError."""
    if isinstance(amount, bool):
        raise PriceParseError(f"Not an amount: {amount!r}")
    try:
        value = Decimal(str(amount))
        if not value.is_finite():
            raise PriceParseError(f"Not a finite amount: {amount!r}")
        cents = int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise PriceParseError(f"Not an amount: {amount!r}") from error
    if cents <= 0:
        raise PriceParseError(f"Not a positive amount: {amount!r}")
    return cents


def _price_block(item: dict[str, Any], key: str) -> dict[str, Any]:
    """The nested price object of an item; a missing one is empty, a wrong type is a PriceParseError."""
    price = item.get(key)
    if price is None:
        return {}
    if not isinstance(price, dict):
        raise PriceParseError(f"Unexpected {key!r} for listing: {price!r}")
    return price


class PriceActorSource(ABC):
    """Runs one price actor for one group size. Subclasses give the actor, its input and how to read an item."""

    name: str
    ACTOR: ClassVar[str]
    QUOTE_COST_USD: ClassVar[float]

    @abstractmethod
    def build_input(self, room_ids: list[str], trip: TripConfig, guests: int) -> dict[str, Any]: ...

    @abstractmethod
    def item_room_id(self, item: dict[str, Any]) -> str: ...

    @abstractmethod
    def parse_item(self, item: dict[str, Any]) -> PriceData: ...

    def __init__(self, runner: ActorRunner) -> None:
        self._runner = runner

    def estimate_usd(self, quotes: int) -> float:
        return quotes * self.QUOTE_COST_USD

    async def fetch_prices(self, room_ids: list[str], trip: TripConfig, guests: int) -> SourceBatch[PriceData]:
        """One actor run quoting `guests` paying guests for all houses. Houses without a usable result map to an
        ApifyError; a house that is not available for that group maps to a PriceData without a total."""
        try:
            items = await self._runner.run_actor(
                self.ACTOR,
                self.build_input(room_ids, trip, guests),
                max_items=len(room_ids),
                max_total_charge_usd=charge_ceiling(self.estimate_usd(len(room_ids))),
            )
        except ApifyError as error:
            return SourceBatch(dict.fromkeys(room_ids, error))
        results: dict[str, PriceData | ApifyError] = {
            room_id: ApifyError(f"No price result for listing {room_id}") for room_id in room_ids
        }
        for item in items:
            if not isinstance(item, dict):
                continue
            room_id = self.item_room_id(item)
            if room_id not in results:
                continue
            try:
                results[room_id] = self.parse_item(item)
            except ApifyError as error:
                results[room_id] = error
        return SourceBatch(results)


class ThelastscraperPrice(PriceActorSource):
    name = "thelastscraper"
    ACTOR = "thelastscraper/airbnb-listing-details"
    QUOTE_COST_USD = 0.0015

    def build_input(self, room_ids: list[str], trip: TripConfig, guests: int) -> dict[str, Any]:
        return {
            "where": [listing_url(room_id) for room_id in room_ids],
            "checkIn": trip.check_in.isoformat(),
            "checkOut": trip.check_out.isoformat(),
            "adults": guests,
            "children": 0,
            "infants": trip.infants,
            "currency": "BRL",
            "language": trip.language[:2],
            "outputFormat": "full_json",
        }

    def item_room_id(self, item: dict[str, Any]) -> str:
        return str(item.get("listing_id"))

    def parse_item(self, item: dict[str, Any]) -> PriceData:
        price = _price_block(item, "price")
        room_id = item.get("listing_id")
        if price.get("dates_match_query") is False:
            raise PriceParseError(f"The quote for listing {room_id} is for other dates than the ones asked")
        if price.get("is_available") is False:
            return PriceData(total_cents=None, source=self.name)
        if price.get("total") is None:
            raise PriceParseError(f"No price for listing {room_id}")
        original = price.get("total_display_original")
        return PriceData(
            total_cents=_cents(price["total"]),
            original_total_cents=None if original is None else _cents(original),
            source=self.name,
        )


class TriAnglePrice(PriceActorSource):
    name = "tri_angle"
    ACTOR = "tri_angle/airbnb-rooms-urls-scraper"
    QUOTE_COST_USD = 0.004

    def build_input(self, room_ids: list[str], trip: TripConfig, guests: int) -> dict[str, Any]:
        return {
            "startUrls": [{"url": listing_url(room_id)} for room_id in room_ids],
            "checkIn": trip.check_in.isoformat(),
            "checkOut": trip.check_out.isoformat(),
            "adults": guests,
            "children": 0,
            "infants": trip.infants,
            "currency": "BRL",
            "locale": trip.language,
        }

    def item_room_id(self, item: dict[str, Any]) -> str:
        return str(item.get("id"))

    def parse_item(self, item: dict[str, Any]) -> PriceData:
        if item.get("isAvailable") is False:
            return PriceData(total_cents=None, source=self.name)
        price = _price_block(item, "price")
        shown = price.get("price")
        if not shown:
            raise PriceParseError(f"No price for listing {item.get('id')} (unavailable on these dates?)")
        original = price.get("originalPrice")
        return PriceData(
            total_cents=_positive_brl_cents(shown),
            original_total_cents=_positive_brl_cents(original) if original else None,
            source=self.name,
        )
