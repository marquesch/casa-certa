import sqlite3
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from alembic import command
from casa_certa.db.models import Base

ROOT = Path(__file__).resolve().parent.parent


def alembic_config() -> Config:
    """Like the CLI, keep Alembic from reconfiguring logging: it would disable the loggers later tests capture."""
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["configure_logger"] = False
    return config


def test_migrations_create_every_orm_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "migrated.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database}")

    command.upgrade(alembic_config(), "head")

    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert set(Base.metadata.tables) <= tables


def test_migrated_schema_matches_the_orm_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "migrated.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    command.upgrade(alembic_config(), "head")

    engine = create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    engine.dispose()
    assert differences == []


def test_upgrade_keeps_an_old_quote_with_guests_as_adults_plus_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "old.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    config = alembic_config()
    command.upgrade(config, "b6b419520522")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO house (id, airbnb_id, name, extra, created_at, updated_at)"
            " VALUES (1, '46397403', 'Casa', '', '2026-10-01', '2026-10-01')"
        )
        connection.execute(
            "INSERT INTO price_quote (house_id, check_in, check_out, adults, children, infants, nights,"
            " total_cents, fetched_at) VALUES (1, '2026-12-05', '2026-12-09', 10, 1, 1, 4, 367500, '2026-10-01')"
        )

    command.upgrade(config, "head")

    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT guests, infants, total_cents, source FROM price_quote").fetchone()
    assert row == (11, 1, 367500, None)

    command.downgrade(config, "b6b419520522")

    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT adults, children, total_cents FROM price_quote").fetchone()
    assert row == (11, 0, 367500)


def test_beach_distance_columns_are_added_and_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "old.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    config = alembic_config()
    command.upgrade(config, "da6f61dd4ee4")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO house (id, airbnb_id, name, extra, created_at, updated_at)"
            " VALUES (1, '1', 'Casa', '', '2026-10-01', '2026-10-01')"
        )
        connection.execute(
            "INSERT INTO analysis (house_id, input_hash, summary, model, created_at)"
            " VALUES (1, 'h', 'ok', 'm', '2026-10-01')"
        )

    command.upgrade(config, "head")

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT summary, beach_distance_m, beach_distance_source, beach_distance_quote FROM analysis"
        ).fetchone()
    assert row == ("ok", None, None, None)

    command.downgrade(config, "da6f61dd4ee4")

    with sqlite3.connect(database) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(analysis)")]
    assert "beach_distance_m" not in columns


def test_search_and_screen_tables_are_added_and_old_tables_survive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "old.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    config = alembic_config()
    command.upgrade(config, "07e94de0c249")

    command.upgrade(config, "head")

    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"search_run", "search_hit", "screen_result", "screen_verdict", "house", "analysis"} <= tables

    command.downgrade(config, "07e94de0c249")

    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "search_run" not in tables
    assert "house" in tables
