"""One real run on one house. Skipped by default; costs a few cents.

Run with: uv run pytest -m live tests/test_live.py -s
"""

from pathlib import Path

import pytest
from apify_client import ApifyClientAsync

from casa_certa.ai.agents import HouseAnalyst, HouseNamer, make_deepseek_model
from casa_certa.config import Settings
from casa_certa.db.models import Base
from casa_certa.db.session import create_session_factory
from casa_certa.pipeline import Pipeline
from casa_certa.sources.airbnb_page import make_client
from casa_certa.sources.apify import ApifyActorRunner
from casa_certa.sources.price_sources import TriAnglePrice
from casa_certa.sources.review_sources import TriAngleReviews
from tests.fakes import make_trip

pytestmark = pytest.mark.live


async def test_real_pipeline_on_one_house(tmp_path: Path) -> None:
    settings = Settings()
    engine, session_factory = create_session_factory(f"sqlite+aiosqlite:///{tmp_path / 'live.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    api_key = settings.deepseek_api_key.get_secret_value()
    trip = make_trip(urls=["https://www.airbnb.com.br/rooms/52584351"], review_cap=5)

    runner = ApifyActorRunner(ApifyClientAsync(settings.apify_token.get_secret_value()))
    async with make_client() as http_client:
        pipeline = Pipeline(
            session_factory=session_factory,
            http_client=http_client,
            price_source=TriAnglePrice(runner),
            review_source=TriAngleReviews(runner),
            namer=HouseNamer(make_deepseek_model(api_key, settings.cheap_model)),
            analyst=HouseAnalyst(make_deepseek_model(api_key, settings.analysis_model)),
        )
        report = await pipeline.run(trip)
    await engine.dispose()

    assert report.failures == {}
    house = report.houses[0]
    print(house.name, house.quotes_by_guests(trip), house.analysis.summary if house.analysis else None)
    assert house.analysis is not None
    assert set(house.quotes_by_guests(trip)) == set(trip.group_sizes())
