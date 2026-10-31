"""Cross-checks the cheap sources against the reference actors on real houses. Skipped by default.

Run with: make test-live-sources (spends a few cents; use -s to read the comparison tables).
"""

import re
from pathlib import Path
from typing import TypeVar

import pytest
from apify_client import ApifyClientAsync

from casa_certa.config import Settings, TripConfig
from casa_certa.config_sources.yaml_source import YamlConfigSource
from casa_certa.sources.airbnb_page import room_id_from_url
from casa_certa.sources.apify import ApifyActorRunner, ApifyError, PriceData, ReviewData
from casa_certa.sources.price_sources import ThelastscraperPrice, TriAnglePrice
from casa_certa.sources.review_sources import KaixReviews, SteadyscrapeReviews

pytestmark = pytest.mark.live

TRIP_FILE = Path(__file__).resolve().parent.parent / "trip.yaml"
MAX_HOUSES = 3
REVIEW_CAP = 15
PRICE_GUESTS = 11

T = TypeVar("T")


async def load_trip() -> TripConfig:
    trip = await YamlConfigSource(TRIP_FILE).load()
    return trip.model_copy(update={"urls": trip.urls[:MAX_HOUSES], "review_cap": REVIEW_CAP})


def make_runner() -> ApifyActorRunner:
    return ApifyActorRunner(ApifyClientAsync(Settings().apify_token.get_secret_value()))


def room_ids_of(trip: TripConfig) -> list[str]:
    return list(dict.fromkeys(room_id_from_url(url) for url in trip.urls))


def answer(source_name: str, room_id: str, results: dict[str, T | ApifyError]) -> T:
    value = results[room_id]
    assert not isinstance(value, ApifyError), f"{source_name} failed for house {room_id}: {value}"
    return value


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def print_table(title: str, header: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    widths = [max(len(line[i]) for line in [header, *rows]) for i in range(len(header))]
    print(f"\n{title}")
    for line in [header, *rows]:
        print("  " + "  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=True)))


def yes_no(value: bool) -> str:
    return "yes" if value else "NO"


def text_mismatches(first: list[ReviewData], second: list[ReviewData]) -> list[str]:
    """Ids shared by both lists whose normalized text differs."""
    second_by_id = {review.airbnb_review_id: review for review in second}
    return [
        review.airbnb_review_id
        for review in first
        if review.airbnb_review_id in second_by_id
        and normalized(review.text) != normalized(second_by_id[review.airbnb_review_id].text)
    ]


async def test_kaix_and_steadyscrape_return_the_same_reviews() -> None:
    trip = await load_trip()
    runner = make_runner()
    kaix, steadyscrape = KaixReviews(runner), SteadyscrapeReviews(runner)
    room_ids = room_ids_of(trip)
    kaix_batch = await kaix.fetch_reviews(room_ids, trip)
    steadyscrape_batch = await steadyscrape.fetch_reviews(room_ids, trip)

    rows: list[tuple[str, ...]] = []
    failures: list[str] = []
    for room_id in room_ids:
        first = answer(kaix.name, room_id, kaix_batch.results)
        second = answer(steadyscrape.name, room_id, steadyscrape_batch.results)
        first_ids = {review.airbnb_review_id for review in first}
        second_ids = {review.airbnb_review_id for review in second}
        different_text = text_mismatches(first, second)
        rows.append(
            (room_id, str(len(first)), str(len(second)), yes_no(first_ids == second_ids and not different_text))
        )
        if first_ids != second_ids:
            failures.append(
                f"{room_id}: review ids differ; only in {kaix.name}: {sorted(first_ids - second_ids)}, "
                f"only in {steadyscrape.name}: {sorted(second_ids - first_ids)}"
            )
        if different_text:
            failures.append(f"{room_id}: {kaix.name} and {steadyscrape.name} disagree on the text of {different_text}")
    print_table(f"Reviews (cap {REVIEW_CAP})", ("house", kaix.name, steadyscrape.name, "equal"), rows)
    assert not failures, "\n".join(failures)


def describe(price: PriceData) -> str:
    return "unavailable" if price.total_cents is None else str(price.total_cents)


async def test_thelastscraper_and_tri_angle_quote_the_same_price() -> None:
    trip = await load_trip()
    guests = (
        PRICE_GUESTS if trip.min_people <= PRICE_GUESTS <= trip.max_people else (trip.min_people + trip.max_people) // 2
    )
    runner = make_runner()
    cheap, reference = ThelastscraperPrice(runner), TriAnglePrice(runner)
    room_ids = room_ids_of(trip)
    cheap_batch = await cheap.fetch_prices(room_ids, trip, guests)
    reference_batch = await reference.fetch_prices(room_ids, trip, guests)

    rows: list[tuple[str, ...]] = []
    failures: list[str] = []
    for room_id in room_ids:
        first = answer(cheap.name, room_id, cheap_batch.results)
        second = answer(reference.name, room_id, reference_batch.results)
        equal = first.total_cents == second.total_cents  # None on both sides means both say "unavailable"
        rows.append((room_id, describe(first), describe(second), yes_no(equal)))
        if not equal:
            failures.append(
                f"{room_id}: {cheap.name} says {describe(first)} but {reference.name} says {describe(second)} "
                f"for {guests} guests"
            )
    print_table(f"Price in cents for {guests} guests", ("house", cheap.name, reference.name, "equal"), rows)
    assert not failures, "\n".join(failures)
