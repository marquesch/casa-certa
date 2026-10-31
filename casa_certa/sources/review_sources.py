"""Review adapters: one per Apify actor, each with a pure `build_input` and `parse`."""

import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any, ClassVar

from casa_certa.config import TripConfig
from casa_certa.sources.airbnb_page import html_to_text, listing_url
from casa_certa.sources.apify import ActorRunner, ApifyError, ReviewData, ReviewParseError
from casa_certa.sources.protocols import SourceBatch, charge_ceiling


def review_data(review_id: Any, text: Any, created_at: Any, rating: Any, language: Any, response: Any) -> ReviewData:
    """Builds a review from an actor's plain fields. Raises ReviewParseError without an id or a readable date."""
    try:
        if review_id is None:
            raise ValueError("no id")
        moment = datetime.fromisoformat(created_at)
        if moment.tzinfo is not None:
            moment = moment.astimezone(UTC).replace(tzinfo=None)
        return ReviewData(
            airbnb_review_id=str(review_id),
            rating=rating,
            created_at=moment,
            language=language,
            text=html_to_text(text) if isinstance(text, str) else "",
            host_response=html_to_text(response) if isinstance(response, str) else None,
        )
    except (ValueError, TypeError) as e:
        raise ReviewParseError(f"Malformed review item {review_id!r}: {e!r}") from e


class ReviewActorSource(ABC):
    """Runs one review actor for all houses. Subclasses give the actor, its input and how to read the items."""

    name: str
    ACTOR: ClassVar[str]

    def __init__(self, runner: ActorRunner) -> None:
        self._runner = runner

    @abstractmethod
    def build_input(self, room_ids: list[str], trip: TripConfig) -> dict[str, Any]: ...

    @abstractmethod
    def max_items(self, houses: int, review_cap: int) -> int: ...

    @abstractmethod
    def parse(self, items: list[dict[str, Any]], room_ids: list[str]) -> dict[str, list[ReviewData]]:
        """Reviews per requested house; a malformed review is skipped so the paid-for others are kept."""

    @abstractmethod
    def estimate_usd(self, houses: int, review_cap: int) -> float: ...

    async def fetch_reviews(
        self, room_ids: list[str], trip: TripConfig, *, expect_reviews: frozenset[str] = frozenset()
    ) -> SourceBatch[list[ReviewData]]:
        """The most recent `trip.review_cap` reviews per house; houses without reviews map to []."""
        if trip.review_cap == 0 or not room_ids:
            return SourceBatch({room_id: [] for room_id in room_ids})
        try:
            items = await self._runner.run_actor(
                self.ACTOR,
                self.build_input(room_ids, trip),
                max_items=self.max_items(len(room_ids), trip.review_cap),
                max_total_charge_usd=charge_ceiling(self.estimate_usd(len(room_ids), trip.review_cap)),
            )
        except ApifyError as error:
            return SourceBatch(dict.fromkeys(room_ids, error))
        return SourceBatch(dict(self.parse(items, room_ids)))


class KaixReviews(ReviewActorSource):
    name = "kaix"
    ACTOR = "kaix/airbnb-listing-scraper"

    def build_input(self, room_ids: list[str], trip: TripConfig) -> dict[str, Any]:
        return {
            "listings": [listing_url(room_id) for room_id in room_ids],
            "includeReviews": True,
            "maxReviewsPerListing": trip.review_cap,
            "reviewsSortBy": "MOST_RECENT",
        }

    def max_items(self, houses: int, review_cap: int) -> int:
        return houses  # one item per listing, the reviews are nested in it

    def parse(self, items: list[dict[str, Any]], room_ids: list[str]) -> dict[str, list[ReviewData]]:
        results: dict[str, list[ReviewData]] = {room_id: [] for room_id in room_ids}
        for item in items:
            if not isinstance(item, dict):
                continue
            room_id = str(item.get("id"))
            if room_id not in results:
                continue
            raw_reviews = item.get("reviews")
            for raw in raw_reviews if isinstance(raw_reviews, list) else []:
                if not isinstance(raw, dict):
                    continue
                response = raw.get("response")
                try:
                    results[room_id].append(
                        review_data(
                            raw.get("id"),
                            raw.get("comments"),
                            raw.get("createdAt"),
                            raw.get("rating"),
                            raw.get("language"),
                            response,
                        )
                    )
                except ReviewParseError:
                    continue
        return results

    def estimate_usd(self, houses: int, review_cap: int) -> float:
        return 0.00005 + houses * (0.0001 + review_cap * 0.00002)


class SteadyscrapeReviews(ReviewActorSource):
    name = "steadyscrape"
    ACTOR = "steadyscrape/airbnb-reviews-scraper"

    def build_input(self, room_ids: list[str], trip: TripConfig) -> dict[str, Any]:
        return {
            "listings": [listing_url(room_id) for room_id in room_ids],
            "maxReviewsPerListing": trip.review_cap,
            "sortBy": "MOST_RECENT",
        }

    def max_items(self, houses: int, review_cap: int) -> int:
        return houses * review_cap

    def parse(self, items: list[dict[str, Any]], room_ids: list[str]) -> dict[str, list[ReviewData]]:
        results: dict[str, list[ReviewData]] = {room_id: [] for room_id in room_ids}
        for item in items:
            if not isinstance(item, dict):
                continue
            room_id = str(item.get("listingId"))
            if room_id not in results:
                continue
            try:
                results[room_id].append(
                    review_data(
                        item.get("reviewId"),
                        item.get("text"),
                        item.get("createdAt"),
                        item.get("rating"),
                        item.get("language"),
                        item.get("hostResponse"),
                    )
                )
            except ReviewParseError:
                continue
        return results

    def estimate_usd(self, houses: int, review_cap: int) -> float:
        return houses * review_cap * 0.0005


class TriAngleReviews(ReviewActorSource):
    name = "tri_angle"
    ACTOR = "tri_angle/airbnb-reviews-scraper"

    def build_input(self, room_ids: list[str], trip: TripConfig) -> dict[str, Any]:
        return {
            "startUrls": [{"url": listing_url(room_id)} for room_id in room_ids],
            "maxReviewsPerListing": trip.review_cap,
            "sortBy": "most-recent",
            "locale": trip.language,
        }

    def max_items(self, houses: int, review_cap: int) -> int:
        return houses * review_cap

    def parse(self, items: list[dict[str, Any]], room_ids: list[str]) -> dict[str, list[ReviewData]]:
        results: dict[str, list[ReviewData]] = {room_id: [] for room_id in room_ids}
        for item in items:
            if not isinstance(item, dict):
                continue
            match = re.search(r"/rooms/(\d+)", str(item.get("startUrl", "")))
            if match and match.group(1) in results:
                try:
                    results[match.group(1)].append(parse_review_item(item))
                except ReviewParseError:
                    continue
        return results

    def estimate_usd(self, houses: int, review_cap: int) -> float:
        return 0.0001 + houses * review_cap * 0.005


def parse_review_item(item: dict[str, Any]) -> ReviewData:
    """A tri_angle review item. Raises ReviewParseError when it lacks an id or a readable date."""
    return review_data(
        item.get("id"),
        item.get("text"),
        item.get("createdAt"),
        item.get("rating"),
        item.get("language"),
        item.get("response"),
    )
