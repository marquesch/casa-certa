import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from openpyxl import load_workbook
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from casa_certa.ai.agents import HouseAnalyst, HouseNamer
from casa_certa.ai.schemas import ScreenOutput, ScreenVerdictOut
from casa_certa.ai.screener import HouseScreener
from casa_certa.cli import (
    ANALYSE_QUESTION,
    CONTINUE_QUESTION,
    SEARCH_QUESTION,
    RunMode,
    RunServices,
    RunSummary,
    ask_yes_no,
    build_parser,
    execute_run,
    format_hits,
    format_plan,
    format_summary,
    main,
    resolve_mode,
)
from casa_certa.config import TripConfig
from casa_certa.config_sources.base import ConfigError
from casa_certa.costs import CostTracker, HouseCost
from casa_certa.db.repository import HouseRepository, SearchRepository
from casa_certa.enums import ScreenStatus
from casa_certa.pipeline import Pipeline, RunPlan
from casa_certa.search import SearchService
from casa_certa.sources.airbnb_page import listing_url
from casa_certa.sources.price_sources import TriAnglePrice
from casa_certa.sources.review_sources import TriAngleReviews
from casa_certa.sources.search_sources import SearchHit, TriAngleSearch
from tests.fakes import FakeRunner, answer_prices, load_html, load_json, make_trip, sample_analysis


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


SITE_TRIP = "urls: [https://www.airbnb.com.br/rooms/1]\ncheck_in: 2026-12-05\ncheck_out: 2026-12-09\n"


def site_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    trip_file = tmp_path / "trip.yaml"
    trip_file.write_text(SITE_TRIP, encoding="utf8")
    monkeypatch.chdir(tmp_path)  # so that no real .env is read
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.delenv("SITE_PASSPHRASE", raising=False)
    return trip_file


def test_parser_reads_the_site_commands() -> None:
    parser = build_parser()
    site = parser.parse_args(["site", "trip.yaml", "--output-dir", "out"])
    assert (site.command, site.trip_file, site.output_dir) == ("site", Path("trip.yaml"), Path("out"))
    assert parser.parse_args(["site", "trip.yaml"]).output_dir == Path("site")
    publish = parser.parse_args(["publish", "--dir", "out", "--yes"])
    assert (publish.command, publish.site_dir, publish.yes) == ("publish", Path("out"), True)
    assert parser.parse_args(["publish"]).site_dir == Path("site")
    assert parser.parse_args(["passphrase"]).command == "passphrase"


def test_passphrase_prints_five_words(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["passphrase"]) == 0
    assert len(capsys.readouterr().out.split()[0].split("-")) == 5


def test_site_without_a_passphrase_explains_how_to_set_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trip_file = site_environment(tmp_path, monkeypatch)
    assert main(["site", str(trip_file)]) == 2
    assert "SITE_PASSPHRASE" in capsys.readouterr().err


def test_site_with_a_weak_passphrase_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trip_file = site_environment(tmp_path, monkeypatch)
    monkeypatch.setenv("SITE_PASSPHRASE", "casa2026")
    assert main(["site", str(trip_file)]) == 2
    assert "passphrase" in capsys.readouterr().err
    assert not (tmp_path / "site").exists()


def test_site_builds_from_an_empty_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trip_file = site_environment(tmp_path, monkeypatch)
    monkeypatch.setenv("SITE_PASSPHRASE", "cavalo-nuvem-tijolo-lampada-rio")

    assert main(["site", str(trip_file)]) == 0

    assert (tmp_path / "site" / "index.html").is_file()
    assert "site/index.html" in capsys.readouterr().out


# --- search and analyse modes ---------------------------------------------------------------------------------------

LISTED = "46397403"  # in the trip's urls: never screened
FOUND = "52584351"  # what the fake search finds
PAGES = {LISTED: "room_46397403.html", FOUND: "room_52584351.html"}
SEARCH_ACTOR = TriAngleSearch.ACTOR
PRICE_ACTOR = TriAnglePrice.ACTOR
REVIEWS_ACTOR = TriAngleReviews.ACTOR


def search_trip(**overrides: Any) -> TripConfig:
    values: dict[str, Any] = {"search": {"locations": ["Praia"], "max_results": 5}, "urls": [listing_url(LISTED)]}
    values.update(overrides)
    return make_trip(**values)


def run_args(tmp_path: Path, *flags: str) -> argparse.Namespace:
    return build_parser().parse_args(["run", "trip.yaml", *flags, "--output", str(tmp_path / "out.xlsx")])


class Asker:
    """A fake `ask` that answers from a script and records the questions."""

    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)
        self.questions: list[str] = []

    async def __call__(self, question: str) -> bool:
        self.questions.append(question)
        if not self.answers:
            raise AssertionError(f"unexpected question: {question}")
        return self.answers.pop(0)


@dataclass
class CliWorld:
    services: RunServices
    runner: FakeRunner
    screen_status: dict[str, ScreenStatus] = field(default_factory=dict)
    analysed: list[str] = field(default_factory=list)

    def actors_called(self) -> list[str]:
        return [actor for actor, _ in self.runner.calls]


async def no_sleep(seconds: float) -> None:
    return None


def make_cli_world(session_factory: async_sessionmaker[AsyncSession]) -> CliWorld:
    reviews = load_json("apify_reviews_46397403.json")
    other_reviews = [{**item, "id": f"other-{item['id']}", "startUrl": listing_url(FOUND)} for item in reviews]
    runner = FakeRunner(
        {
            SEARCH_ACTOR: [{"url": listing_url(FOUND), "title": "Casa perto do mar"}],
            PRICE_ACTOR: answer_prices,
            REVIEWS_ACTOR: reviews + other_reviews,
        }
    )
    tracker = CostTracker(0.30, 1.20)
    world = CliWorld(services=None, runner=runner)  # type: ignore[arg-type]

    def serve_page(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=load_html(PAGES[request.url.path.rsplit("/", 1)[-1]]))

    def name_house(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        name = "Casa do Albatroz" if "Albatroz" in str(messages) else "Casa do Campo"
        return ModelResponse(parts=[TextPart(f'{{"name": "{name}"}}')])

    def analyse_house(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        world.analysed.append(str(messages))
        return ModelResponse(parts=[TextPart(sample_analysis(["Perto da praia", "Piscina"]).model_dump_json())])

    def screen_house(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        room_id = LISTED if "Albatroz" in str(messages) else FOUND
        status = world.screen_status.get(room_id, ScreenStatus.MEETS)
        verdict = ScreenVerdictOut(aspect="Perto da praia", status=status, reason="Fica longe do mar", quote="3 km")
        return ModelResponse(parts=[TextPart(ScreenOutput(verdicts=[verdict]).model_dump_json())])

    pipeline = Pipeline(
        session_factory=session_factory,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(serve_page)),
        price_source=TriAnglePrice(runner),
        review_source=TriAngleReviews(runner),
        namer=HouseNamer(FunctionModel(name_house), usage_sink=tracker),
        analyst=HouseAnalyst(FunctionModel(analyse_house), usage_sink=tracker),
        page_delay=(0, 0),
        sleep=no_sleep,
        screener=HouseScreener(FunctionModel(screen_house), usage_sink=tracker),
        tracker=tracker,
    )
    searcher = SearchService(TriAngleSearch(runner), session_factory, tracker)
    world.services = RunServices(pipeline, searcher, session_factory, tracker)
    return world


async def stored_ids(session_factory: async_sessionmaker[AsyncSession]) -> set[str]:
    async with session_factory() as session:
        return {house.airbnb_id for house in await HouseRepository(session).all()}


def test_parser_search_and_analyse_default_to_false() -> None:
    args = build_parser().parse_args(["run", "trip.yaml"])
    assert (args.search, args.analyse) == (False, False)
    both = build_parser().parse_args(["run", "trip.yaml", "--search", "--analyse"])
    assert (both.search, both.analyse) == (True, True)


async def test_both_flags_assume_yes_and_never_ask(tmp_path: Path, session_factory, capsys) -> None:
    args = run_args(tmp_path, "--search", "--analyse")
    assert await resolve_mode(args, search_trip(), Asker()) == RunMode(True, True, True)
    world = make_cli_world(session_factory)

    assert await execute_run(args, search_trip(), world.services, Asker()) == 0  # Asker() fails on any question

    assert SEARCH_ACTOR in world.actors_called() and PRICE_ACTOR in world.actors_called()
    assert await stored_ids(session_factory) == {LISTED, FOUND}
    out = capsys.readouterr().out
    assert "Estimated Apify cost" in out  # the plan is still printed
    assert (tmp_path / "out.xlsx").is_file()


async def test_search_only_does_not_analyse(tmp_path: Path, session_factory, capsys) -> None:
    args = run_args(tmp_path, "--search")
    assert await resolve_mode(args, search_trip(), Asker()) == RunMode(True, False, False)
    world = make_cli_world(session_factory)

    assert await execute_run(args, search_trip(), world.services, Asker()) == 0

    assert world.actors_called() == [SEARCH_ACTOR]
    assert await stored_ids(session_factory) == set()
    async with session_factory() as session:
        assert await SearchRepository(session).last_search_ids() == [FOUND]
    out = capsys.readouterr().out
    assert f"{FOUND}  {listing_url(FOUND)}  Casa perto do mar (Praia)" in out
    assert "Summary: 1 found (0 already stored), 0 failed." in out
    assert not (tmp_path / "out.xlsx").exists()


async def test_analyse_only_does_not_search(tmp_path: Path, session_factory) -> None:
    trip = search_trip()
    async with session_factory() as session:
        await SearchRepository(session).replace_last_search(
            trip, [SearchHit(airbnb_id=FOUND, url=listing_url(FOUND), location="Praia")]
        )
        await session.commit()
    args = run_args(tmp_path, "--analyse", "--yes")
    assert await resolve_mode(args, trip, Asker()) == RunMode(False, True, True)
    world = make_cli_world(session_factory)

    assert await execute_run(args, trip, world.services, Asker()) == 0

    assert SEARCH_ACTOR not in world.actors_called()
    assert await stored_ids(session_factory) == {LISTED, FOUND}  # the yaml url and the stored hit


@pytest.mark.parametrize("search", [True, False])
@pytest.mark.parametrize("analyse", [True, False])
async def test_no_flags_with_search_block_asks_search_then_analyse(
    tmp_path: Path, session_factory, search: bool, analyse: bool
) -> None:
    world = make_cli_world(session_factory)
    asker = Asker(search, analyse)

    assert await execute_run(run_args(tmp_path), search_trip(), world.services, asker) == 0

    assert asker.questions == [SEARCH_QUESTION, ANALYSE_QUESTION]  # "no" to the search still offers the analysis
    assert (SEARCH_ACTOR in world.actors_called()) == search
    expected = ({LISTED, FOUND} if search else {LISTED}) if analyse else set()
    assert await stored_ids(session_factory) == expected
    assert (tmp_path / "out.xlsx").exists() == analyse


async def test_no_flags_without_search_block_behaves_as_before(tmp_path: Path, session_factory, capsys) -> None:
    trip = make_trip(urls=[listing_url(LISTED)])
    args = run_args(tmp_path)
    assert await resolve_mode(args, trip, Asker()) == RunMode(False, True, False)
    world = make_cli_world(session_factory)
    asker = Asker(False)

    assert await execute_run(args, trip, world.services, asker) == 1

    assert asker.questions == [CONTINUE_QUESTION]  # only the cost confirmation, as before
    assert "Cancelled." in capsys.readouterr().out
    assert world.runner.calls == []


async def test_search_without_a_search_block_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="--search needs a search: block"):
        await resolve_mode(run_args(tmp_path, "--search"), make_trip(), Asker())


def test_search_without_a_search_block_exits_with_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trip_file = tmp_path / "trip.yaml"
    trip_file.write_text(SITE_TRIP, encoding="utf8")
    monkeypatch.chdir(tmp_path)  # so that no real .env is read
    monkeypatch.setenv("APIFY_TOKEN", "a")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "b")
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr("casa_certa.cli.run_migrations", lambda: None)
    assert main(["run", str(trip_file), "--search"]) == 2
    assert "--search needs a search: block" in capsys.readouterr().err


async def test_analyse_with_nothing_to_analyse_says_to_search_first(tmp_path: Path, session_factory) -> None:
    world = make_cli_world(session_factory)
    trip = search_trip(urls=[])

    with pytest.raises(ConfigError, match="run --search first"):
        await execute_run(run_args(tmp_path, "--analyse"), trip, world.services, Asker())

    assert world.runner.calls == []


@pytest.mark.parametrize(("typed", "expected"), [(EOFError(), False), ("", False), ("  ", False), ("y", True)])
async def test_eof_and_empty_answers_are_no(monkeypatch: pytest.MonkeyPatch, typed: Any, expected: bool) -> None:
    def fake_input(prompt: str) -> str:
        if isinstance(typed, BaseException):
            raise typed
        return str(typed)

    monkeypatch.setattr("builtins.input", fake_input)
    assert await ask_yes_no("Search?") is expected


@pytest.mark.parametrize("flags", [["--dry-run"], ["--search", "--dry-run"], ["--search", "--analyse", "--dry-run"]])
async def test_dry_run_with_search_calls_no_actor(tmp_path: Path, session_factory, capsys, flags: list[str]) -> None:
    world = make_cli_world(session_factory)

    assert await execute_run(run_args(tmp_path, *flags), search_trip(), world.services, Asker()) == 0

    assert world.runner.calls == []
    assert world.analysed == []
    assert world.services.tracker.total().total_usd == 0
    assert 'search "Praia": up to 5 results, ~$0.02' in capsys.readouterr().out
    async with session_factory() as session:
        assert await SearchRepository(session).last_search_ids() == []


def test_format_plan_mentions_screening_and_all_pass_assumption() -> None:
    plan = RunPlan(
        room_ids=["1", "2"],
        fetch_facts=["1", "2"],
        fetch_reviews=["1", "2"],
        fetch_prices={"1": [9], "2": [9]},
        review_cap=30,
        estimated_cost_usd=0.01,
        screen_rooms=["2"],
        skipped_screened=["3"],
    )
    text = format_plan(plan)
    assert "searched houses to screen against the must-haves (cheap LLM): 1" in text
    assert "assumes every searched house passes the screen" in text
    assert "skipped, screened out by an earlier run: 1" in text


def test_summary_lists_counts_and_total_cost() -> None:
    cost = HouseCost(apify_usd=0.35, llm_usd=0.03, estimated=True)
    full = RunSummary(cost=cost, found=12, already_stored=3, screened_out=4, analysed=8, failed=1)
    assert format_summary(full) == (
        "Summary: 12 found (3 already stored), 4 screened out, 8 analysed, 1 failed. "
        "Total cost $0.38 (Apify ~$0.35, DeepSeek ~$0.03)"
    )
    analyse_only = RunSummary(cost=HouseCost(), analysed=2)
    assert format_summary(analyse_only) == (
        "Summary: 0 screened out, 2 analysed, 0 failed. Total cost $0.00 (Apify $0.00, DeepSeek ~$0.00)"
    )


def test_hits_table_marks_the_stored_houses() -> None:
    hits = [
        SearchHit(airbnb_id="1", url=listing_url("1"), title="Casa A", location="Praia"),
        SearchHit(airbnb_id="22", url=listing_url("22"), title=None, location="Centro"),
    ]
    lines = format_hits(hits, {"22"}).splitlines()
    assert lines[0] == "Found 2 houses:"
    assert lines[1].split() == ["1", listing_url("1"), "Casa", "A", "(Praia)"]
    assert lines[2].split() == ["22", listing_url("22"), "-", "(Centro)", "stored"]


async def test_houses_screened_out_now_and_earlier_are_listed_and_in_the_spreadsheet(
    tmp_path: Path, session_factory, capsys
) -> None:
    world = make_cli_world(session_factory)
    world.screen_status[FOUND] = ScreenStatus.FAILS
    args = run_args(tmp_path, "--search", "--analyse")

    assert await execute_run(args, search_trip(), world.services, Asker()) == 0
    first = capsys.readouterr().out
    assert await execute_run(args, search_trip(), world.services, Asker()) == 0  # skipped: screened out before
    second = capsys.readouterr().out

    for out in (first, second):
        assert f'screened out: {FOUND} Fica longe do mar ("3 km")' in out
        assert "1 screened out, 1 analysed, 0 failed" in out
    assert "skipped, screened out by an earlier run: 1" in second
    sheet = load_workbook(tmp_path / "out.xlsx")["Descartadas"]
    assert FOUND in [str(cell.value) for cell in sheet["A"]]


async def test_a_failed_search_exits_with_1(tmp_path: Path, session_factory, capsys) -> None:
    world = make_cli_world(session_factory)
    world.runner.fail_once.add(SEARCH_ACTOR)

    assert await execute_run(run_args(tmp_path, "--search"), search_trip(), world.services, Asker()) == 1

    captured = capsys.readouterr()
    assert 'FAILED search "Praia"' in captured.err
    assert "1 failed" in captured.out


async def test_yes_and_dry_run_answer_the_search_questions_without_asking(tmp_path: Path) -> None:
    for flag in ("--yes", "--dry-run"):
        assert await resolve_mode(run_args(tmp_path, flag), search_trip(), Asker()) == RunMode(True, True, True)
