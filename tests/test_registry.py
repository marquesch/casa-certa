import pytest

from casa_certa.sources.fallback import FallbackPriceSource, FallbackReviewSource
from casa_certa.sources.price_sources import ThelastscraperPrice, TriAnglePrice
from casa_certa.sources.registry import (
    PRICE_SOURCE_NAMES,
    REVIEW_SOURCE_NAMES,
    SourceConfigError,
    build_price_source,
    build_review_source,
)
from casa_certa.sources.review_sources import KaixReviews, TriAngleReviews
from tests.fakes import FakeRunner


def test_a_single_name_returns_the_adapter_itself() -> None:
    runner = FakeRunner({})
    assert isinstance(build_price_source(["thelastscraper"], runner), ThelastscraperPrice)
    assert isinstance(build_price_source(["tri_angle"], runner), TriAnglePrice)
    assert isinstance(build_review_source(["kaix"], runner), KaixReviews)


def test_several_names_return_a_chain_in_order() -> None:
    runner = FakeRunner({})
    prices = build_price_source(["thelastscraper", "tri_angle"], runner)
    reviews = build_review_source(["kaix", "steadyscrape", "tri_angle"], runner)
    assert isinstance(prices, FallbackPriceSource) and prices.name == "thelastscraper+tri_angle"
    assert isinstance(reviews, FallbackReviewSource) and reviews.name == "kaix+steadyscrape+tri_angle"


def test_every_registered_name_builds() -> None:
    runner = FakeRunner({})
    assert [build_price_source([n], runner).name for n in PRICE_SOURCE_NAMES] == list(PRICE_SOURCE_NAMES)
    assert [build_review_source([n], runner).name for n in REVIEW_SOURCE_NAMES] == list(REVIEW_SOURCE_NAMES)
    assert isinstance(build_review_source(["tri_angle"], runner), TriAngleReviews)


def test_unknown_names_list_the_valid_ones() -> None:
    with pytest.raises(SourceConfigError, match=r"nope.*thelastscraper, tri_angle"):
        build_price_source(["thelastscraper", "nope"], FakeRunner({}))
    with pytest.raises(SourceConfigError, match="kaix, steadyscrape, tri_angle"):
        build_review_source(["thelastscraper"], FakeRunner({}))


def test_an_empty_list_is_an_error() -> None:
    with pytest.raises(SourceConfigError, match="No price source"):
        build_price_source([], FakeRunner({}))
    with pytest.raises(SourceConfigError, match="No review source"):
        build_review_source([], FakeRunner({}))


def test_search_source_builds_and_rejects_unknown_or_several_names() -> None:
    from casa_certa.sources.registry import SEARCH_SOURCE_NAMES, build_search_source

    runner = FakeRunner({})
    assert [build_search_source([n], runner).name for n in SEARCH_SOURCE_NAMES] == list(SEARCH_SOURCE_NAMES)
    with pytest.raises(SourceConfigError, match=r"nope.*tri_angle"):
        build_search_source(["nope"], runner)
    with pytest.raises(SourceConfigError, match="exactly one"):
        build_search_source(["tri_angle", "tri_angle"], runner)
