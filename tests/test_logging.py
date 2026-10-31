import logging
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from casa_certa.logging_setup import NOISY_LOGGERS, quiet_noisy_loggers
from casa_certa.sources.apify import ApifyError
from tests.fakes import make_trip
from tests.test_apify import FakeClient, make_runner
from tests.test_pipeline import make_world


async def test_a_run_logs_page_fetches_apify_calls_and_one_line_per_house(
    session_factory: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
) -> None:
    world = make_world(session_factory)

    with caplog.at_level(logging.INFO):
        await world.pipeline.run(make_trip())

    lines = [record.getMessage() for record in caplog.records]
    assert any(line.startswith("fetching 2 listing pages") for line in lines)
    assert any(line.startswith("page 1/2 46397403: ok in ") for line in lines)
    assert any(line.startswith("page 2/2 52584351: ok in ") for line in lines)
    assert any(line.startswith("reviews: tri_angle for 2 houses, up to 30 each") for line in lines)
    assert any(line.startswith("prices: tri_angle for ") and " guests" in line for line in lines)
    assert any(line.startswith("[1/2] Casa do Albatroz (46397403): listing saved") for line in lines)
    assert any("Casa do Albatroz: analysing with" in line for line in lines)
    assert any(line.startswith("[1/2] Casa do Albatroz (46397403): done - ") for line in lines)
    assert any(line.startswith("[2/2] Casa do Campo (52584351): done - ") for line in lines)


async def test_a_second_run_reports_the_analysis_as_cached(
    session_factory: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
) -> None:
    world = make_world(session_factory)
    await world.pipeline.run(make_trip())
    caplog.clear()

    with caplog.at_level(logging.INFO):
        await world.pipeline.run(make_trip())

    lines = [record.getMessage() for record in caplog.records]
    assert any("analysis cached" in line for line in lines)
    assert not any("analysing with" in line for line in lines)
    assert not any(line.startswith(("page ", "prices:", "reviews:")) for line in lines)


async def test_a_blocked_page_is_logged_as_a_warning_with_the_house(
    session_factory: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
) -> None:
    world = make_world(session_factory, blocked=frozenset({"46397403"}))

    with caplog.at_level(logging.INFO):
        await world.pipeline.run(make_trip())

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(line.startswith("page 1/2 46397403: failed (HTTP 403") for line in warnings)
    assert any(line.startswith("[1/2] 46397403: failed (") for line in warnings)


async def test_the_apify_runner_logs_start_and_finish(caplog: pytest.LogCaptureFixture) -> None:
    client = FakeClient(SimpleNamespace(status="SUCCEEDED", default_dataset_id="ds"), items=[{"id": 1}, {"id": 2}])

    with caplog.at_level(logging.INFO):
        await make_runner(client).run_actor("a/b", {})

    lines = [record.getMessage() for record in caplog.records]
    assert lines[0] == "apify a/b: run started"
    assert lines[1].startswith("apify a/b: done in ") and lines[1].endswith("2 items")


async def test_the_apify_runner_logs_failures(caplog: pytest.LogCaptureFixture) -> None:
    client = FakeClient(SimpleNamespace(status="FAILED", default_dataset_id="ds"))

    with caplog.at_level(logging.INFO), pytest.raises(ApifyError):
        await make_runner(client).run_actor("a/b", {})

    assert any(r.levelno == logging.WARNING and "apify a/b: failed after" in r.getMessage() for r in caplog.records)


def test_chatty_libraries_are_silenced() -> None:
    quiet_noisy_loggers()
    assert all(logging.getLogger(name).level == logging.WARNING for name in NOISY_LOGGERS)
