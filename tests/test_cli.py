from pathlib import Path

import pytest

from casa_certa.cli import build_parser, format_plan, main
from casa_certa.pipeline import RunPlan


def test_parser_reads_run_options() -> None:
    args = build_parser().parse_args(["run", "trip.yaml", "--refresh", "--dry-run", "--yes", "--output", "out.xlsx"])
    assert args.trip_file == Path("trip.yaml")
    assert (args.refresh, args.dry_run, args.yes) == (True, True, True)
    assert args.output == Path("out.xlsx")


def test_parser_defaults_are_safe() -> None:
    args = build_parser().parse_args(["run", "trip.yaml"])
    assert (args.refresh, args.dry_run, args.yes) == (False, False, False)


def test_format_plan_shows_counts_and_cost() -> None:
    plan = RunPlan(
        room_ids=["1", "2", "3"],
        fetch_facts=["3"],
        fetch_reviews=["3"],
        fetch_prices={"2": [9, 10], "3": [9, 10, 11]},
        review_cap=30,
        estimated_cost_usd=0.0215,
    )
    text = format_plan(plan)
    assert "3 houses" in text
    assert "listing pages to fetch (free): 1" in text
    assert "reviews to fetch: 1 houses, up to 30 each" in text
    assert "prices to fetch: 5 quotes for 2 houses" in text
    assert "Estimated Apify cost: ~$0.021 (more if fallback sources are needed)" in text


def test_format_plan_when_everything_is_cached() -> None:
    plan = RunPlan(
        room_ids=["1"], fetch_facts=[], fetch_reviews=[], fetch_prices={}, review_cap=30, estimated_cost_usd=0.0
    )
    assert "Nothing to fetch" in format_plan(plan)


def test_an_unknown_source_name_prints_an_error_and_exits_with_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trip_file = tmp_path / "trip.yaml"
    trip_file.write_text(
        "urls: [https://www.airbnb.com.br/rooms/1]\ncheck_in: 2026-12-05\ncheck_out: 2026-12-09\n", encoding="utf8"
    )
    monkeypatch.chdir(tmp_path)  # so that no real .env is read
    monkeypatch.setenv("APIFY_TOKEN", "a")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "b")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("PRICE_SOURCES", "bogus")
    monkeypatch.setattr("casa_certa.cli.run_migrations", lambda: None)

    assert main(["run", str(trip_file), "--dry-run"]) == 2

    assert "error: Unknown price source bogus" in capsys.readouterr().err
