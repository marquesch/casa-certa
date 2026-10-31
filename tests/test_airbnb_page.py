import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from casa_certa.sources.airbnb_page import (
    ListingFacts,
    PageFetchError,
    PageParseError,
    fetch_html,
    fetch_listing,
    fetch_listings,
    html_to_text,
    listing_url,
    parse_listing_html,
    room_id_from_url,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return parse_listing_html((FIXTURES / name).read_text(encoding="utf8"))


@pytest.fixture(scope="module")
def matinhos():
    return load("room_46397403.html")


@pytest.fixture(scope="module")
def itapoa():
    return load("room_52584351.html")


def test_room_id_from_url():
    url = "https://www.airbnb.com.br/rooms/52584351?adults=10&check_in=2026-12-05"
    assert room_id_from_url(url) == "52584351"
    assert room_id_from_url("46397403") == "46397403"
    with pytest.raises(ValueError):
        room_id_from_url("https://example.com/nada")


def test_listing_url() -> None:
    assert listing_url("1") == "https://www.airbnb.com.br/rooms/1"


def test_html_to_text():
    assert html_to_text("a<br />b &amp; c") == "a\nb & c"


def test_basics_matinhos(matinhos):
    assert matinhos.listing_id == "46397403"
    assert matinhos.title == "Sobrado Praia Albatroz, 100 metros do mar, Piscina"
    assert matinhos.person_capacity == 12
    assert (matinhos.bedrooms, matinhos.beds, matinhos.bathrooms) == (4, 9, 5)
    assert matinhos.rating == 4.95
    assert matinhos.review_count == 87
    assert matinhos.category_ratings["cleanliness"] == 4.85
    assert matinhos.is_guest_favorite is True
    assert matinhos.location_name == "Matinhos, Paraná, Brasil"
    assert matinhos.latitude == pytest.approx(-25.74)
    assert "100 metros do mar" in matinhos.description


def test_amenities_flag_missing_ones(matinhos):
    assert "Piscina privativa" in matinhos.available_amenities()
    assert "Ar-condicionado" in matinhos.unavailable_amenities()
    assert "Ar-condicionado" not in matinhos.available_amenities()


def test_host_matinhos(matinhos):
    assert matinhos.host.name == "Anfitriao A"
    assert matinhos.host.is_superhost is True
    assert matinhos.host.rating == 4.95
    assert matinhos.host.rating_count == 87


def test_rules_and_rooms(matinhos):
    titles = [r.title for r in matinhos.house_rules]
    assert "Até 12 hóspedes para pernoite" in titles
    assert len(matinhos.rooms) == 5
    assert matinhos.rooms[0].beds == ["1 beliche"]


def test_itapoa_few_reviews_and_air_conditioning(itapoa):
    assert itapoa.listing_id == "52584351"
    assert itapoa.person_capacity == 16
    assert itapoa.review_count == 10
    assert (itapoa.bedrooms, itapoa.beds, itapoa.bathrooms) == (6, 12, 3.5)
    assert "Ar-condicionado" in itapoa.available_amenities()
    # the free text reveals that only some bedrooms have A/C
    assert "ventilador" in itapoa.description


def test_model_dump_is_json_serializable(matinhos: ListingFacts) -> None:
    matinhos.model_dump_json()


def test_missing_state_raises():
    with pytest.raises(PageParseError):
        parse_listing_html("<html>captcha</html>")


def mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_html_retries_once_on_429() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429 if len(calls) == 1 else 200, text="ok")

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async with mock_client(handler) as client:
        text = await fetch_html("https://x/rooms/1", client=client, retry_wait=7, sleep=fake_sleep)
    assert text == "ok"
    assert sleeps == [7]


async def test_fetch_html_raises_after_second_failure() -> None:
    async def fake_sleep(seconds: float) -> None:
        return None

    async with mock_client(lambda request: httpx.Response(503)) as client:
        with pytest.raises(PageFetchError, match="HTTP 503"):
            await fetch_html("https://x/rooms/1", client=client, sleep=fake_sleep)


async def test_fetch_html_raises_on_blocked_status() -> None:
    async with mock_client(lambda request: httpx.Response(403)) as client:
        with pytest.raises(PageFetchError, match="HTTP 403"):
            await fetch_html("https://x/rooms/1", client=client)


async def test_fetch_listings_pauses_between_requests_and_isolates_errors() -> None:
    pages = {room: (FIXTURES / f"room_{room}.html").read_text(encoding="utf8") for room in ("46397403", "52584351")}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/222"):
            return httpx.Response(403)
        return httpx.Response(200, text=pages[request.url.path.rsplit("/", 1)[-1]])

    pauses: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        pauses.append(seconds)

    async with mock_client(handler) as client:
        out = await fetch_listings(["46397403", "222", "52584351"], client=client, delay=(2, 5), sleep=fake_sleep)

    assert len(pauses) == 2  # no pause before the first request
    assert all(2 <= p <= 5 for p in pauses)
    assert isinstance(out["46397403"], ListingFacts)
    assert isinstance(out["222"], PageFetchError)
    assert isinstance(out["52584351"], ListingFacts)


async def test_fetch_listings_reports_bad_ids_as_errors() -> None:
    async with mock_client(lambda request: httpx.Response(200, text="")) as client:
        out = await fetch_listings(["not-a-listing"], client=client)
    assert isinstance(out["not-a-listing"], PageFetchError)


def corrupt_state(name: str, corrupt: Callable[[dict[str, Any]], None]) -> str:
    """The fixture page with its embedded JSON changed in place by `corrupt`."""
    page = (FIXTURES / name).read_text(encoding="utf8")
    match = re.search(r'(<script id="data-deferred-state-0"[^>]*>)(.*?)(</script>)', page, re.S)
    assert match is not None
    state = json.loads(match.group(2))
    corrupt(state)
    return page[: match.start(2)] + json.dumps(state) + page[match.end(2) :]


def pdp_of(state: dict[str, Any]) -> dict[str, Any]:
    pdp: dict[str, Any] = state["niobeClientData"][0][1]["data"]["node"]["pdpPresentation"]
    return pdp


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda state: pdp_of(state)["overview"].update(items=5),
        lambda state: pdp_of(state).update(rules={"groupItems": 5}),
    ],
)
def test_unexpected_page_shapes_raise_page_parse_error(corrupt) -> None:
    with pytest.raises(PageParseError):
        parse_listing_html(corrupt_state("room_46397403.html", corrupt))


async def test_fetch_listing_rejects_a_redirect_to_another_listing() -> None:
    other = (FIXTURES / "room_52584351.html").read_text(encoding="utf8")
    client = mock_client(lambda request: httpx.Response(200, text=other))

    with pytest.raises(PageParseError, match="46397403"):
        await fetch_listing("46397403", client=client)


async def test_fetch_listing_accepts_the_requested_listing() -> None:
    page = (FIXTURES / "room_46397403.html").read_text(encoding="utf8")
    client = mock_client(lambda request: httpx.Response(200, text=page))

    assert (await fetch_listing("46397403", client=client)).listing_id == "46397403"
