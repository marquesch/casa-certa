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


def test_migrations_create_every_orm_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "migrated.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database}")

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert set(Base.metadata.tables) <= tables


def test_migrated_schema_matches_the_orm_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "migrated.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database}")
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

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
    config = Config(str(ROOT / "alembic.ini"))
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
