"""One real search of one place. Skipped by default.

Run with: make test-live-search (spends about a cent: 3 results at roughly $0.004 each).
"""

from datetime import date

import pytest
from apify_client import ApifyClientAsync

from casa_certa.config import Settings
from casa_certa.sources.apify import ApifyActorRunner
from casa_certa.sources.search_sources import SearchQuery, TriAngleSearch

pytestmark = pytest.mark.live

QUERY = SearchQuery(
    location="Itapoá, SC",
    check_in=date(2027, 1, 10),
    check_out=date(2027, 1, 14),
    guests=6,
    infants=0,
    max_results=3,
)


async def test_tri_angle_searches_one_place() -> None:
    runner = ApifyActorRunner(ApifyClientAsync(Settings().apify_token.get_secret_value()))
    hits = await TriAngleSearch(runner).search(QUERY)

    assert 1 <= len(hits) <= QUERY.max_results
    for hit in hits:
        print(f"{hit.airbnb_id}  {hit.rating}  {hit.title}")
        assert hit.airbnb_id.isdigit()
        assert hit.location == QUERY.location
