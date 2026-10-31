from typing import Any

import pytest

from casa_certa.sources.apify import ApifyError, PriceData, PriceParseError
from casa_certa.sources.price_sources import ThelastscraperPrice, TriAnglePrice, parse_brl_cents
from tests.fakes import FakeRunner, answer_prices, load_json, make_trip

THELAST = ThelastscraperPrice.ACTOR
TRI = TriAnglePrice.ACTOR


def thelast_items() -> list[dict[str, Any]]:
    return load_json("thelastscraper_price_11_guests.json")


def only(batch_result: PriceData | ApifyError) -> PriceData:
    assert isinstance(batch_result, PriceData)
    return batch_result


# ------------------------------------------------------------------ thelastscraper


def test_thelastscraper_parses_the_real_fixture() -> None:
    source = ThelastscraperPrice(FakeRunner({}))
    albatroz, itapoa = (source.parse_item(item) for item in thelast_items())
    assert (albatroz.total_cents, albatroz.original_total_cents, albatroz.source) == (367500, None, "thelastscraper")
    assert (itapoa.total_cents, itapoa.original_total_cents, itapoa.source) == (369100, 398500, "thelastscraper")


def test_thelastscraper_rounds_half_up_to_cents() -> None:
    item = thelast_items()[0]
    item["price"]["total"] = 1234.565
    assert ThelastscraperPrice(FakeRunner({})).parse_item(item).total_cents == 123457


def test_thelastscraper_unavailable_is_a_valid_answer() -> None:
    item = thelast_items()[0]
    item["price"]["is_available"] = False
    price = ThelastscraperPrice(FakeRunner({})).parse_item(item)
    assert (price.total_cents, price.source) == (None, "thelastscraper")


def test_thelastscraper_rejects_a_quote_for_other_dates() -> None:
    item = thelast_items()[0]
    item["price"]["dates_match_query"] = False
    with pytest.raises(PriceParseError, match="other dates"):
        ThelastscraperPrice(FakeRunner({})).parse_item(item)


def test_thelastscraper_rejects_a_missing_total() -> None:
    item = thelast_items()[0]
    item["price"]["total"] = None
    with pytest.raises(PriceParseError):
        ThelastscraperPrice(FakeRunner({})).parse_item(item)


async def test_thelastscraper_builds_the_input_and_bounds_the_run() -> None:
    runner = FakeRunner({THELAST: thelast_items()})

    batch = await ThelastscraperPrice(runner).fetch_prices(["46397403", "52584351", "999"], make_trip(), 11)

    assert runner.calls_to(THELAST) == [
        {
            "where": [f"https://www.airbnb.com.br/rooms/{room}" for room in ("46397403", "52584351", "999")],
            "checkIn": "2026-12-05",
            "checkOut": "2026-12-09",
            "adults": 11,
            "children": 0,
            "infants": 1,
            "currency": "BRL",
            "language": "pt",
            "outputFormat": "full_json",
        }
    ]
    assert runner.max_items_for(THELAST) == [3]
    assert runner.max_charge_for(THELAST) == [pytest.approx(3 * 3 * 0.0015)]
    assert only(batch.results["46397403"]).total_cents == 367500
    assert only(batch.results["52584351"]).total_cents == 369100
    assert isinstance(batch.results["999"], ApifyError)
    assert batch.notes == {}


async def test_thelastscraper_whole_run_failure_is_the_error_for_every_house() -> None:
    runner = FakeRunner({}, fail_once={THELAST})
    batch = await ThelastscraperPrice(runner).fetch_prices(["1", "2"], make_trip(), 9)
    assert all(isinstance(result, ApifyError) for result in batch.results.values())
    assert set(batch.results) == {"1", "2"}


async def test_a_bad_item_is_the_error_of_its_house_only() -> None:
    items = thelast_items()
    items[0]["price"]["total"] = None
    batch = await ThelastscraperPrice(FakeRunner({THELAST: items})).fetch_prices(
        ["46397403", "52584351"], make_trip(), 11
    )
    assert isinstance(batch.results["46397403"], PriceParseError)
    assert only(batch.results["52584351"]).total_cents == 369100


BAD_TOTALS = ["abc", "3.675,00", True, float("nan"), float("inf"), float("-inf"), 0, -5, "", "0.001", [1]]


@pytest.mark.parametrize("bad", BAD_TOTALS)
def test_thelastscraper_garbage_totals_are_parse_errors(bad: Any) -> None:
    item = thelast_items()[0]
    item["price"]["total"] = bad
    with pytest.raises(PriceParseError):
        ThelastscraperPrice(FakeRunner({})).parse_item(item)


@pytest.mark.parametrize("bad", ["abc", "3.675,00", True, float("nan"), float("inf"), 0, -5])
def test_thelastscraper_garbage_originals_are_parse_errors(bad: Any) -> None:
    item = thelast_items()[1]
    item["price"]["total_display_original"] = bad
    with pytest.raises(PriceParseError):
        ThelastscraperPrice(FakeRunner({})).parse_item(item)


@pytest.mark.parametrize("good", ["3675", 3675.0, 3675, "3675.00"])
def test_thelastscraper_accepts_numeric_strings_and_floats(good: Any) -> None:
    item = thelast_items()[0]
    item["price"]["total"] = good
    assert ThelastscraperPrice(FakeRunner({})).parse_item(item).total_cents == 367500


def test_thelastscraper_keeps_an_original_that_is_not_above_the_total() -> None:
    item = thelast_items()[0]
    item["price"]["total_display_original"] = 3000
    assert ThelastscraperPrice(FakeRunner({})).parse_item(item).original_total_cents == 300000


def test_thelastscraper_unavailable_ignores_a_garbage_total() -> None:
    item = thelast_items()[0]
    item["price"].update({"is_available": False, "total": 0})
    assert ThelastscraperPrice(FakeRunner({})).parse_item(item).total_cents is None


@pytest.mark.parametrize("price", ["oops", 5, ["x"]])
def test_thelastscraper_non_dict_price_is_a_parse_error(price: Any) -> None:
    item = thelast_items()[0]
    item["price"] = price
    with pytest.raises(PriceParseError):
        ThelastscraperPrice(FakeRunner({})).parse_item(item)


@pytest.mark.parametrize("bad", BAD_TOTALS)
async def test_a_garbage_total_fails_its_house_and_spares_the_other(bad: Any) -> None:
    items = thelast_items()
    items[0]["price"]["total"] = bad
    batch = await ThelastscraperPrice(FakeRunner({THELAST: items})).fetch_prices(
        ["46397403", "52584351"], make_trip(), 11
    )
    assert isinstance(batch.results["46397403"], PriceParseError)
    assert only(batch.results["52584351"]).total_cents == 369100


async def test_a_garbage_original_fails_its_house_and_spares_the_other() -> None:
    items = thelast_items()
    items[1]["price"]["total_display_original"] = "abc"
    batch = await ThelastscraperPrice(FakeRunner({THELAST: items})).fetch_prices(
        ["46397403", "52584351"], make_trip(), 11
    )
    assert only(batch.results["46397403"]).total_cents == 367500
    assert isinstance(batch.results["52584351"], PriceParseError)


async def test_non_dict_items_are_ignored() -> None:
    items: list[Any] = ["junk", None, *thelast_items()]
    batch = await ThelastscraperPrice(FakeRunner({THELAST: items})).fetch_prices(["46397403"], make_trip(), 11)
    assert only(batch.results["46397403"]).total_cents == 367500


async def test_thelastscraper_ceiling_has_no_float_noise() -> None:
    runner = FakeRunner({THELAST: thelast_items()})
    await ThelastscraperPrice(runner).fetch_prices(["46397403"], make_trip(), 11)
    assert runner.max_charge_for(THELAST) == [0.0045]


def test_thelastscraper_estimate() -> None:
    assert ThelastscraperPrice(FakeRunner({})).estimate_usd(5) == pytest.approx(0.0075)


# ------------------------------------------------------------------ tri_angle


def test_parse_brl_cents_values() -> None:
    assert [parse_brl_cents(t) for t in ("R$ 3.675", "R$ 6.431,69", "R$ 918,75", "R$ 5")] == [
        367500,
        643169,
        91875,
        500,
    ]


def test_parse_brl_cents_rejects_garbage() -> None:
    with pytest.raises(PriceParseError):
        parse_brl_cents("free")


def test_tri_angle_parses_plain_and_discounted_prices() -> None:
    source = TriAnglePrice(FakeRunner({}))
    plain = source.parse_item(load_json("apify_rooms_46397403.json")[0])
    discounted = source.parse_item(load_json("apify_rooms_52584351.json")[0])
    assert (plain.total_cents, plain.original_total_cents, plain.source) == (367500, None, "tri_angle")
    assert (discounted.total_cents, discounted.original_total_cents) == (369100, 398500)


def test_tri_angle_without_price_raises_and_unavailable_is_valid() -> None:
    source = TriAnglePrice(FakeRunner({}))
    with pytest.raises(PriceParseError):
        source.parse_item({"id": "1", "price": None})
    item = {**load_json("apify_rooms_46397403.json")[0], "isAvailable": False}
    assert source.parse_item(item).total_cents is None


def test_tri_angle_without_the_availability_flag_still_parses() -> None:
    item = {k: v for k, v in load_json("apify_rooms_46397403.json")[0].items() if k != "isAvailable"}
    assert TriAnglePrice(FakeRunner({})).parse_item(item).total_cents == 367500


async def test_tri_angle_builds_input_maps_results_and_reports_missing_houses() -> None:
    runner = FakeRunner({TRI: load_json("apify_rooms_46397403.json")})

    batch = await TriAnglePrice(runner).fetch_prices(["46397403", "999"], make_trip(), 11)

    run_input = runner.calls_to(TRI)[0]
    assert (run_input["adults"], run_input["children"], run_input["infants"]) == (11, 0, 1)
    assert (run_input["checkIn"], run_input["checkOut"], run_input["currency"]) == ("2026-12-05", "2026-12-09", "BRL")
    assert [u["url"] for u in run_input["startUrls"]] == [
        "https://www.airbnb.com.br/rooms/46397403",
        "https://www.airbnb.com.br/rooms/999",
    ]
    assert runner.max_items_for(TRI) == [2]
    assert runner.max_charge_for(TRI) == [pytest.approx(3 * 2 * 0.004)]
    assert not isinstance(batch.results["46397403"], ApifyError)
    assert isinstance(batch.results["999"], ApifyError)


async def test_tri_angle_quotes_the_requested_group_size_and_reports_unavailable_houses() -> None:
    runner = FakeRunner({TRI: answer_prices})
    source = TriAnglePrice(runner)

    at_9 = await source.fetch_prices(["46397403", "52584351"], make_trip(), 9)
    at_13 = await source.fetch_prices(["46397403", "52584351"], make_trip(), 13)

    assert [call["adults"] for call in runner.calls_to(TRI)] == [9, 13]
    assert [(only(p).total_cents, only(p).original_total_cents) for p in at_9.results.values()] == [
        (367500, None),
        (294700, 324100),
    ]
    assert only(at_13.results["46397403"]).total_cents is None
    assert only(at_13.results["52584351"]).total_cents == 443500


async def test_tri_angle_whole_run_failure() -> None:
    batch = await TriAnglePrice(FakeRunner({}, fail_once={TRI})).fetch_prices(["1"], make_trip(), 9)
    assert isinstance(batch.results["1"], ApifyError)


@pytest.mark.parametrize("bad", ["R$ .", "R$ 0,00", "R$ 0", "free", 123, True, ["R$ 5"]])
def test_tri_angle_garbage_totals_are_parse_errors(bad: Any) -> None:
    item = load_json("apify_rooms_46397403.json")[0]
    item["price"]["price"] = bad
    with pytest.raises(PriceParseError):
        TriAnglePrice(FakeRunner({})).parse_item(item)


@pytest.mark.parametrize("bad", ["R$ .", "free", "R$ 0,00", 123, True])
def test_tri_angle_garbage_originals_are_parse_errors(bad: Any) -> None:
    item = load_json("apify_rooms_52584351.json")[0]
    item["price"]["originalPrice"] = bad
    with pytest.raises(PriceParseError):
        TriAnglePrice(FakeRunner({})).parse_item(item)


@pytest.mark.parametrize("price", ["oops", 5, ["x"]])
def test_tri_angle_non_dict_price_is_a_parse_error(price: Any) -> None:
    with pytest.raises(PriceParseError):
        TriAnglePrice(FakeRunner({})).parse_item({"id": "1", "price": price})


async def test_tri_angle_garbage_fails_its_house_and_spares_the_other() -> None:
    items = [*load_json("apify_rooms_46397403.json"), *load_json("apify_rooms_52584351.json")]
    items[0]["price"]["price"] = "R$ ."
    batch = await TriAnglePrice(FakeRunner({TRI: items})).fetch_prices(["46397403", "52584351"], make_trip(), 9)
    assert isinstance(batch.results["46397403"], PriceParseError)
    assert only(batch.results["52584351"]).total_cents == 369100


async def test_tri_angle_ceiling_has_no_float_noise() -> None:
    runner = FakeRunner({TRI: load_json("apify_rooms_46397403.json")})
    await TriAnglePrice(runner).fetch_prices(["46397403"], make_trip(), 9)
    assert runner.max_charge_for(TRI) == [0.012]


def test_tri_angle_estimate() -> None:
    assert TriAnglePrice(FakeRunner({})).estimate_usd(5) == pytest.approx(0.02)
