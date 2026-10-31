"""Test doubles and helpers shared across test modules."""

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from casa_certa.ai.schemas import EvidenceOut, HouseAnalysis, VerdictOut
from casa_certa.config import TripConfig
from casa_certa.enums import AspectStatus, EvidenceSource
from casa_certa.pricing import format_brl
from casa_certa.sources.airbnb_page import room_id_from_url
from casa_certa.sources.apify import ApifyError

FIXTURES = Path(__file__).parent / "fixtures"


def load_json(name: str) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads((FIXTURES / name).read_text(encoding="utf8"))
    return data


def load_html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf8")


def make_trip(**overrides: Any) -> TripConfig:
    values: dict[str, Any] = {
        "urls": ["https://www.airbnb.com.br/rooms/46397403", "https://www.airbnb.com.br/rooms/52584351"],
        "check_in": date(2026, 12, 5),
        "check_out": date(2026, 12, 9),
        "infants": 1,
        "aspects": [
            {"name": "Perto da praia", "kind": "must_have"},
            {"name": "Piscina", "kind": "nice_to_have"},
        ],
    }
    values.update(overrides)
    return TripConfig.model_validate(values)


def sample_analysis(aspect_names: list[str]) -> HouseAnalysis:
    return HouseAnalysis(
        aspects=[
            VerdictOut(
                aspect=name,
                status=AspectStatus.MET,
                reason="ok",
                evidence=[EvidenceOut(source=EvidenceSource.LISTING, quote="100 metros do mar")],
            )
            for name in aspect_names
        ],
        pros=["Piscina privativa"],
        cons=["Sem ar-condicionado"],
        summary="Boa casa para o grupo",
        confidence_note=None,
    )


# Real totals measured with the price actor for 5-9 Dec 2026, by paying guests: (total, original) in cents.
ITAPOA_TOTALS = {
    9: (294700, 324100),
    10: (331900, 361300),
    11: (369100, 398500),
    12: (406300, 435700),
    13: (443500, 472900),
}
ALBATROZ_TOTAL = 367500  # flat for 9 to 12 guests; unavailable at 13 (capacity 12)
ALBATROZ_CAPACITY = 12

Items = list[dict[str, Any]]


def priced_item(room_id: str, total_cents: int | None, original_cents: int | None = None) -> dict[str, Any]:
    """A tri_angle price item copied from the real fixture, with its price overridden (None: unavailable)."""
    item: dict[str, Any] = json.loads(json.dumps(load_json(f"apify_rooms_{room_id}.json")[0]))
    if total_cents is None:
        return {**item, "isAvailable": False, "price": None}
    item["price"]["price"] = format_brl(total_cents)
    item["price"]["originalPrice"] = format_brl(original_cents) if original_cents else None
    return item


def answer_prices(run_input: dict[str, Any]) -> Items:
    """The fake price actor: answers each requested house according to the `adults` in the run input."""
    guests = run_input["adults"] + run_input["children"]
    items: Items = []
    for entry in run_input["startUrls"]:
        room_id = room_id_from_url(entry["url"])
        if room_id == "52584351":
            total, original = ITAPOA_TOTALS[guests]
            items.append(priced_item(room_id, total, original))
        elif room_id == "46397403":
            items.append(priced_item(room_id, ALBATROZ_TOTAL if guests <= ALBATROZ_CAPACITY else None))
    return items


class FakeRunner:
    """An ActorRunner that returns canned dataset items per actor id (or computed from the input) and records calls."""

    def __init__(
        self, outputs: dict[str, Items | Callable[[dict[str, Any]], Items]], fail_once: set[str] | None = None
    ) -> None:
        self.outputs = outputs
        self.fail_once = set(fail_once or ())  # actor ids whose next call raises ApifyError, once
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.max_items_seen: list[int | None] = []
        self.max_charge_seen: list[float | None] = []

    async def run_actor(
        self,
        actor_id: str,
        run_input: dict[str, Any],
        max_items: int | None = None,
        max_total_charge_usd: float | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append((actor_id, run_input))
        self.max_items_seen.append(max_items)
        self.max_charge_seen.append(max_total_charge_usd)
        if actor_id in self.fail_once:
            self.fail_once.discard(actor_id)
            raise ApifyError(f"Actor {actor_id} failed")
        output = self.outputs.get(actor_id, [])
        return output(run_input) if callable(output) else output

    def calls_to(self, actor_id: str) -> list[dict[str, Any]]:
        return [run_input for called, run_input in self.calls if called == actor_id]

    def max_items_for(self, actor_id: str) -> list[int | None]:
        return [seen for (called, _), seen in zip(self.calls, self.max_items_seen, strict=True) if called == actor_id]

    def max_charge_for(self, actor_id: str) -> list[float | None]:
        return [seen for (called, _), seen in zip(self.calls, self.max_charge_seen, strict=True) if called == actor_id]
