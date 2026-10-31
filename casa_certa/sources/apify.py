"""Price and review text from Apify actors.

The actual actor execution is hidden behind `ActorRunner`, so tests can inject a fake and the code
stays free of network access.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

import httpx
from apify_client import ApifyClientAsync
from apify_client.errors import ApifyClientError
from pydantic import BaseModel

# The platform aborts a run that takes longer than this, which surfaces as a non-SUCCEEDED status.
ACTOR_RUN_TIMEOUT = timedelta(minutes=10)


class ApifyError(Exception):
    """An Apify actor run failed or returned nothing usable."""


class PriceParseError(ApifyError):
    """The scraper item has no usable price although the listing is available."""


class ReviewParseError(ApifyError):
    """A review item is malformed (missing id, unreadable date...)."""


class PriceData(BaseModel):
    total_cents: int | None  # None: not available for those dates and guest count (a normal answer, not an error)
    original_total_cents: int | None = None
    source: str = ""


class ReviewData(BaseModel):
    airbnb_review_id: str
    rating: int | None
    created_at: datetime  # naive UTC
    language: str | None
    text: str
    host_response: str | None


class ActorRunner(Protocol):
    async def run_actor(
        self,
        actor_id: str,
        run_input: dict[str, Any],
        max_items: int | None = None,
        max_total_charge_usd: float | None = None,
    ) -> list[dict[str, Any]]: ...


class ApifyActorRunner:
    """Runs an actor on Apify and returns its dataset items."""

    def __init__(self, client: ApifyClientAsync) -> None:
        self._client = client

    async def run_actor(
        self,
        actor_id: str,
        run_input: dict[str, Any],
        max_items: int | None = None,
        max_total_charge_usd: float | None = None,
    ) -> list[dict[str, Any]]:
        ceiling = None if max_total_charge_usd is None else Decimal(str(max_total_charge_usd))
        try:
            run = await self._client.actor(actor_id).call(
                run_input=run_input, max_items=max_items, max_total_charge_usd=ceiling, run_timeout=ACTOR_RUN_TIMEOUT
            )
        except (ApifyClientError, httpx.HTTPError) as e:
            raise ApifyError(f"Actor {actor_id} could not be run: {e!r}") from e
        if run is None:
            raise ApifyError(f"Actor {actor_id} did not start")
        if run.status != "SUCCEEDED":
            raise ApifyError(f"Actor {actor_id} finished with status {run.status}")
        try:
            page = await self._client.dataset(run.default_dataset_id).list_items()
        except (ApifyClientError, httpx.HTTPError) as e:
            raise ApifyError(f"Could not read the results of actor {actor_id}: {e!r}") from e
        return list(page.items)
