"""Spreadsheet export: a compact comparison sheet and a details sheet."""

from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from casa_certa.config import AspectKind, TripConfig
from casa_certa.db.models import House
from casa_certa.enums import AspectStatus, EvidenceSource, PointKind
from casa_certa.i18n import Labels, labels_for
from casa_certa.pricing import PriceRow, SizeQuote, cents_to_reais, price_table
from casa_certa.sources.airbnb_page import listing_url

SYMBOLS = {
    AspectStatus.MET: "✅",
    AspectStatus.PARTIAL: "⚠️",
    AspectStatus.UNMET: "❌",
    AspectStatus.UNKNOWN: "❓",
}
BRL_FORMAT = '"R$" #,##0.00'
BOLD = Font(bold=True)
WRAP = Alignment(wrap_text=True, vertical="top")
FAILURE_FONT = Font(bold=True, color="C00000")
NOTE_FONT = Font(italic=True)


def build_workbook(
    houses: list[House],
    failures: dict[str, str],
    trip: TripConfig,
    screened_out: dict[str, str] | None = None,
) -> Workbook:
    labels = labels_for(trip.language)
    workbook = Workbook()
    comparison = workbook.worksheets[0]
    comparison.title = labels.comparison_sheet
    _write_comparison(comparison, houses, failures, trip, labels)
    details = workbook.create_sheet(labels.details_sheet)
    _write_details(details, houses, trip, labels)
    if screened_out:
        _write_screened_out(workbook.create_sheet(labels.screened_out_sheet), screened_out, labels)
    return workbook


def _append(sheet: Worksheet, values: list[Any], *, bold: bool = False) -> int:
    sheet.append(values)
    row = sheet.max_row
    for cell in sheet[row]:
        cell.alignment = WRAP
        if bold:
            cell.font = BOLD
    return row


def _status(house: House, aspect_name: str) -> AspectStatus:
    verdict = house.analysis.verdict_for(aspect_name) if house.analysis else None
    return verdict.status if verdict else AspectStatus.UNKNOWN


def _dealbreakers(house: House, trip: TripConfig) -> list[str]:
    return [
        aspect.name
        for aspect in trip.aspects
        if aspect.kind is AspectKind.MUST_HAVE and _status(house, aspect.name) is AspectStatus.UNMET
    ]


def _price_basis(trip: TripConfig, labels: Labels) -> str:
    """Dates a price is for. Infants are excluded: Airbnb does not count them as guests."""
    return labels.price_basis.format(dates=f"{trip.check_in:%d/%m}\u2013{trip.check_out:%d/%m}")


def _price_rows(house: House, trip: TripConfig) -> list[PriceRow]:
    quotes = {
        guests: SizeQuote(quote.total_cents, quote.original_total_cents)
        for guests, quote in house.quotes_by_guests(trip).items()
    }
    return price_table(quotes, trip)


def _price_cell(sheet: Worksheet, row: int, column: int) -> None:
    """Show a numeric cell as BRL; text cells (n/d) are left alone."""
    cell = sheet.cell(row=row, column=column)
    if isinstance(cell.value, float):
        cell.number_format = BRL_FORMAT


def _price_or_na(cents: int | None, labels: Labels) -> float | str:
    return cents_to_reais(cents) if cents is not None else labels.not_available


def _write_comparison(
    sheet: Worksheet, houses: list[House], failures: dict[str, str], trip: TripConfig, labels: Labels
) -> None:
    aspect_headers = [f"★ {a.name}" if a.kind is AspectKind.MUST_HAVE else a.name for a in trip.aspects]
    _append(
        sheet,
        [
            labels.house,
            labels.rating,
            labels.reviews,
            *aspect_headers,
            labels.dealbreakers,
            labels.per_person_at.format(people=trip.min_people),
            labels.per_person_at.format(people=trip.max_people),
            labels.confidence,
            labels.summary,
        ],
        bold=True,
    )
    for house in houses:
        facts = house.facts
        rows = _price_rows(house, trip)
        smallest, largest = rows[0], rows[-1]
        row = _append(
            sheet,
            [
                house.name,
                facts.rating if facts else None,
                facts.review_count if facts else None,
                *[SYMBOLS[_status(house, aspect.name)] for aspect in trip.aspects],
                ", ".join(_dealbreakers(house, trip)) or None,
                _price_or_na(smallest.per_person_cents, labels),
                _price_or_na(largest.per_person_cents, labels),
                house.analysis.confidence_note if house.analysis else None,
                house.analysis.summary if house.analysis else None,
            ],
        )
        first_price_column = 3 + len(trip.aspects) + 2  # after house, rating, reviews, aspects, dealbreakers
        _price_cell(sheet, row, first_price_column)
        _price_cell(sheet, row, first_price_column + 1)
        sheet.cell(row=row, column=1).hyperlink = listing_url(house.airbnb_id)
    for room_id, message in failures.items():
        row = _append(sheet, [f"{room_id} — {labels.failed}: {message}"])
        failure_cell = sheet.cell(row=row, column=1)
        failure_cell.alignment = Alignment(vertical="top")  # no wrap: the text overflows into the empty cells
        failure_cell.font = FAILURE_FONT
    note_row = _append(sheet, [_price_basis(trip, labels)])
    sheet.cell(row=note_row, column=1).alignment = Alignment(vertical="top")  # the note overflows to the right
    sheet.cell(row=note_row, column=1).font = NOTE_FONT
    sheet.column_dimensions["A"].width = 30
    sheet.column_dimensions[get_column_letter(sheet.max_column - 1)].width = 30  # the confidence column
    sheet.column_dimensions[get_column_letter(sheet.max_column)].width = 60  # the summary column
    sheet.freeze_panes = "B2"


def _write_screened_out(sheet: Worksheet, screened_out: dict[str, str], labels: Labels) -> None:
    """Houses dropped before the paid steps; the link lets a wrong drop be rescued by adding it to `urls`."""
    _append(sheet, [labels.id, labels.link, labels.reason], bold=True)
    for room_id, reason in screened_out.items():
        row = _append(sheet, [room_id, listing_url(room_id), reason])
        sheet.cell(row=row, column=3).data_type = "s"  # the reason is LLM text: never a formula, even after "="
        sheet.cell(row=row, column=2).hyperlink = listing_url(room_id)
    for column, width in zip("ABC", (16, 44, 80), strict=True):
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = "A2"


def _source_label(source: EvidenceSource, labels: Labels) -> str:
    return {
        EvidenceSource.LISTING: labels.source_listing,
        EvidenceSource.REVIEW: labels.source_review,
        EvidenceSource.EXTRA: labels.source_extra,
    }[source]


def _write_price_table(sheet: Worksheet, rows: list[PriceRow], trip: TripConfig, labels: Labels) -> None:
    """The full table, one row per group size; the original price column appears only when some size has one."""
    has_original = any(row.original_total_cents is not None for row in rows)
    _append(sheet, [labels.prices], bold=True)
    _append(sheet, [_price_basis(trip, labels)])
    headers = [labels.people, labels.total, labels.per_person, *([labels.original_price] if has_original else [])]
    _append(sheet, headers, bold=True)
    for row in rows:
        values: list[Any] = [
            row.people,
            _price_or_na(row.total_cents, labels),
            _price_or_na(row.per_person_cents, labels),
        ]
        if has_original:
            values.append(cents_to_reais(row.original_total_cents) if row.original_total_cents is not None else None)
        price_row = _append(sheet, values)
        for column in range(2, len(values) + 1):
            _price_cell(sheet, price_row, column)


def _write_details(sheet: Worksheet, houses: list[House], trip: TripConfig, labels: Labels) -> None:
    for house in houses:
        facts = house.facts
        analysis = house.analysis
        title_row = _append(sheet, [house.name], bold=True)
        sheet.cell(row=title_row, column=1).hyperlink = listing_url(house.airbnb_id)
        stats_row = _append(
            sheet,
            [labels.rating, facts.rating if facts else None, labels.reviews, facts.review_count if facts else None],
        )
        for label_column in (1, 3):  # bold the labels, not the values
            sheet.cell(row=stats_row, column=label_column).font = BOLD
        if analysis:
            _append(sheet, [labels.summary, analysis.summary])
            if analysis.confidence_note:
                _append(sheet, [labels.confidence, analysis.confidence_note])
        if house.extra.strip():
            _append(sheet, [labels.extra, house.extra.strip()])

        if analysis:
            _append(sheet, [labels.pros], bold=True)
            for point in (p for p in analysis.points if p.kind is PointKind.PRO):
                _append(sheet, [point.text])
            _append(sheet, [labels.cons], bold=True)
            for point in (p for p in analysis.points if p.kind is PointKind.CON):
                _append(sheet, [point.text])
            _append(sheet, [labels.aspect, labels.status, labels.reason, labels.evidence], bold=True)
            for verdict in analysis.verdicts:
                evidence = "\n".join(f'[{_source_label(e.source, labels)}] "{e.quote}"' for e in verdict.evidence)
                _append(sheet, [verdict.aspect_name, SYMBOLS[verdict.status], verdict.reason, evidence])

        if house.quotes_by_guests(trip):
            _write_price_table(sheet, _price_rows(house, trip), trip, labels)
        sheet.append([])
        sheet.append([])
    for column, width in zip("ABCD", (34, 16, 50, 60), strict=True):
        sheet.column_dimensions[column].width = width
