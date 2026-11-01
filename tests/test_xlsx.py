from sqlalchemy.ext.asyncio import AsyncSession

from casa_certa.db.models import House
from casa_certa.db.repository import HouseRepository
from casa_certa.enums import AspectStatus
from casa_certa.export.xlsx import build_workbook
from casa_certa.sources.apify import PriceData
from tests.fakes import make_trip, sample_analysis

BRL_FORMAT = '"R$" #,##0.00'


async def save_albatroz_prices(session: AsyncSession, house: House, original_cents: int | None = None) -> None:
    """The real Albatroz quotes: 3675 flat from 9 to 12 guests, unavailable at 13."""
    repo = HouseRepository(session)
    for guests in make_trip().group_sizes():
        price = PriceData(total_cents=367500 if guests <= 12 else None, original_total_cents=original_cents)
        await repo.save_quote(house, make_trip(), guests, price)


async def analysed_house(session: AsyncSession, house: House) -> House:
    repo = HouseRepository(session)
    await save_albatroz_prices(session, house)
    await repo.save_analysis(house, "hash", "test-model", sample_analysis(["Perto da praia", "Piscina"]))
    await session.commit()
    return house


def all_cell_values(sheet: object) -> list[str]:
    return [str(cell.value) for row in sheet.iter_rows() for cell in row if cell.value is not None]  # type: ignore[attr-defined]


async def test_comparison_sheet_has_one_row_per_house(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)

    workbook = build_workbook([house], {}, make_trip())

    assert workbook.sheetnames == ["Comparativo", "Detalhes"]
    sheet = workbook["Comparativo"]
    header = [cell.value for cell in sheet[1]]
    assert header == [
        "Casa",
        "Nota",
        "Avaliações",
        "★ Perto da praia",
        "Piscina",
        "Eliminatórios",
        "Por pessoa (9)",
        "Por pessoa (13)",
        "Confiança",
        "Resumo",
    ]
    row = [cell.value for cell in sheet[2]]
    assert row == ["Casa do Albatroz", 4.95, 87, "✅", "✅", None, 408.34, "n/d", None, "Boa casa para o grupo"]
    assert sheet["G2"].number_format == BRL_FORMAT  # 367500 / 9 rounded up
    assert sheet["H2"].number_format == "General"  # 13 guests: unavailable
    assert sheet.column_dimensions["J"].width == 60  # the summary stays the wide last column
    assert sheet["A3"].value == "05/12\u201309/12, hóspedes pagantes; bebês não contam"
    assert sheet["A2"].hyperlink.target == "https://www.airbnb.com.br/rooms/46397403"


async def test_dealbreaker_column_lists_unmet_must_haves(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)
    assert house.analysis is not None
    house.analysis.verdicts[0].status = AspectStatus.UNMET

    sheet = build_workbook([house], {}, make_trip())["Comparativo"]

    assert sheet["D2"].value == "❌"
    assert sheet["F2"].value == "Perto da praia"


async def test_missing_analysis_and_price_render_as_unknown_and_not_available(
    session: AsyncSession, stored_house: House
) -> None:
    sheet = build_workbook([stored_house], {}, make_trip())["Comparativo"]
    row = [cell.value for cell in sheet[2]]
    assert row[3:5] == ["❓", "❓"]
    assert row[6:8] == ["n/d", "n/d"]
    assert sheet["G2"].number_format == "General"


async def test_failures_are_listed_after_the_houses(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)
    sheet = build_workbook([house], {"999": "HTTP 403 while fetching"}, make_trip())["Comparativo"]
    assert sheet["A3"].value == "999 — Falhou: HTTP 403 while fetching"
    assert not sheet["A3"].alignment.wrap_text  # long text overflows to the right instead of a tall cell
    assert sheet["A3"].font.bold


async def test_details_sheet_has_points_evidence_and_price_table(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)

    values = all_cell_values(build_workbook([house], {}, make_trip())["Detalhes"])

    assert "Piscina privativa" in values  # pro
    assert "Sem ar-condicionado" in values  # con
    assert any("100 metros do mar" in value for value in values)  # evidence quote
    assert "O dono confirmou que a praia fica a 3 minutos a pé." in values  # extra note
    sheet = build_workbook([house], {}, make_trip())["Detalhes"]
    prices = {cell.value: cell for row in sheet.iter_rows() for cell in row if isinstance(cell.value, float)}
    assert prices[3675.0].number_format == BRL_FORMAT
    assert 408.34 in prices  # 367500 / 9 rounded up
    assert prices[408.34].number_format == BRL_FORMAT
    assert "n/d" in values  # 13 people: unavailable
    assert "05/12\u201309/12, hóspedes pagantes; bebês não contam" in values
    assert not any(value.startswith("Preço original") for value in values)
    header = next(row for row in sheet.iter_rows() if row[0].value == "Pessoas")
    assert [cell.value for cell in header if cell.value is not None] == ["Pessoas", "Total", "Por pessoa"]
    table = [[cell.value for cell in sheet[(header[0].row or 0) + offset][:3]] for offset in range(1, 6)]
    assert table == [
        [9, 3675.0, 408.34],
        [10, 3675.0, 367.5],
        [11, 3675.0, 334.1],
        [12, 3675.0, 306.25],
        [13, "n/d", "n/d"],
    ]


async def test_details_show_the_original_price_when_discounted(session: AsyncSession, stored_house: House) -> None:
    await save_albatroz_prices(session, stored_house, original_cents=420000)
    await session.commit()

    sheet = build_workbook([stored_house], {}, make_trip())["Detalhes"]

    cells = [cell for row in sheet.iter_rows() for cell in row if cell.value == "Preço original"]
    assert len(cells) == 1
    assert cells[0].column == 4  # after Pessoas, Total, Por pessoa
    first = sheet.cell(row=cells[0].row + 1, column=4)
    assert first.value == 4200.0
    assert first.number_format == BRL_FORMAT
    assert sheet.cell(row=cells[0].row + 5, column=4).value is None  # 13 guests: unavailable, no original
    assert "Preço original" not in [c.value for c in build_workbook([stored_house], {}, make_trip())["Comparativo"][1]]


async def test_details_rating_row_has_bold_labels(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)
    sheet = build_workbook([house], {}, make_trip())["Detalhes"]
    assert [cell.value for cell in sheet[2]][:4] == ["Nota", 4.95, "Avaliações", 87]
    assert sheet["A2"].font.bold and sheet["C2"].font.bold
    assert not sheet["B2"].font.bold


async def test_status_matching_ignores_case_and_spacing(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)
    assert house.analysis is not None
    house.analysis.verdicts[0].aspect_name = "  perto  DA praia "
    house.analysis.verdicts[0].status = AspectStatus.UNMET

    sheet = build_workbook([house], {}, make_trip())["Comparativo"]

    assert sheet["D2"].value == "❌"
    assert sheet["F2"].value == "Perto da praia"


async def test_comparison_sheet_shows_the_confidence_note(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)
    assert house.analysis is not None
    house.analysis.confidence_note = "Poucas avaliações"

    sheet = build_workbook([house], {"999": "boom"}, make_trip())["Comparativo"]

    assert sheet["I2"].value == "Poucas avaliações"
    assert sheet["J2"].value == "Boa casa para o grupo"
    assert sheet["A3"].value == "999 \u2014 Falhou: boom"
    assert sheet.freeze_panes == "B2"


async def test_per_person_columns_show_the_per_guest_fee_of_a_house_that_charges_one(
    session: AsyncSession, stored_house: House
) -> None:
    repo = HouseRepository(session)
    for guests, total in zip(make_trip().group_sizes(), (294700, 331900, 369100, 406300, 443500), strict=True):
        await repo.save_quote(stored_house, make_trip(), guests, PriceData(total_cents=total))
    await session.commit()

    row = [cell.value for cell in build_workbook([stored_house], {}, make_trip())["Comparativo"][2]]

    assert row[6:8] == [327.45, 341.16]  # the larger group pays more each: ceil(2947/9) and ceil(4435/13)


async def test_screened_out_sheet_lists_id_link_and_reason(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)

    workbook = build_workbook([house], {}, make_trip(), {"111": "preço acima do limite"})

    assert workbook.sheetnames == ["Comparativo", "Detalhes", "Descartadas"]
    sheet = workbook["Descartadas"]
    assert [cell.value for cell in sheet[1]] == ["Id", "Link", "Motivo"]
    assert [cell.value for cell in sheet[2]] == ["111", "https://www.airbnb.com.br/rooms/111", "preço acima do limite"]
    assert sheet["B2"].hyperlink.target == "https://www.airbnb.com.br/rooms/111"


async def test_no_screened_out_sheet_when_nothing_was_dropped(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)

    assert build_workbook([house], {}, make_trip()).sheetnames == ["Comparativo", "Detalhes"]
    assert build_workbook([house], {}, make_trip(), {}).sheetnames == ["Comparativo", "Detalhes"]


async def test_a_screened_out_reason_starting_with_equals_is_text_not_a_formula(
    session: AsyncSession, stored_house: House
) -> None:
    house = await analysed_house(session, stored_house)

    workbook = build_workbook([house], {}, make_trip(), {"111": '=HYPERLINK("http://x","y")'})

    cell = workbook["Descartadas"]["C2"]
    assert cell.data_type == "s"
    assert cell.value == '=HYPERLINK("http://x","y")'
