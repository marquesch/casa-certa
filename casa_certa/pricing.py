"""Price table: total and per-person cost for each group size."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel

from casa_certa.config import TripConfig


@dataclass(frozen=True)
class SizeQuote:
    """What Airbnb quoted for one group size; no total means the house is not available for that size."""

    total_cents: int | None
    original_total_cents: int | None = None


class PriceRow(BaseModel):
    people: int
    total_cents: int | None  # None: no quote, or not available for this many people
    original_total_cents: int | None
    per_person_cents: int | None


def format_brl(cents: int) -> str:
    reais, centavos = divmod(cents, 100)
    return f"R$ {reais:,}".replace(",", ".") + f",{centavos:02d}"


def cents_to_reais(cents: int) -> float:
    """Exact integer cents to a number a spreadsheet can sort on."""
    return float(Decimal(cents) / 100)


def price_table(quotes: Mapping[int, SizeQuote], trip: TripConfig) -> list[PriceRow]:
    """One row per group size from its own quote. The per-person amount is rounded up so the group covers the total."""
    rows: list[PriceRow] = []
    for people in trip.group_sizes():
        quote = quotes.get(people, SizeQuote(None))
        total = quote.total_cents
        rows.append(
            PriceRow(
                people=people,
                total_cents=total,
                original_total_cents=quote.original_total_cents if total is not None else None,
                per_person_cents=-(-total // people) if total is not None else None,
            )
        )
    return rows
