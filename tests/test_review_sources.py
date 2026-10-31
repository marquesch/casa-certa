import re
from typing import Any, cast

import pytest

from casa_certa.sources.apify import ApifyError, ReviewData, ReviewParseError
from casa_certa.sources.review_sources import (
    KaixReviews,
    ReviewActorSource,
    SteadyscrapeReviews,
    TriAngleReviews,
    parse_review_item,
    review_data,
)
from tests.fakes import FakeRunner, load_json, make_trip

ROOM = "46397403"


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def reviews_of(batch_result: list[ReviewData] | ApifyError) -> list[ReviewData]:
    assert isinstance(batch_result, list)
    return batch_result


# ------------------------------------------------------------------ shared parsing


def test_review_data_converts_offset_to_naive_utc_and_keeps_naive() -> None:
    aware = review_data(1, "ok", "2026-10-11T14:13:09+02:00", 5, "pt", None)
    naive = review_data(1, "ok", "2026-10-11T14:13:09", 5, "pt", None)
    assert aware.created_at.isoformat() == "2026-10-11T12:13:09"
    assert naive.created_at.isoformat() == "2026-10-11T14:13:09"


def test_review_data_strips_html_from_text_and_response() -> None:
    review = review_data(1, "a<br/>b", "2026-10-11T14:13:09", None, None, "Thanks<br/>see you")
    assert "<br" not in review.text
    assert review.host_response is not None and "<br" not in review.host_response


@pytest.mark.parametrize(
    "broken",
    [
        {"createdAt": "2026-10-11T14:13:09", "text": "no id"},
        {"id": 1, "createdAt": "not a date", "text": "bad date"},
        {"id": 1, "text": "no date"},
        {"id": 1, "createdAt": 12345, "text": "wrong type"},
    ],
)
def test_parse_review_item_raises_for_malformed_items(broken: dict[str, Any]) -> None:
    with pytest.raises(ReviewParseError):
        parse_review_item(broken)


def test_parse_review_item_reads_a_tri_angle_item() -> None:
    review = parse_review_item(load_json("apify_reviews_46397403.json")[0])
    assert (review.airbnb_review_id, review.rating, review.host_response) == ("1794188945820921552", 5, None)
    assert review.created_at.tzinfo is None


# ------------------------------------------------------------------ per adapter

ADAPTERS: list[tuple[type[ReviewActorSource], str]] = [
    (KaixReviews, "kaix_46397403.json"),
    (SteadyscrapeReviews, "steadyscrape_46397403.json"),
    (TriAngleReviews, "apify_reviews_46397403.json"),
]


@pytest.mark.parametrize(("adapter", "fixture"), ADAPTERS)
async def test_adapter_reads_its_real_fixture_and_gives_missing_houses_no_reviews(
    adapter: type[ReviewActorSource], fixture: str
) -> None:
    runner = FakeRunner({adapter.ACTOR: load_json(fixture)})

    batch = await adapter(runner).fetch_reviews([ROOM, "999"], make_trip(review_cap=30))

    reviews = reviews_of(batch.results[ROOM])
    assert len(reviews) == (4 if adapter is TriAngleReviews else 15)
    assert reviews_of(batch.results["999"]) == []
    assert batch.notes == {}


@pytest.mark.parametrize("adapter", [KaixReviews, SteadyscrapeReviews])
async def test_new_adapters_match_the_tri_angle_reviews(adapter: type[ReviewActorSource]) -> None:
    fixture = "kaix_46397403.json" if adapter is KaixReviews else "steadyscrape_46397403.json"
    baseline = await TriAngleReviews(
        FakeRunner({TriAngleReviews.ACTOR: load_json("apify_reviews_46397403.json")})
    ).fetch_reviews([ROOM], make_trip(review_cap=30))
    batch = await adapter(FakeRunner({adapter.ACTOR: load_json(fixture)})).fetch_reviews(
        [ROOM], make_trip(review_cap=30)
    )

    expected, got = reviews_of(baseline.results[ROOM]), reviews_of(batch.results[ROOM])
    for want, have in zip(expected, got[: len(expected)], strict=False):
        assert have.airbnb_review_id == want.airbnb_review_id
        assert have.rating == want.rating
        assert have.created_at == want.created_at
        assert squash(have.text) == squash(want.text)
    assert [r.airbnb_review_id for r in got[: len(expected)]][:4] == [r.airbnb_review_id for r in expected][:4]


@pytest.mark.parametrize(("adapter", "fixture"), ADAPTERS)
async def test_adapter_with_zero_cap_or_no_rooms_makes_no_call(adapter: type[ReviewActorSource], fixture: str) -> None:
    runner = FakeRunner({})
    zero = await adapter(runner).fetch_reviews(["1"], make_trip(review_cap=0))
    none = await adapter(runner).fetch_reviews([], make_trip())
    assert (zero.results, none.results) == ({"1": []}, {})
    assert runner.calls == []


@pytest.mark.parametrize(("adapter", "fixture"), ADAPTERS)
async def test_adapter_whole_run_failure_is_the_error_for_every_house(
    adapter: type[ReviewActorSource], fixture: str
) -> None:
    runner = FakeRunner({}, fail_once={adapter.ACTOR})
    batch = await adapter(runner).fetch_reviews(["1", "2"], make_trip())
    assert all(isinstance(result, ApifyError) for result in batch.results.values())
    assert set(batch.results) == {"1", "2"}


@pytest.mark.parametrize(("adapter", "fixture"), ADAPTERS)
async def test_adapter_skips_a_bad_review_and_keeps_the_rest(adapter: type[ReviewActorSource], fixture: str) -> None:
    items = load_json(fixture)
    if adapter is KaixReviews:
        items[0]["reviews"][0]["createdAt"] = "garbage"
        total = len(items[0]["reviews"])
    else:
        items[0]["createdAt"] = "garbage"
        total = len(items)

    batch = await adapter(FakeRunner({adapter.ACTOR: items})).fetch_reviews([ROOM], make_trip(review_cap=30))

    assert len(reviews_of(batch.results[ROOM])) == total - 1


async def test_kaix_input_ceiling_and_host_response() -> None:
    items = load_json("kaix_46397403.json")
    items[0]["reviews"][0]["response"] = "Thanks<br/>see you"
    runner = FakeRunner({KaixReviews.ACTOR: items})

    batch = await KaixReviews(runner).fetch_reviews([ROOM, "52584351"], make_trip(review_cap=30))

    assert runner.calls_to(KaixReviews.ACTOR) == [
        {
            "listings": ["https://www.airbnb.com.br/rooms/46397403", "https://www.airbnb.com.br/rooms/52584351"],
            "includeReviews": True,
            "maxReviewsPerListing": 30,
            "reviewsSortBy": "MOST_RECENT",
        }
    ]
    assert runner.max_items_for(KaixReviews.ACTOR) == [2]
    assert runner.max_charge_for(KaixReviews.ACTOR) == [pytest.approx(3 * KaixReviews(runner).estimate_usd(2, 30))]
    first = reviews_of(batch.results[ROOM])[0]
    assert first.host_response is not None and "see you" in first.host_response
    assert reviews_of(batch.results[ROOM])[1].host_response is None


async def test_steadyscrape_input_and_ceiling() -> None:
    runner = FakeRunner({SteadyscrapeReviews.ACTOR: load_json("steadyscrape_46397403.json")})

    await SteadyscrapeReviews(runner).fetch_reviews([ROOM], make_trip(review_cap=20))

    assert runner.calls_to(SteadyscrapeReviews.ACTOR) == [
        {
            "listings": ["https://www.airbnb.com.br/rooms/46397403"],
            "maxReviewsPerListing": 20,
            "sortBy": "MOST_RECENT",
        }
    ]
    assert runner.max_items_for(SteadyscrapeReviews.ACTOR) == [20]
    assert runner.max_charge_for(SteadyscrapeReviews.ACTOR) == [pytest.approx(3 * 20 * 0.0005)]


async def test_tri_angle_input_and_ceiling() -> None:
    runner = FakeRunner({TriAngleReviews.ACTOR: load_json("apify_reviews_46397403.json")})

    await TriAngleReviews(runner).fetch_reviews([ROOM, "999"], make_trip(review_cap=30))

    run_input = runner.calls_to(TriAngleReviews.ACTOR)[0]
    assert run_input["maxReviewsPerListing"] == 30
    assert run_input["sortBy"] == "most-recent"
    assert [u["url"] for u in run_input["startUrls"]] == [f"https://www.airbnb.com.br/rooms/{r}" for r in (ROOM, "999")]
    assert runner.max_items_for(TriAngleReviews.ACTOR) == [60]
    assert runner.max_charge_for(TriAngleReviews.ACTOR) == [pytest.approx(3 * (0.0001 + 2 * 30 * 0.005))]


def test_estimates() -> None:
    runner = FakeRunner({})
    assert KaixReviews(runner).estimate_usd(1, 30) == pytest.approx(0.00005 + 0.0001 + 30 * 0.00002)
    assert SteadyscrapeReviews(runner).estimate_usd(1, 30) == pytest.approx(0.015)
    assert TriAngleReviews(runner).estimate_usd(1, 30) == pytest.approx(0.1501)


@pytest.mark.parametrize("reviews", ["oops", 5, {"a": 1}, None])
async def test_kaix_non_list_reviews_count_as_none(reviews: Any) -> None:
    items = load_json("kaix_46397403.json")
    items[0]["reviews"] = reviews
    batch = await KaixReviews(FakeRunner({KaixReviews.ACTOR: items})).fetch_reviews([ROOM], make_trip(review_cap=30))
    assert batch.results[ROOM] == []


async def test_kaix_skips_non_dict_reviews_and_items() -> None:
    items: list[Any] = ["junk", *load_json("kaix_46397403.json")]
    total = len(items[1]["reviews"])
    items[1]["reviews"] = ["junk", 7, None, *items[1]["reviews"]]
    batch = await KaixReviews(FakeRunner({KaixReviews.ACTOR: items})).fetch_reviews([ROOM], make_trip(review_cap=30))
    assert len(reviews_of(batch.results[ROOM])) == total


@pytest.mark.parametrize(("adapter", "fixture"), ADAPTERS)
async def test_adapter_survives_wrong_field_types(adapter: type[ReviewActorSource], fixture: str) -> None:
    items = load_json(fixture)
    target = items[0]["reviews"][0] if adapter is KaixReviews else items[0]
    target.update({"text": 5, "rating": "five", "language": 3, "response": ["x"], "hostResponse": 4})
    batch = await adapter(FakeRunner({adapter.ACTOR: [cast(Any, "junk"), *items]})).fetch_reviews(
        [ROOM], make_trip(review_cap=30)
    )
    assert isinstance(batch.results[ROOM], list)


async def test_review_ceilings_have_no_float_noise() -> None:
    kaix = FakeRunner({})
    await KaixReviews(kaix).fetch_reviews([ROOM], make_trip(review_cap=30))
    assert kaix.max_charge_for(KaixReviews.ACTOR) == [0.00225]
    steady = FakeRunner({})
    await SteadyscrapeReviews(steady).fetch_reviews([ROOM], make_trip(review_cap=30))
    assert steady.max_charge_for(SteadyscrapeReviews.ACTOR) == [0.045]
    tri = FakeRunner({})
    await TriAngleReviews(tri).fetch_reviews([ROOM], make_trip(review_cap=30))
    assert tri.max_charge_for(TriAngleReviews.ACTOR) == [0.4503]
