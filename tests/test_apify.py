from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from apify_client import ApifyClientAsync
from apify_client.errors import ApifyClientError

from casa_certa.sources.apify import ACTOR_RUN_TIMEOUT, ApifyActorRunner, ApifyError


class FakeActorCall:
    def __init__(self, run: Any, error: Exception | None = None) -> None:
        self.run = run
        self.error = error
        self.kwargs: dict[str, Any] = {}

    async def call(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.run


class FakeClient:
    def __init__(
        self,
        run: Any,
        items: list[dict[str, Any]] | None = None,
        call_error: Exception | None = None,
        list_error: Exception | None = None,
    ) -> None:
        self.actor_call = FakeActorCall(run, call_error)
        self.items = items or []
        self.list_error = list_error

    def actor(self, actor_id: str) -> FakeActorCall:
        return self.actor_call

    def dataset(self, dataset_id: str) -> Any:
        async def list_items() -> Any:
            if self.list_error is not None:
                raise self.list_error
            return SimpleNamespace(items=self.items)

        return SimpleNamespace(list_items=list_items)


def make_runner(client: FakeClient) -> ApifyActorRunner:
    return ApifyActorRunner(cast(ApifyClientAsync, client))


async def test_actor_runner_returns_items_and_bounds_the_run() -> None:
    client = FakeClient(SimpleNamespace(status="SUCCEEDED", default_dataset_id="ds"), items=[{"id": 1}])

    items = await make_runner(client).run_actor("a/b", {"x": 1}, max_items=7)

    assert items == [{"id": 1}]
    assert client.actor_call.kwargs == {
        "run_input": {"x": 1},
        "max_items": 7,
        "max_total_charge_usd": None,
        "run_timeout": ACTOR_RUN_TIMEOUT,
        "logger": None,
    }
    assert timedelta(minutes=10) == ACTOR_RUN_TIMEOUT


async def test_actor_runner_passes_the_charge_ceiling_as_a_decimal() -> None:
    client = FakeClient(SimpleNamespace(status="SUCCEEDED", default_dataset_id="ds"))

    await make_runner(client).run_actor("a/b", {}, max_total_charge_usd=0.045)

    assert client.actor_call.kwargs["max_total_charge_usd"] == Decimal("0.045")


@pytest.mark.parametrize("status", ["FAILED", "TIMED-OUT"])
async def test_actor_runner_raises_on_unsuccessful_status(status: str) -> None:
    client = FakeClient(SimpleNamespace(status=status, default_dataset_id="ds"))
    with pytest.raises(ApifyError, match=status):
        await make_runner(client).run_actor("a/b", {})


async def test_actor_runner_raises_when_run_did_not_start() -> None:
    with pytest.raises(ApifyError):
        await make_runner(FakeClient(None)).run_actor("a/b", {})


@pytest.mark.parametrize("error", [ApifyClientError("boom"), httpx.ConnectError("no network")])
async def test_actor_runner_turns_call_errors_into_apify_errors(error: Exception) -> None:
    client = FakeClient(None, call_error=error)
    with pytest.raises(ApifyError, match="a/b") as caught:
        await make_runner(client).run_actor("a/b", {})
    assert caught.value.__cause__ is error


@pytest.mark.parametrize("error", [ApifyClientError("boom"), httpx.ReadTimeout("slow")])
async def test_actor_runner_turns_dataset_errors_into_apify_errors(error: Exception) -> None:
    client = FakeClient(SimpleNamespace(status="SUCCEEDED", default_dataset_id="ds"), list_error=error)
    with pytest.raises(ApifyError, match="a/b") as caught:
        await make_runner(client).run_actor("a/b", {})
    assert caught.value.__cause__ is error


async def test_actor_runner_passes_no_limits_through_as_none() -> None:
    client = FakeClient(SimpleNamespace(status="SUCCEEDED", default_dataset_id="ds"))

    await make_runner(client).run_actor("a/b", {}, max_items=None, max_total_charge_usd=None)

    assert client.actor_call.kwargs["max_items"] is None
    assert client.actor_call.kwargs["max_total_charge_usd"] is None


class RecordingSink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []

    def record_apify(self, actor_id: str, usd: float) -> None:
        self.calls.append((actor_id, usd))


@pytest.mark.parametrize(("usage", "expected"), [(0.0123, [("a/b", 0.0123)]), (0.0, []), (None, [])])
async def test_actor_runner_reports_only_a_billed_cost_to_the_sink(usage, expected) -> None:
    run = SimpleNamespace(status="SUCCEEDED", default_dataset_id="ds", usage_total_usd=usage)
    sink = RecordingSink()
    runner = ApifyActorRunner(cast(ApifyClientAsync, FakeClient(run)), cost_sink=sink)

    await runner.run_actor("a/b", {})

    assert sink.calls == expected
