"""Command line entry point: `casa-certa run|site|publish|passphrase`."""

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from apify_client import ApifyClientAsync
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alembic import command
from casa_certa.ai.agents import HouseAnalyst, HouseNamer, make_deepseek_model
from casa_certa.ai.screener import HouseScreener
from casa_certa.config import Settings, SiteSettings, TripConfig
from casa_certa.config_sources.base import ConfigError
from casa_certa.config_sources.yaml_source import YamlConfigSource
from casa_certa.costs import CostTracker, HouseCost
from casa_certa.db.models import ScreenResult
from casa_certa.db.repository import HouseRepository, SearchInfo, SearchRepository
from casa_certa.db.session import create_session_factory
from casa_certa.enums import ScreenStatus
from casa_certa.export.xlsx import build_workbook
from casa_certa.logging_setup import configure_logging
from casa_certa.pipeline import Pipeline, RunPlan, RunReport, rooms_from
from casa_certa.search import SearchOutcome, SearchService
from casa_certa.sources.airbnb_page import make_client
from casa_certa.sources.apify import ApifyActorRunner
from casa_certa.sources.registry import build_price_source, build_review_source, build_search_source
from casa_certa.sources.search_sources import SearchHit
from casa_certa.web.crypto import check_passphrase
from casa_certa.web.model import build_site
from casa_certa.web.passphrase import generate_passphrase
from casa_certa.web.publish import PublishError, origin_url, pages_url, publish, redact_url
from casa_certa.web.site import write_site

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="casa-certa", description="Compare Airbnb houses for a family trip.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Search and/or analyze the houses of a trip file and write a spreadsheet")
    run.add_argument("trip_file", type=Path)
    run.add_argument("--search", action="store_true", help="Search the places of the trip and store the houses found")
    run.add_argument("--analyse", action="store_true", help="Analyse the urls plus the houses of the last search")
    run.add_argument("--refresh", action="store_true", help="Re-fetch everything from Airbnb (costs money)")
    run.add_argument("--dry-run", action="store_true", help="Only print what would be searched and fetched")
    run.add_argument("--yes", action="store_true", help="Answer yes to every question, the cost confirmation included")
    run.add_argument("--output", type=Path, default=None, help="Output .xlsx path")
    site = commands.add_parser("site", help="Build the encrypted static site from the stored data (no network)")
    site.add_argument("trip_file", type=Path)
    site.add_argument("--output-dir", type=Path, default=Path("site"), help="Where to write the site")
    publish_command = commands.add_parser("publish", help="Publish the built site to the gh-pages branch of origin")
    publish_command.add_argument("--dir", type=Path, default=Path("site"), dest="site_dir", help="The built site")
    publish_command.add_argument("--yes", action="store_true", help="Do not ask for confirmation")
    commands.add_parser("passphrase", help="Print a new random passphrase for the site")
    return parser


def format_plan(plan: RunPlan) -> str:
    lines = [f"Trip has {len(plan.room_ids)} houses."]
    if plan.skipped_screened:
        lines.append(f"- skipped, screened out by an earlier run: {len(plan.skipped_screened)}")
    if plan.is_empty:
        lines.append("Nothing to fetch: everything is already stored.")
        return "\n".join(lines)
    lines += [
        f"- listing pages to fetch (free): {len(plan.fetch_facts)}",
        f"- reviews to fetch: {len(plan.fetch_reviews)} houses, up to {plan.review_cap} each",
        f"- prices to fetch: {plan.price_quotes} quotes for {len(plan.fetch_prices)} houses",
    ]
    if plan.screen_rooms:
        lines.append(f"- searched houses to screen against the must-haves (cheap LLM): {len(plan.screen_rooms)}")
    cost = f"Estimated Apify cost: ~${plan.estimated_cost_usd:.3f} (more if fallback sources are needed)"
    if plan.screen_rooms:
        cost += "; it assumes every searched house passes the screen"
    lines.append(cost)
    return "\n".join(lines)


def format_hits(hits: list[SearchHit], stored: set[str]) -> str:
    """The houses a search found, one per line: id, link, title, place, and `stored` when already a house."""
    if not hits:
        return "The search found no houses."
    id_width = max(len(hit.airbnb_id) for hit in hits)
    url_width = max(len(hit.url) for hit in hits)
    lines = [f"Found {len(hits)} houses:"]
    for hit in hits:
        mark = "  stored" if hit.airbnb_id in stored else ""
        title = (hit.title or "").strip() or "-"
        lines.append(f"  {hit.airbnb_id:<{id_width}}  {hit.url:<{url_width}}  {title} ({hit.location}){mark}")
    return "\n".join(lines)


@dataclass(frozen=True)
class RunSummary:
    """The counts printed at the end of a run.

    `found` is None when no search ran. `analysed` is None when no analysis ran; otherwise it counts the houses in the
    spreadsheet, the ones stored by earlier runs included."""

    cost: HouseCost
    found: int | None = None
    already_stored: int = 0
    screened_out: int = 0
    analysed: int | None = None
    failed: int = 0


def format_summary(summary: RunSummary) -> str:
    parts: list[str] = []
    if summary.found is not None:
        parts.append(f"{summary.found} found ({summary.already_stored} already stored)")
    if summary.analysed is not None:
        parts += [f"{summary.screened_out} screened out", f"{summary.analysed} in the spreadsheet"]
    parts.append(f"{summary.failed} failed")
    cost = summary.cost
    mark = "~" if cost.estimated else ""
    total = f"${cost.total_usd:.2f} (Apify {mark}${cost.apify_usd:.2f}, DeepSeek ~${cost.llm_usd:.2f})"
    return f"Summary: {', '.join(parts)}. Total cost {total}"


def run_migrations() -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["configure_logger"] = False  # keep the CLI output free of Alembic's INFO lines
    command.upgrade(config, "head")


Ask = Callable[[str], Awaitable[bool]]
SEARCH_QUESTION = "Search?"
ANALYSE_QUESTION = "Analyse everything found?"
CONTINUE_QUESTION = "Continue?"


async def ask_yes_no(question: str) -> bool:
    """Ask on stdin; end of input or an empty answer is a no."""
    try:
        answer = await asyncio.to_thread(input, f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


async def _confirm() -> bool:
    return await ask_yes_no(CONTINUE_QUESTION)


@dataclass(frozen=True)
class RunMode:
    """What a `run` does. `analyse_after_search` None means: ask once the search (if any) is done."""

    search: bool
    analyse_after_search: bool | None
    assume_yes: bool


async def resolve_mode(args: argparse.Namespace, trip: TripConfig, ask: Ask) -> RunMode:
    """Turn the flags into a mode, asking whether to search only when no flag says what to do."""
    if args.search and trip.search is None:
        raise ConfigError("--search needs a search: block in the trip file")
    if args.search and args.analyse:
        return RunMode(search=True, analyse_after_search=True, assume_yes=True)
    if args.search:
        return RunMode(search=True, analyse_after_search=False, assume_yes=args.yes)
    if args.analyse or trip.search is None:  # without a search block `run` works as it always did
        return RunMode(search=False, analyse_after_search=True, assume_yes=args.yes)
    if args.dry_run or args.yes:  # a dry run spends nothing, so there is nothing to ask
        return RunMode(search=True, analyse_after_search=True, assume_yes=True)
    return RunMode(search=await ask(SEARCH_QUESTION), analyse_after_search=None, assume_yes=False)


@dataclass(frozen=True)
class RunServices:
    """Everything a `run` talks to, built by `_run` from the settings and by the tests from fakes."""

    pipeline: Pipeline
    searcher: SearchService
    session_factory: async_sessionmaker[AsyncSession]
    tracker: CostTracker


class RunCancelled(Exception):
    """The user declined the cost confirmation."""


@dataclass(frozen=True)
class Analysis:
    """The analyse stage's outcome; `screened_out` also holds the hits screened out by an earlier run."""

    report: RunReport
    screened_out: dict[str, str]


async def execute_run(args: argparse.Namespace, trip: TripConfig, services: RunServices, ask: Ask) -> int:
    """The `run` command once its services exist: resolve the mode, search, analyse, report."""
    if trip.search is not None and (args.search or not args.analyse):
        _print_lines(services.searcher.plan_lines(trip))  # shown before the search question
    mode = await resolve_mode(args, trip, ask)
    outcome = await _search_stage(args, trip, services) if mode.search else None
    analysis: Analysis | None = None
    if mode.analyse_after_search is not False:
        try:
            analysis = await _analyse_stage(args, trip, services, mode, outcome, ask)
        except RunCancelled:
            print("Cancelled.")
            return 1
    if args.dry_run:
        return 0
    summary = _summary(services.tracker, outcome, analysis)
    print(format_summary(summary))
    return 1 if summary.failed else 0


async def _search_stage(args: argparse.Namespace, trip: TripConfig, services: RunServices) -> SearchOutcome | None:
    """Run the searches and print what they found; a dry run stops at the plan lines already printed."""
    if args.dry_run:
        return None
    outcome = await services.searcher.run(trip)
    new_ids = {hit.airbnb_id for hit in outcome.new_hits}
    print(format_hits(outcome.hits, {hit.airbnb_id for hit in outcome.hits} - new_ids))
    for location, message in outcome.failures.items():
        print(f'FAILED search "{location}": {message}', file=sys.stderr)
    return outcome


async def _analyse_stage(
    args: argparse.Namespace,
    trip: TripConfig,
    services: RunServices,
    mode: RunMode,
    outcome: SearchOutcome | None,
    ask: Ask,
) -> Analysis | None:
    """Plan, confirm and run the pipeline on the yaml urls plus the hits, then write the spreadsheet.

    None when nothing was analysed."""
    rooms = rooms_from(trip, await _hit_ids(services, trip, mode, outcome))
    if args.dry_run and mode.search:
        print("Not counted below: the houses the search finds, which are screened before any paid step.")
    if not rooms:
        if not mode.search:
            raise ConfigError(
                "Nothing to analyse: the trip has no urls and there is no stored search; run --search first"
            )
        print("Nothing to analyse.")
        return None
    plan = await services.pipeline.plan(trip, rooms, refresh=args.refresh)
    print(format_plan(plan))
    if args.dry_run or not await _confirm_analysis(mode, plan, ask):
        return None
    report = await services.pipeline.run(trip, rooms, refresh=args.refresh)
    earlier = await _stored_screen_reasons(services.session_factory, plan.skipped_screened)
    analysis = Analysis(report, {**report.screened_out, **earlier})
    _write_outputs(args, trip, analysis)
    return analysis


async def _hit_ids(services: RunServices, trip: TripConfig, mode: RunMode, outcome: SearchOutcome | None) -> list[str]:
    """This run's hits; the stored last search when no search was asked for; none for a dry run's search."""
    if outcome is not None:
        return [hit.airbnb_id for hit in outcome.hits]
    if mode.search:  # a dry run: the search that would run replaces the stored one
        return []
    async with services.session_factory() as session:
        searches = SearchRepository(session)
        warning = stale_search_warning(await searches.last_search_info(), trip)
        if warning:
            print(warning, file=sys.stderr)
        return await searches.last_search_ids()


def stale_search_warning(info: SearchInfo | None, trip: TripConfig) -> str | None:
    """A warning when the stored search was run for different dates or group size than the trip has now."""
    if info is None or (info.check_in, info.check_out, info.guests) == (trip.check_in, trip.check_out, trip.max_people):
        return None
    return (
        f"warning: the stored search was run for {info.check_in} to {info.check_out}, {info.guests} guests, but the "
        f"trip is {trip.check_in} to {trip.check_out}, {trip.max_people} guests; run --search again"
    )


async def _confirm_analysis(mode: RunMode, plan: RunPlan, ask: Ask) -> bool:
    """Whether to go on and spend. Raises RunCancelled when the plain cost confirmation is declined."""
    if plan.is_empty or mode.assume_yes:
        return True
    if mode.analyse_after_search is None:
        if await ask(ANALYSE_QUESTION):
            return True
        print("Not analysing.")
        return False
    if not await ask(CONTINUE_QUESTION):
        raise RunCancelled
    return True


async def _stored_screen_reasons(
    session_factory: async_sessionmaker[AsyncSession], room_ids: list[str]
) -> dict[str, str]:
    """The stored reason of each house screened out by an earlier run: its first failed must-have."""
    reasons: dict[str, str] = {}
    async with session_factory() as session:
        searches = SearchRepository(session)
        for room_id in room_ids:
            result = await searches.get_screen(room_id)
            if result is not None:
                reasons[room_id] = stored_screen_reason(result)
    return reasons


def stored_screen_reason(result: ScreenResult) -> str:
    """Same wording as the pipeline's: the reason of the first failed must-have, then its quote."""
    failed = next((v for v in result.verdicts if v.status is ScreenStatus.FAILS), None)
    if failed is None:
        return "failed the must-haves"
    return f'{failed.reason} ("{failed.quote}")' if failed.quote else failed.reason


def _write_outputs(args: argparse.Namespace, trip: TripConfig, analysis: Analysis) -> None:
    report = analysis.report
    output = args.output or Path(f"casa_certa_{trip.check_in.isoformat()}.xlsx")
    build_workbook(report.houses, report.failures, trip, screened_out=analysis.screened_out).save(output)
    print(f"Wrote {output} with {len(report.houses)} houses.")
    for room_id, reason in analysis.screened_out.items():
        print(f"screened out: {room_id} {reason}")
    for room_id, messages in report.warnings.items():
        for message in messages:
            print(f"warning [{room_id}]: {message}", file=sys.stderr)
    for room_id, message in report.failures.items():
        print(f"FAILED [{room_id}]: {message}", file=sys.stderr)


def _summary(tracker: CostTracker, outcome: SearchOutcome | None, analysis: Analysis | None) -> RunSummary:
    return RunSummary(
        cost=tracker.total(),
        found=len(outcome.hits) if outcome else None,
        already_stored=len(outcome.hits) - len(outcome.new_hits) if outcome else 0,
        screened_out=len(analysis.screened_out) if analysis else 0,
        analysed=len(analysis.report.houses) if analysis else None,
        failed=(len(outcome.failures) if outcome else 0) + (len(analysis.report.failures) if analysis else 0),
    )


def _print_lines(lines: list[str]) -> None:
    for line in lines:
        print(line)


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    trip = await YamlConfigSource(args.trip_file).load()

    tracker = CostTracker(settings.deepseek_input_usd_per_mtok, settings.deepseek_output_usd_per_mtok)
    runner = ApifyActorRunner(ApifyClientAsync(settings.apify_token.get_secret_value()), cost_sink=tracker)
    price_source = build_price_source(settings.price_sources, runner)  # fails fast on a bad *_SOURCES setting
    review_source = build_review_source(settings.review_sources, runner)
    search_source = build_search_source(settings.search_sources, runner)
    await asyncio.to_thread(run_migrations)  # only after the configuration is known to be valid
    engine, session_factory = create_session_factory(settings.database_url)
    api_key = settings.deepseek_api_key.get_secret_value()
    try:
        async with make_client() as http_client:
            pipeline = Pipeline(
                session_factory=session_factory,
                http_client=http_client,
                price_source=price_source,
                review_source=review_source,
                namer=HouseNamer(make_deepseek_model(api_key, settings.cheap_model), usage_sink=tracker),
                analyst=HouseAnalyst(make_deepseek_model(api_key, settings.analysis_model), usage_sink=tracker),
                page_delay=(settings.page_delay_min, settings.page_delay_max),
                screener=HouseScreener(make_deepseek_model(api_key, settings.screen_model), usage_sink=tracker),
                tracker=tracker,
            )
            searcher = SearchService(search_source, session_factory, tracker)
            services = RunServices(pipeline, searcher, session_factory, tracker)
            return await execute_run(args, trip, services, ask_yes_no)
    finally:
        await engine.dispose()


async def _site(args: argparse.Namespace) -> int:
    try:
        settings = SiteSettings()
    except ValidationError as error:
        if any("site_passphrase" in item["loc"] for item in error.errors()):
            print("error: set SITE_PASSPHRASE in .env (run `make passphrase` to generate one)", file=sys.stderr)
        else:
            print(f"error: invalid settings: {error}", file=sys.stderr)  # pydantic does not echo secret values
        return 2
    trip = await YamlConfigSource(args.trip_file).load()
    passphrase = settings.site_passphrase.get_secret_value()
    await asyncio.to_thread(check_passphrase, passphrase)  # refuse a weak one before touching anything
    await asyncio.to_thread(run_migrations)
    engine, session_factory = create_session_factory(settings.database_url)
    try:
        async with session_factory() as session:
            houses = await HouseRepository(session).all()
            hit_ids = await SearchRepository(session).last_search_ids()
    finally:
        await engine.dispose()
    index = await asyncio.to_thread(write_site, houses, trip, passphrase, args.output_dir, hit_ids)
    site = build_site(houses, trip, hit_ids)
    shown = len(site.complete) + len(site.incomplete)
    print(f"Wrote {index} ({shown} listings). Run `make publish` to put it online.")
    return 0


async def _publish(args: argparse.Namespace) -> int:
    origin = await origin_url(PROJECT_ROOT)
    shown = redact_url(origin)
    target = pages_url(origin) or shown
    print(f"This replaces the gh-pages branch of {shown} with the contents of {args.site_dir} (served at {target}).")
    if not args.yes and not await _confirm():
        print("Cancelled.")
        return 1
    await publish(args.site_dir, origin)
    print(f"Published. It can take a minute to appear at {target}")
    return 0


async def _passphrase(args: argparse.Namespace) -> int:
    print(generate_passphrase())
    print("Put it in .env as SITE_PASSPHRASE=<the words above> and share it with the group.", file=sys.stderr)
    return 0


HANDLERS = {"run": _run, "site": _site, "publish": _publish, "passphrase": _passphrase}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    try:
        return asyncio.run(HANDLERS[args.command](args))
    except (ConfigError, ValueError) as error:  # WeakPassphraseError is a ValueError
        print(f"error: {error}", file=sys.stderr)
        return 2
    except PublishError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
