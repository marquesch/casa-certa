import pytest

from casa_certa.config import TripConfig
from casa_certa.sources.apify import ApifyError, PriceData, ReviewData
from casa_certa.sources.fallback import FallbackPriceSource, FallbackReviewSource
from casa_certa.sources.protocols import SourceBatch
from tests.fakes import make_trip


def price(total: int | None = 100) -> PriceData:
    return PriceData(total_cents=total)


def review(n: int = 1) -> ReviewData:
    return ReviewData.model_validate(
        {
            "airbnb_review_id": str(n),
            "rating": 5,
            "created_at": "2026-10-11T12:00:00",
            "language": "pt",
            "text": "ok",
            "host_response": None,
        }
    )


class FakePrices:
    def __init__(
        self,
        name: str,
        answers: dict[str, PriceData | ApifyError] | ApifyError,
        cost: float = 1.0,
        notes: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.notes = notes or {}
        self.answers = answers
        self.cost = cost
        self.asked: list[list[str]] = []

    async def fetch_prices(self, room_ids: list[str], trip: TripConfig, guests: int) -> SourceBatch[PriceData]:
        self.asked.append(list(room_ids))
        if isinstance(self.answers, ApifyError):
            raise self.answers
        return SourceBatch({r: self.answers[r] for r in room_ids if r in self.answers}, dict(self.notes))

    def estimate_usd(self, quotes: int) -> float:
        return quotes * self.cost


class FakeReviews:
    def __init__(self, name: str, answers: dict[str, list[ReviewData] | ApifyError] | ApifyError) -> None:
        self.name = name
        self.answers = answers
        self.asked: list[list[str]] = []

    async def fetch_reviews(
        self, room_ids: list[str], trip: TripConfig, *, expect_reviews: frozenset[str] = frozenset()
    ) -> SourceBatch[list[ReviewData]]:
        self.asked.append(list(room_ids))
        if isinstance(self.answers, ApifyError):
            raise self.answers
        return SourceBatch({r: self.answers[r] for r in room_ids if r in self.answers})

    def estimate_usd(self, houses: int, review_cap: int) -> float:
        return houses * review_cap * 0.01


async def test_the_first_source_answers_everything_and_the_second_is_never_called() -> None:
    first = FakePrices("a", {"1": price(), "2": price()})
    second = FakePrices("b", {})

    batch = await FallbackPriceSource([first, second]).fetch_prices(["1", "2"], make_trip(), 9)

    assert set(batch.results) == {"1", "2"}
    assert batch.notes == {}
    assert second.asked == []


async def test_a_first_source_that_fails_entirely_hands_everything_to_the_second() -> None:
    first = FakePrices("a", ApifyError("down"))
    second = FakePrices("b", {"1": price(5), "2": price(6)})

    batch = await FallbackPriceSource([first, second]).fetch_prices(["1", "2"], make_trip(), 9)

    assert [r.total_cents for r in batch.results.values() if isinstance(r, PriceData)] == [5, 6]
    assert batch.notes == {
        "1": "price from b because a failed: down",
        "2": "price from b because a failed: down",
    }


async def test_only_the_failed_rooms_go_to_the_next_source() -> None:
    first = FakePrices("a", {"1": price(), "2": ApifyError("no quote")})
    second = FakePrices("b", {"2": price(7)})

    batch = await FallbackPriceSource([first, second]).fetch_prices(["1", "2"], make_trip(), 9)

    assert second.asked == [["2"]]
    assert batch.notes == {"2": "price from b because a failed: no quote"}
    assert isinstance(batch.results["1"], PriceData) and isinstance(batch.results["2"], PriceData)


async def test_a_missing_result_counts_as_a_failure() -> None:
    first = FakePrices("a", {"1": price()})
    second = FakePrices("b", {"2": price()})

    batch = await FallbackPriceSource([first, second]).fetch_prices(["1", "2"], make_trip(), 9)

    assert second.asked == [["2"]]
    assert batch.notes == {"2": "price from b because a failed: no result"}


async def test_an_unavailable_price_is_an_answer_not_a_failure() -> None:
    first = FakePrices("a", {"1": price(None)})
    second = FakePrices("b", {"1": price()})

    batch = await FallbackPriceSource([first, second]).fetch_prices(["1"], make_trip(), 13)

    assert second.asked == []
    assert batch.results["1"] == price(None)


async def test_all_sources_failing_names_every_source() -> None:
    first = FakePrices("a", ApifyError("down"))
    second = FakePrices("b", {"1": ApifyError("bad")})

    batch = await FallbackPriceSource([first, second]).fetch_prices(["1"], make_trip(), 9)

    error = batch.results["1"]
    assert isinstance(error, ApifyError)
    assert str(error) == "All price sources failed: a: down; b: bad"
    assert batch.notes == {}


def test_name_and_estimate_come_from_the_chain() -> None:
    chain = FallbackPriceSource([FakePrices("a", {}, cost=2.0), FakePrices("b", {}, cost=9.0)])
    assert (chain.name, chain.estimate_usd(3)) == ("a+b", 6.0)
    reviews = FallbackReviewSource([FakeReviews("x", {}), FakeReviews("y", {})])
    assert (reviews.name, reviews.estimate_usd(2, 30)) == ("x+y", 0.6)


async def test_an_empty_list_for_a_house_with_reviews_falls_back() -> None:
    first = FakeReviews("a", {"1": []})
    second = FakeReviews("b", {"1": [review()]})

    batch = await FallbackReviewSource([first, second]).fetch_reviews(
        ["1"], make_trip(), expect_reviews=frozenset({"1"})
    )

    assert batch.results["1"] == [review()]
    assert batch.notes == {"1": "reviews from b because a failed: returned no reviews although the listing has some"}


async def test_an_empty_list_for_a_house_without_reviews_is_accepted() -> None:
    first = FakeReviews("a", {"1": []})
    second = FakeReviews("b", {"1": [review()]})

    batch = await FallbackReviewSource([first, second]).fetch_reviews(["1"], make_trip())

    assert batch.results["1"] == []
    assert second.asked == []


async def test_review_chain_partial_failure_and_exhaustion() -> None:
    first = FakeReviews("a", {"1": [review()], "2": ApifyError("boom")})
    second = FakeReviews("b", {"2": []})

    batch = await FallbackReviewSource([first, second]).fetch_reviews(
        ["1", "2"], make_trip(), expect_reviews=frozenset({"2"})
    )

    error = batch.results["2"]
    assert isinstance(error, ApifyError)
    assert "a: boom" in str(error) and "b: returned no reviews" in str(error)
    assert batch.results["1"] == [review()]
    assert second.asked == [["2"]]


async def test_duplicate_room_ids_are_asked_once_and_answered_once() -> None:
    first = FakePrices("a", {"1": price(), "2": ApifyError("x")})
    second = FakePrices("b", {"2": price(7)})

    batch = await FallbackPriceSource([first, second]).fetch_prices(["1", "2", "1", "2"], make_trip(), 9)

    assert (first.asked, second.asked) == ([["1", "2"]], [["2"]])
    assert list(batch.results) == ["1", "2"]


async def test_results_follow_the_input_order_even_when_the_fallback_answers_first_rooms() -> None:
    first = FakePrices("a", {"3": price(), "2": ApifyError("x"), "1": ApifyError("y")})
    second = FakePrices("b", {"2": price(), "1": price()})

    batch = await FallbackPriceSource([first, second]).fetch_prices(["1", "2", "3"], make_trip(), 9)

    assert list(batch.results) == ["1", "2", "3"]


async def test_results_for_rooms_nobody_asked_about_are_ignored() -> None:
    class Chatty(FakePrices):
        async def fetch_prices(self, room_ids: list[str], trip: TripConfig, guests: int) -> SourceBatch[PriceData]:
            return SourceBatch({"1": price(), "stranger": price()}, {"stranger": "hello"})

    batch = await FallbackPriceSource([Chatty("a", {})]).fetch_prices(["1"], make_trip(), 9)

    assert list(batch.results) == ["1"]
    assert batch.notes == {}


async def test_a_chain_of_three_where_the_second_also_fails_some_rooms() -> None:
    first = FakePrices("a", {"1": ApifyError("a1"), "2": ApifyError("a2"), "3": price()})
    second = FakePrices("b", {"1": price(), "2": ApifyError("b2")})
    third = FakePrices("c", {"2": price(9)})

    batch = await FallbackPriceSource([first, second, third]).fetch_prices(["1", "2", "3"], make_trip(), 9)

    assert (second.asked, third.asked) == ([["1", "2"]], [["2"]])
    assert batch.notes == {
        "1": "price from b because a failed: a1",
        "2": "price from c because a failed: a2",
    }


async def test_nested_chains_keep_the_inner_notes() -> None:
    inner = FallbackPriceSource([FakePrices("a", {"1": ApifyError("x")}), FakePrices("b", {"1": price()})])
    outer = FallbackPriceSource([inner, FakePrices("c", {"2": price()})])

    batch = await outer.fetch_prices(["1", "2"], make_trip(), 9)

    assert batch.notes["1"] == "price from b because a failed: x"
    assert batch.notes["2"].startswith("price from c because a+b failed:")


async def test_a_note_from_a_source_that_answered_is_kept_next_to_the_chain_note() -> None:
    first = FakePrices("a", {"1": ApifyError("x")})
    second = FakePrices("b", {"1": price()}, notes={"1": "inner note"})

    batch = await FallbackPriceSource([first, second]).fetch_prices(["1"], make_trip(), 9)

    assert batch.notes == {"1": "price from b because a failed: x; inner note"}


async def test_review_chains_keep_notes_too() -> None:
    class Noted(FakeReviews):
        async def fetch_reviews(
            self, room_ids: list[str], trip: TripConfig, *, expect_reviews: frozenset[str] = frozenset()
        ) -> SourceBatch[list[ReviewData]]:
            return SourceBatch({"1": [review()]}, {"1": "kept"})

    batch = await FallbackReviewSource([Noted("a", {})]).fetch_reviews(["1"], make_trip())

    assert batch.notes == {"1": "kept"}


def test_an_empty_chain_is_a_clear_error() -> None:
    with pytest.raises(ValueError, match="at least one"):
        FallbackPriceSource([])
    with pytest.raises(ValueError, match="at least one"):
        FallbackReviewSource([])
