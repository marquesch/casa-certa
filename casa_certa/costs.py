"""Cost tracking: what Apify and DeepSeek charge per house, and the progress line that reports it.

Apify cost arrives per actor run, which may cover several houses; LLM usage arrives per call. Both are attributed
through context variables, so concurrent tasks (`asyncio.gather`) never mix their houses.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Protocol

from pydantic_ai.usage import RunUsage

TOKENS_PER_MTOK = 1_000_000


class CostSink(Protocol):
    def record_apify(self, actor_id: str, usd: float) -> None: ...


class UsageSink(Protocol):
    def record_llm(self, model: str, input_tokens: int, output_tokens: int) -> None: ...


def report_usage(sink: UsageSink | None, model_name: str, usage: RunUsage) -> None:
    if sink is not None:
        sink.record_llm(model_name, usage.input_tokens, usage.output_tokens)


@dataclass
class HouseCost:
    apify_usd: float = 0.0
    llm_usd: float = 0.0
    estimated: bool = False  # the Apify figure is an estimate or a share of a run covering several houses

    @property
    def total_usd(self) -> float:
        return self.apify_usd + self.llm_usd


@dataclass
class _Batch:
    room_ids: list[str]
    estimate_usd: float
    run_costs: list[float] = field(default_factory=list)  # one per actor run; <= 0 means not billed/reported


class CostTracker:
    """Implements both sinks and keeps the per-house totals."""

    def __init__(self, input_usd_per_mtok: float, output_usd_per_mtok: float) -> None:
        self._input_price = input_usd_per_mtok
        self._output_price = output_usd_per_mtok
        self._costs: dict[str, HouseCost] = {}
        self._unassigned = HouseCost()  # usage recorded outside any house still counts in the total
        self._house: ContextVar[str | None] = ContextVar("cost_house", default=None)
        self._batch: ContextVar[_Batch | None] = ContextVar("cost_batch", default=None)

    @contextmanager
    def house(self, room_id: str) -> Iterator[None]:
        token = self._house.set(room_id)
        try:
            yield
        finally:
            self._house.reset(token)

    @contextmanager
    def batch(self, room_ids: list[str], estimate_usd: float) -> Iterator[None]:
        """One source call for these houses, possibly several actor runs (fallback sources); costs split evenly.

        Every run's cost is reported through `record_apify`; an amount <= 0 means that run's billed cost is unknown.
        Positive amounts are summed. With none, `estimate_usd` is used instead. Either way the share is marked
        estimated when it was split, estimated, or some run's cost was unknown.
        """
        if not room_ids:
            raise ValueError("A cost batch needs at least one room id")
        batch = _Batch(room_ids, estimate_usd)
        token = self._batch.set(batch)
        try:
            yield
        finally:
            self._batch.reset(token)
            self._settle(batch)

    def record_apify(self, actor_id: str, usd: float) -> None:
        batch = self._batch.get()
        if batch is not None:
            batch.run_costs.append(usd)

    def record_llm(self, model: str, input_tokens: int, output_tokens: int) -> None:
        usd = (input_tokens * self._input_price + output_tokens * self._output_price) / TOKENS_PER_MTOK
        label = self._house.get()
        target = self._unassigned if label is None else self._costs.setdefault(label, HouseCost())
        target.llm_usd += usd

    def _settle(self, batch: _Batch) -> None:
        reported = sum(usd for usd in batch.run_costs if usd > 0)
        unknown = not batch.run_costs or any(usd <= 0 for usd in batch.run_costs)
        total = reported if reported > 0 else batch.estimate_usd
        share = total / len(batch.room_ids)
        estimated = reported <= 0 or unknown or len(batch.room_ids) > 1
        for room_id in batch.room_ids:
            cost = self._costs.setdefault(room_id, HouseCost())
            cost.apify_usd += share
            cost.estimated = cost.estimated or estimated

    def cost_of(self, room_id: str) -> HouseCost:
        return self._costs.get(room_id, HouseCost())

    def total(self) -> HouseCost:
        everything = [*self._costs.values(), self._unassigned]
        return HouseCost(
            apify_usd=sum(c.apify_usd for c in everything),
            llm_usd=sum(c.llm_usd for c in everything),
            estimated=any(c.estimated for c in everything),
        )

    def line(self, room_id: str, name: str, index: int, total: int, status: str) -> str:
        """One progress line for the house `room_id`, printed under its display `name`.

        Example: `[12/80] Casa da Piscina: Apify ~$0.0105, DeepSeek ~$0.0009, run total $0.38 (...)`.

        `analysed` shows the costs only; any other status is printed before them, and `cached` costs nothing. The
        DeepSeek figure is always `~` (priced from configured per-token rates); Apify's only when estimated or split.
        """
        run = self.total()
        run_total = f"run total ${run.total_usd:.2f} (Apify ${run.apify_usd:.2f}, DeepSeek ${run.llm_usd:.2f})"
        prefix = f"[{index}/{total}] {name}:"
        if status == "cached":
            return f"{prefix} cached, $0, {run_total}"
        cost = self.cost_of(room_id)
        mark = "~" if cost.estimated else ""
        figures = f"Apify {mark}${cost.apify_usd:.4f}, DeepSeek ~${cost.llm_usd:.4f}"
        shown = figures if status == "analysed" else f"{status}, {figures}"
        return f"{prefix} {shown}, {run_total}"
