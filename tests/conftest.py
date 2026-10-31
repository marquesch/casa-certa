from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from casa_certa.db.models import Base, House
from casa_certa.db.repository import HouseRepository
from casa_certa.db.session import create_session_factory
from casa_certa.sources.airbnb_page import ListingFacts, parse_listing_html
from casa_certa.sources.review_sources import parse_review_item
from tests.fakes import load_html, load_json


@pytest.fixture
def matinhos_facts() -> ListingFacts:
    return parse_listing_html(load_html("room_46397403.html"))


@pytest.fixture
def itapoa_facts() -> ListingFacts:
    return parse_listing_html(load_html("room_52584351.html"))


@pytest.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine, factory = create_session_factory(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


@pytest.fixture
async def session(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with session_factory() as db_session:
        yield db_session


@pytest.fixture
async def stored_house(session: AsyncSession, matinhos_facts: ListingFacts) -> House:
    """A house with facts, four reviews and an extra note, already committed."""
    repo = HouseRepository(session)
    house = await repo.add("46397403", "Casa do Albatroz")
    await repo.save_facts(house, matinhos_facts)
    await repo.save_reviews(house, [parse_review_item(item) for item in load_json("apify_reviews_46397403.json")])
    house.extra = "O dono confirmou que a praia fica a 3 minutos a pé."
    await session.commit()
    return house
