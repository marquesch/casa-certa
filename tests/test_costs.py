import asyncio

import pytest

from casa_certa.costs import CostTracker


def make_tracker() -> CostTracker:
    return CostTracker(input_usd_per_mtok=0.28, output_usd_per_mtok=0.42)


def test_llm_usage_is_priced_per_million_tokens() -> None:
    tracker = make_tracker()

    with tracker.house("A"):
        tracker.record_llm("deepseek-chat", 1_000_000, 500_000)

    assert tracker.cost_of("A").llm_usd == pytest.approx(0.28 + 0.21)
    assert tracker.total().llm_usd == pytest.approx(0.49)


def test_batch_splits_reported_cost_evenly_and_marks_it_estimated() -> None:
    tracker = make_tracker()

    with tracker.batch(["1", "2"], estimate_usd=0.5):
        tracker.record_apify("a/b", 0.02)

    assert tracker.cost_of("1").apify_usd == pytest.approx(0.01)
    assert tracker.cost_of("2").apify_usd == pytest.approx(0.01)
    assert tracker.cost_of("1").estimated


def test_a_reported_cost_for_a_single_house_is_exact() -> None:
    tracker = make_tracker()

    with tracker.batch(["1"], estimate_usd=0.5):
        tracker.record_apify("a/b", 0.02)

    assert tracker.cost_of("1").apify_usd == pytest.approx(0.02)
    assert not tracker.cost_of("1").estimated


def test_batch_without_a_reported_cost_uses_the_estimate() -> None:
    tracker = make_tracker()

    with tracker.batch(["1"], estimate_usd=0.03):
        pass

    assert tracker.cost_of("1").apify_usd == pytest.approx(0.03)
    assert tracker.cost_of("1").estimated


async def test_concurrent_batches_do_not_mix() -> None:
    tracker = make_tracker()

    async def run(room_id: str, usd: float, pause: float) -> None:
        with tracker.batch([room_id], estimate_usd=9.0):
            await asyncio.sleep(pause)
            tracker.record_apify("a/b", usd)
            await asyncio.sleep(pause)

    await asyncio.gather(run("1", 0.01, 0.01), run("2", 0.05, 0.02))

    assert tracker.cost_of("1").apify_usd == pytest.approx(0.01)
    assert tracker.cost_of("2").apify_usd == pytest.approx(0.05)


async def test_concurrent_houses_do_not_mix_llm_usage() -> None:
    tracker = make_tracker()

    async def run(label: str, tokens: int) -> None:
        with tracker.house(label):
            await asyncio.sleep(0.01)
            tracker.record_llm("m", tokens, 0)

    await asyncio.gather(run("A", 1_000_000), run("B", 2_000_000))

    assert tracker.cost_of("A").llm_usd == pytest.approx(0.28)
    assert tracker.cost_of("B").llm_usd == pytest.approx(0.56)


def test_line_marks_estimates_with_a_tilde_and_shows_running_total() -> None:
    tracker = make_tracker()
    with tracker.batch(["1", "2"], estimate_usd=0.0):
        tracker.record_apify("a/b", 0.021)
    with tracker.house("1"):
        tracker.record_llm("m", 1_000_000, 0)

    line = tracker.line("1", "Casa da Piscina", 12, 80, "analysed")

    assert (
        line
        == "[12/80] Casa da Piscina: Apify ~$0.0105, DeepSeek ~$0.2800, run total $0.30 (Apify $0.02, DeepSeek $0.28)"
    )


def test_line_has_no_tilde_on_an_exact_apify_figure() -> None:
    tracker = make_tracker()
    with tracker.batch(["1"], estimate_usd=0.0):
        tracker.record_apify("a/b", 0.02)

    assert "Apify $0.0200" in tracker.line("1", "Casa", 1, 1, "analysed")


def test_cached_house_line_costs_zero() -> None:
    tracker = make_tracker()

    assert tracker.line("46397403", "Casa", 3, 10, "cached") == (
        "[3/10] Casa: cached, $0, run total $0.00 (Apify $0.00, DeepSeek $0.00)"
    )


def test_line_shows_both_figures_when_booked_by_room_id() -> None:
    tracker = make_tracker()
    with tracker.batch(["46397403"], estimate_usd=0.0):
        tracker.record_apify("a/b", 0.02)
    with tracker.house("46397403"):
        tracker.record_llm("m", 1_000_000, 0)

    line = tracker.line("46397403", "Casa da Piscina", 1, 2, "analysed")

    assert line.startswith("[1/2] Casa da Piscina: Apify $0.0200, DeepSeek ~$0.2800")


def test_a_batch_without_room_ids_is_rejected() -> None:
    with pytest.raises(ValueError), make_tracker().batch([], estimate_usd=0.1):
        pass


def test_several_reported_runs_in_one_batch_are_summed_and_split() -> None:
    tracker = make_tracker()

    with tracker.batch(["1", "2"], estimate_usd=9.0):
        tracker.record_apify("a/b", 0.02)
        tracker.record_apify("c/d", 0.04)

    assert tracker.cost_of("1").apify_usd == pytest.approx(0.03)
    assert tracker.cost_of("2").apify_usd == pytest.approx(0.03)


def test_one_reported_and_one_unreported_run_gives_the_reported_sum_marked_estimated() -> None:
    tracker = make_tracker()

    with tracker.batch(["1"], estimate_usd=9.0):
        tracker.record_apify("a/b", 0.0)
        tracker.record_apify("c/d", 0.04)

    assert tracker.cost_of("1").apify_usd == pytest.approx(0.04)
    assert tracker.cost_of("1").estimated


def test_only_unreported_runs_use_the_estimate() -> None:
    tracker = make_tracker()

    with tracker.batch(["1"], estimate_usd=0.03):
        tracker.record_apify("a/b", 0.0)

    assert tracker.cost_of("1").apify_usd == pytest.approx(0.03)
    assert tracker.cost_of("1").estimated
