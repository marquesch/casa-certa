"""Builds a price or review source from the names in the settings."""

from collections.abc import Callable, Mapping, Sequence

from casa_certa.sources.apify import ActorRunner
from casa_certa.sources.fallback import FallbackPriceSource, FallbackReviewSource
from casa_certa.sources.price_sources import ThelastscraperPrice, TriAnglePrice
from casa_certa.sources.protocols import PriceSource, ReviewSource
from casa_certa.sources.review_sources import KaixReviews, SteadyscrapeReviews, TriAngleReviews

PRICE_SOURCE_NAMES = ("thelastscraper", "tri_angle")
REVIEW_SOURCE_NAMES = ("kaix", "steadyscrape", "tri_angle")

_PRICE_FACTORIES: Mapping[str, Callable[[ActorRunner], PriceSource]] = {
    "thelastscraper": ThelastscraperPrice,
    "tri_angle": TriAnglePrice,
}
_REVIEW_FACTORIES: Mapping[str, Callable[[ActorRunner], ReviewSource]] = {
    "kaix": KaixReviews,
    "steadyscrape": SteadyscrapeReviews,
    "tri_angle": TriAngleReviews,
}


class SourceConfigError(ValueError):
    """The configured source names are empty or unknown."""


def _validate(kind: str, names: Sequence[str], valid: Sequence[str]) -> None:
    if not names:
        raise SourceConfigError(f"No {kind} source configured; choose from: {', '.join(valid)}")
    unknown = [name for name in names if name not in valid]
    if unknown:
        raise SourceConfigError(f"Unknown {kind} source {', '.join(unknown)}; choose from: {', '.join(valid)}")


def build_price_source(names: Sequence[str], runner: ActorRunner) -> PriceSource:
    _validate("price", names, PRICE_SOURCE_NAMES)
    sources = [_PRICE_FACTORIES[name](runner) for name in names]
    return sources[0] if len(sources) == 1 else FallbackPriceSource(sources)


def build_review_source(names: Sequence[str], runner: ActorRunner) -> ReviewSource:
    _validate("review", names, REVIEW_SOURCE_NAMES)
    sources = [_REVIEW_FACTORIES[name](runner) for name in names]
    return sources[0] if len(sources) == 1 else FallbackReviewSource(sources)
