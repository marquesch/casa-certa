from datetime import date

from casa_certa.config import TripConfig
from casa_certa.pricing import SizeQuote, format_brl, price_table

TRIP = TripConfig(urls=["x"], check_in=date(2026, 12, 5), check_out=date(2026, 12, 9), min_people=9, max_people=13)

# Real totals (cents) measured for 5-9 Dec 2026: Itapoá charges per extra guest, Albatroz is flat and full at 13.
ITAPOA = {
    9: SizeQuote(294700, 324100),
    10: SizeQuote(331900, 361300),
    11: SizeQuote(369100, 398500),
    12: SizeQuote(406300, 435700),
    13: SizeQuote(443500, 472900),
}
ALBATROZ = {**dict.fromkeys(range(9, 13), SizeQuote(367500)), 13: SizeQuote(None)}


def test_format_brl() -> None:
    assert format_brl(367500) == "R$ 3.675,00"
    assert format_brl(643169) == "R$ 6.431,69"
    assert format_brl(5) == "R$ 0,05"


def test_price_table_uses_the_real_total_of_each_group_size() -> None:
    rows = price_table(ITAPOA, TRIP)
    assert [r.people for r in rows] == [9, 10, 11, 12, 13]
    assert [r.total_cents for r in rows] == [294700, 331900, 369100, 406300, 443500]
    assert [r.original_total_cents for r in rows] == [324100, 361300, 398500, 435700, 472900]
    assert rows[0].per_person_cents == -(-294700 // 9) == 32745  # 32744.44 rounded up to the cent
    assert rows[4].per_person_cents == -(-443500 // 13) == 34116  # 34115.38 rounded up
    assert rows[4].per_person_cents > rows[0].per_person_cents  # per-guest fees: bigger groups pay more each


def test_price_table_flat_price_gets_cheaper_per_person_and_unavailable_size_is_none() -> None:
    rows = price_table(ALBATROZ, TRIP)
    assert [r.total_cents for r in rows] == [367500] * 4 + [None]
    assert rows[0].per_person_cents == 40834  # 367500 / 9 = 40833.33 -> rounded up
    assert rows[1].per_person_cents == 36750
    assert rows[4].per_person_cents is None
    assert rows[0].original_total_cents is None


def test_price_table_size_missing_from_the_quotes_is_none() -> None:
    rows = price_table({9: SizeQuote(100000)}, TRIP)
    assert [r.total_cents for r in rows] == [100000, None, None, None, None]
    assert [r.per_person_cents for r in rows][1:] == [None] * 4
