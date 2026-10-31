"""Extract everything possible from an Airbnb listing page with a plain HTTP request.

The page ships with embedded JSON (`data-deferred-state-0`) holding the listing's
static data. It does NOT contain review text or the price for given dates (both are
loaded later through GraphQL). Use other sources for those.

Async: use an httpx.AsyncClient from make_client().
"""

from __future__ import annotations

import asyncio
import base64
import html as html_lib
import json
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

DEFAULT_DOMAIN = "www.airbnb.com.br"
DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"
DEFAULT_ACCEPT_LANGUAGE = "pt-BR,pt;q=0.9"

_STATE_RE = re.compile(r'<script id="data-deferred-state-0"[^>]*>(.*?)</script>', re.S)
_ROOM_ID_RE = re.compile(r"/rooms/(?:plus/)?(\d+)")


class AirbnbPageError(Exception):
    """Base error for fetching or interpreting the page."""


class PageFetchError(AirbnbPageError):
    """The request failed or was blocked (HTTP != 200)."""


class PageParseError(AirbnbPageError):
    """The page came back but lacks the expected structure (layout change or block)."""


# --------------------------------------------------------------------------- model


class Amenity(BaseModel):
    group: str
    name: str
    available: bool
    note: str = ""


class Room(BaseModel):
    name: str
    beds: list[str]


class Highlight(BaseModel):
    type: str
    headline: str
    body: str


class RuleItem(BaseModel):
    group: str
    type: str
    title: str
    description: str = ""


class Host(BaseModel):
    name: str | None = None
    is_superhost: bool | None = None
    is_verified: bool | None = None
    rating: float | None = None
    rating_count: int | None = None
    years_hosting: int | None = None
    response_rate: str | None = None
    response_time: str | None = None


class ListingFacts(BaseModel):
    listing_id: str
    title: str | None = None
    property_type: str | None = None
    space_type: str | None = None
    person_capacity: int | None = None
    overview: list[str] = Field(default_factory=list)  # as shown by Airbnb, e.g. ["12 hóspedes", "4 quartos"]
    bedrooms: int | None = None
    beds: int | None = None
    bathrooms: float | None = None
    rating: float | None = None
    review_count: int | None = None
    category_ratings: dict[str, float] = Field(default_factory=dict)
    is_guest_favorite: bool | None = None
    description: str | None = None
    amenities: list[Amenity] = Field(default_factory=list)
    rooms: list[Room] = Field(default_factory=list)
    highlights: list[Highlight] = Field(default_factory=list)
    location_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_is_exact: bool | None = None
    host: Host = Field(default_factory=Host)
    house_rules: list[RuleItem] = Field(default_factory=list)
    safety: list[RuleItem] = Field(default_factory=list)

    def available_amenities(self) -> list[str]:
        return [a.name for a in self.amenities if a.available]

    def unavailable_amenities(self) -> list[str]:
        return [a.name for a in self.amenities if not a.available]


# --------------------------------------------------------------------------- helpers


def room_id_from_url(url_or_id: str) -> str:
    """Accept a numeric id or a /rooms/<id> URL and return the id."""
    s = str(url_or_id).strip()
    if s.isdigit():
        return s
    m = _ROOM_ID_RE.search(s)
    if not m:
        raise ValueError(f"Could not find a listing id in: {url_or_id!r}")
    return m.group(1)


def listing_url(room_id: str, *, domain: str = DEFAULT_DOMAIN) -> str:
    return f"https://{domain}/rooms/{room_id}"


def _dig(obj: Any, *path: str | int, default: Any = None) -> Any:
    """Walk nested dicts/lists, returning `default` if anything is missing."""
    cur = obj
    for key in path:
        try:
            cur = cur[key]
        except (KeyError, IndexError, TypeError):
            return default
        if cur is None:
            return default
    return cur


def _text(x: Any) -> str | None:
    """Extract text from any of the text shapes Airbnb uses."""
    if x is None:
        return None
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        for key in (
            "localizedString",
            "source",
            "localizedStringWithTranslationPreference",
            "localizedContent",
            "text",
        ):
            v = x.get(key)
            if isinstance(v, str):
                return v
        if "content" in x:
            return _text(x["content"])
    return None


def html_to_text(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    s = re.sub(r"</(p|div|li)>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html_lib.unescape(s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def _to_int(x: Any) -> int | None:
    try:
        return int(str(x).replace(".", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _num_from_overview(items: list[str], pattern: str) -> float | None:
    for item in items:
        m = re.search(pattern, item, re.I)
        if m:
            return float(m.group(1).replace(",", "."))
    return None


# --------------------------------------------------------------------------- parsing


def _load_state(page_html: str) -> dict[str, Any]:
    m = _STATE_RE.search(page_html)
    if not m:
        raise PageParseError(
            "Embedded JSON (data-deferred-state-0) not found: possible block/captcha or layout change."
        )
    try:
        state = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise PageParseError(f"Embedded JSON is invalid: {e}") from e
    if not isinstance(state, dict):
        raise PageParseError("Embedded JSON has an unexpected shape.")
    return state


def parse_listing_html(page_html: str) -> ListingFacts:
    state = _load_state(page_html)
    data = _dig(state, "niobeClientData", 0, 1, "data")
    node = _dig(data, "node")
    pdp = _dig(node, "pdpPresentation")
    if not isinstance(node, dict) or not isinstance(pdp, dict):
        raise PageParseError("Listing structure (node/pdpPresentation) not found.")
    try:
        return _extract_facts(data, node, pdp)
    except (AttributeError, TypeError, KeyError, IndexError) as e:
        raise PageParseError(f"Listing data has an unexpected shape: {e!r}") from e


def _extract_facts(data: Any, node: dict[str, Any], pdp: dict[str, Any]) -> ListingFacts:
    meta = _dig(data, "presentation", "stayProductDetailPage", "sections", "metadata", default={})
    sharing = _dig(meta, "sharingConfig", default={})

    overview = [s for s in _dig(pdp, "overview", "items", default=[]) if isinstance(s, str)]
    stats = _dig(pdp, "quality", "listingRatingStats", "overallRatingStats", default={})

    return ListingFacts(
        listing_id=_decode_id(node.get("id", ""))
        or str(_dig(meta, "loggingContext", "eventDataLogging", "listingId", default="")),
        title=_text(_dig(pdp, "title")),
        property_type=sharing.get("propertyType") if isinstance(sharing, dict) else None,
        space_type=node.get("spaceType"),
        person_capacity=node.get("personCapacity") or _dig(sharing, "personCapacity"),
        overview=overview,
        bedrooms=_as_int(_num_from_overview(overview, r"(\d+)\s+(?:quartos?|bedrooms?)")),
        beds=_as_int(_num_from_overview(overview, r"(\d+)\s+(?:camas?|beds?)")),
        bathrooms=_num_from_overview(overview, r"(\d+(?:[.,]\d+)?)\s+(?:banheiros?|bathrooms?|baths?)"),
        rating=_dig(stats, "ratingAverage") or _dig(sharing, "starRating"),
        review_count=_to_int(_dig(stats, "ratingCount") or _dig(sharing, "reviewCount")),
        category_ratings=_category_ratings(node),
        is_guest_favorite=_dig(pdp, "quality", "isGuestFavorite"),
        description=html_to_text(_text(_dig(pdp, "descriptions", "longDescriptionHtml"))),
        amenities=_amenities(pdp),
        rooms=_rooms(pdp),
        highlights=_highlights(pdp),
        location_name=_dig(pdp, "location", "subtitle") or _dig(pdp, "localizedLocation"),
        latitude=_dig(pdp, "location", "latitude") or _dig(node, "location", "coordinate", "latitude"),
        longitude=_dig(pdp, "location", "longitude") or _dig(node, "location", "coordinate", "longitude"),
        location_is_exact=_dig(pdp, "location", "isExactLocation"),
        host=_host(pdp),
        house_rules=_rule_items(_dig(pdp, "rules", "groupItems", default=[])),
        safety=_rule_items(_dig(pdp, "safetyAndProperty", "groupItems", default=[])),
    )


def _as_int(x: float | None) -> int | None:
    return None if x is None else int(x)


def _decode_id(node_id: str) -> str:
    """`node.id` comes base64-encoded ('StayListing:123'); return it as-is if numeric."""
    if str(node_id).isdigit():
        return str(node_id)
    try:
        return base64.b64decode(node_id).decode().split(":")[-1]
    except ValueError:  # not valid base64 / not utf-8 (binascii.Error and UnicodeDecodeError are ValueErrors)
        return ""


def _category_ratings(node: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in _dig(node, "listingRatingStats", "categoryRatingStats", default=[]):
        name = item.get("categoryTypeA")
        value = _dig(item, "value", "ratingAverage")
        if name and value is not None:
            out[name.lower()] = value
    return out


def _amenities(pdp: dict[str, Any]) -> list[Amenity]:
    out: list[Amenity] = []
    for group in _dig(pdp, "amenities", "seeAllAmenitiesGroups", default=[]):
        gname = group.get("title") or ""
        for a in group.get("amenities") or []:
            out.append(
                Amenity(
                    group=gname,
                    name=a.get("title") or "",
                    available=bool(a.get("available")),
                    note=_text(a.get("subtitle")) or "",
                )
            )
    return out


def _rooms(pdp: dict[str, Any]) -> list[Room]:
    out: list[Room] = []
    for stop in _dig(pdp, "sleepingArrangements", "stops", default=[]):
        beds = [_text(d) or "" for d in _dig(stop, "description", "descriptions", default=[])]
        out.append(Room(name=stop.get("name") or "", beds=[b for b in beds if b]))
    return out


def _highlights(pdp: dict[str, Any]) -> list[Highlight]:
    return [
        Highlight(
            type=h.get("type") or "",
            headline=_text(h.get("headline")) or "",
            body=_text(h.get("body")) or "",
        )
        for h in (pdp.get("highlights") or [])
    ]


def _host(pdp: dict[str, Any]) -> Host:
    info = _dig(pdp, "hostInfo", default={})
    passport = _dig(info, "passportData", default={})
    return Host(
        name=passport.get("name"),
        is_superhost=passport.get("isSuperhost"),
        is_verified=passport.get("isVerified"),
        rating=passport.get("ratingAverage"),
        rating_count=_to_int(passport.get("ratingCount")),
        years_hosting=_dig(passport, "timeAsHost", "years"),
        response_rate=_text(info.get("responseRateText")),
        response_time=_text(info.get("responseTimeText")),
    )


def _rule_items(groups: list[dict[str, Any]]) -> list[RuleItem]:
    out: list[RuleItem] = []
    for g in groups or []:
        gtitle = g.get("title") or ""
        for item in g.get("items") or []:
            out.append(
                RuleItem(
                    group=gtitle,
                    type=item.get("type") or "",
                    title=item.get("title") or "",
                    description=_text(item.get("description")) or "",
                )
            )
    return out


# --------------------------------------------------------------------------- network

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
Sleep = Callable[[float], Awaitable[None]]


def make_client(*, timeout: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept-Language": DEFAULT_ACCEPT_LANGUAGE,
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=timeout,
        follow_redirects=True,
    )


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    try:
        return await client.get(url)
    except httpx.HTTPError as e:
        raise PageFetchError(f"Network failure while fetching {url}: {e}") from e


async def fetch_html(
    url: str,
    *,
    client: httpx.AsyncClient,
    retry_wait: float = 10.0,
    sleep: Sleep = asyncio.sleep,
) -> str:
    """GET a page, retrying once (after `retry_wait` seconds) on 429/5xx."""
    response = await _get(client, url)
    if response.status_code in RETRYABLE_STATUSES:
        log.warning("page %s: HTTP %d, retrying once in %.0fs", url, response.status_code, retry_wait)
        await sleep(retry_wait)
        response = await _get(client, url)
    if response.status_code != 200:
        raise PageFetchError(f"HTTP {response.status_code} while fetching {url}")
    return response.text


async def fetch_listing(
    url_or_id: str,
    *,
    client: httpx.AsyncClient,
    retry_wait: float = 10.0,
    sleep: Sleep = asyncio.sleep,
) -> ListingFacts:
    room_id = room_id_from_url(url_or_id)
    page_html = await fetch_html(listing_url(room_id), client=client, retry_wait=retry_wait, sleep=sleep)
    facts = parse_listing_html(page_html)
    if facts.listing_id != room_id:
        raise PageParseError(f"Requested listing {room_id} but Airbnb served listing {facts.listing_id or '?'}")
    return facts


async def fetch_listings(
    urls_or_ids: list[str],
    *,
    client: httpx.AsyncClient,
    delay: tuple[float, float] = (2.0, 5.0),
    sleep: Sleep = asyncio.sleep,
) -> dict[str, ListingFacts | AirbnbPageError]:
    """Fetch several listings sequentially with a random pause between requests.

    A failure on one listing does not stop the others: the error is returned in place of the result.
    """
    results: dict[str, ListingFacts | AirbnbPageError] = {}
    total = len(urls_or_ids)
    for index, item in enumerate(urls_or_ids, start=1):
        if index > 1:
            await sleep(random.uniform(*delay))
        started = time.monotonic()
        try:
            results[item] = await fetch_listing(item, client=client, sleep=sleep)
            log.info("page %d/%d %s: ok in %.1fs", index, total, item, time.monotonic() - started)
        except AirbnbPageError as e:
            results[item] = e
            log.warning("page %d/%d %s: failed (%s)", index, total, item, e)
        except ValueError as e:  # not a listing url or id
            results[item] = PageFetchError(str(e))
            log.warning("page %d/%d %s: skipped (%s)", index, total, item, e)
    return results
