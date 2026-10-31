"""Command line entry point: `casa-certa run trip.yaml`."""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from alembic.config import Config
from apify_client import ApifyClientAsync

from alembic import command
from casa_certa.ai.agents import HouseAnalyst, HouseNamer, make_deepseek_model
from casa_certa.config import Settings
from casa_certa.config_sources.base import ConfigError
from casa_certa.config_sources.yaml_source import YamlConfigSource
from casa_certa.db.session import create_session_factory
from casa_certa.export.xlsx import build_workbook
from casa_certa.logging_setup import configure_logging
from casa_certa.pipeline import Pipeline, RunPlan
from casa_certa.sources.airbnb_page import make_client
from casa_certa.sources.apify import ApifyActorRunner
from casa_certa.sources.registry import build_price_source, build_review_source

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="casa-certa", description="Compare Airbnb houses for a family trip.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Analyze the houses in a trip file and write a spreadsheet")
    run.add_argument("trip_file", type=Path)
    run.add_argument("--refresh", action="store_true", help="Re-fetch everything from Airbnb (costs money)")
    run.add_argument("--dry-run", action="store_true", help="Only print what would be fetched")
    run.add_argument("--yes", action="store_true", help="Do not ask for confirmation before spending")
    run.add_argument("--output", type=Path, default=None, help="Output .xlsx path")
    return parser


def format_plan(plan: RunPlan) -> str:
    lines = [f"Trip has {len(plan.room_ids)} houses."]
    if plan.is_empty:
        lines.append("Nothing to fetch: everything is already stored.")
        return "\n".join(lines)
    lines += [
        f"- listing pages to fetch (free): {len(plan.fetch_facts)}",
        f"- reviews to fetch: {len(plan.fetch_reviews)} houses, up to {plan.review_cap} each",
        f"- prices to fetch: {plan.price_quotes} quotes for {len(plan.fetch_prices)} houses",
        f"Estimated Apify cost: ~${plan.estimated_cost_usd:.3f} (more if fallback sources are needed)",
    ]
    return "\n".join(lines)


def run_migrations() -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["configure_logger"] = False  # keep the CLI output free of Alembic's INFO lines
    command.upgrade(config, "head")


async def _confirm() -> bool:
    answer = await asyncio.to_thread(input, "Continue? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    trip = await YamlConfigSource(args.trip_file).load()

    runner = ApifyActorRunner(ApifyClientAsync(settings.apify_token.get_secret_value()))
    price_source = build_price_source(settings.price_sources, runner)  # fails fast on a bad PRICE_SOURCES
    review_source = build_review_source(settings.review_sources, runner)
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
                namer=HouseNamer(make_deepseek_model(api_key, settings.cheap_model)),
                analyst=HouseAnalyst(make_deepseek_model(api_key, settings.analysis_model)),
                page_delay=(settings.page_delay_min, settings.page_delay_max),
            )
            plan = await pipeline.plan(trip, refresh=args.refresh)
            print(format_plan(plan))
            if args.dry_run:
                return 0
            if not plan.is_empty and not args.yes and not await _confirm():
                print("Cancelled.")
                return 1
            report = await pipeline.run(trip, refresh=args.refresh)
    finally:
        await engine.dispose()

    output = args.output or Path(f"casa_certa_{trip.check_in.isoformat()}.xlsx")
    build_workbook(report.houses, report.failures, trip).save(output)
    print(f"Wrote {output} with {len(report.houses)} houses.")
    for room_id, messages in report.warnings.items():
        for message in messages:
            print(f"warning [{room_id}]: {message}", file=sys.stderr)
    for room_id, message in report.failures.items():
        print(f"FAILED [{room_id}]: {message}", file=sys.stderr)
    return 1 if report.failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging()
    try:
        return asyncio.run(_run(args))
    except (ConfigError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
