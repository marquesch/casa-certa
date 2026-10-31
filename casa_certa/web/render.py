"""Renders the site view model to the page (still plain HTML: encryption happens afterwards)."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

from casa_certa.config import TripConfig
from casa_certa.enums import AspectStatus, EvidenceSource
from casa_certa.i18n import Labels
from casa_certa.pricing import format_brl
from casa_certa.web.model import SiteModel

WEB_DIR = Path(__file__).parent
BEACH_BANDS_M = (100, 300, 1000)
STATUS_SYMBOLS = {
    AspectStatus.MET: "✅",
    AspectStatus.PARTIAL: "⚠️",
    AspectStatus.UNMET: "❌",
    AspectStatus.UNKNOWN: "❓",
}


def read_static(name: str) -> Markup:
    """A bundled CSS or JS file, to be inlined as is."""
    return Markup((WEB_DIR / "static" / name).read_text(encoding="utf8"))


def format_distance(meters: int, labels: Labels) -> str:
    if meters < 1000:
        return f"{meters} m"
    kilometers = f"{meters / 1000:.1f}".rstrip("0").rstrip(".")
    return f"{kilometers.replace('.', labels.decimal_separator)} km"


def _environment(labels: Labels) -> Environment:
    environment = Environment(
        loader=FileSystemLoader(WEB_DIR / "templates"), autoescape=True, undefined=StrictUndefined
    )
    environment.filters["brl"] = format_brl
    environment.filters["distance"] = lambda meters: format_distance(meters, labels)
    return environment


def _status_texts(labels: Labels) -> dict[str, tuple[str, str]]:
    names = {
        AspectStatus.MET: labels.status_met,
        AspectStatus.PARTIAL: labels.status_partial,
        AspectStatus.UNMET: labels.status_unmet,
        AspectStatus.UNKNOWN: labels.status_unknown,
    }
    return {status.value: (STATUS_SYMBOLS[status], names[status]) for status in AspectStatus}


def _source_texts(labels: Labels) -> dict[str, str]:
    return {
        EvidenceSource.LISTING.value: labels.source_listing,
        EvidenceSource.REVIEW.value: labels.source_review,
        EvidenceSource.EXTRA.value: labels.source_extra,
    }


def render_page(site: SiteModel, trip: TripConfig, labels: Labels) -> str:
    dates = f"{site.check_in:%d/%m}\u2013{site.check_out:%d/%m}"
    return (
        _environment(labels)
        .get_template("page.html.j2")
        .render(
            site=site,
            labels=labels,
            language=trip.language,
            trip_line=labels.trip_line.format(dates=dates, min=site.min_people, max=site.max_people),
            updated=labels.updated.format(date=f"{site.updated_at:%d/%m}") if site.updated_at else "",
            price_basis=labels.price_basis.format(dates=dates),
            statuses=_status_texts(labels),
            source_texts=_source_texts(labels),
            beach_bands=[
                (meters, labels.beach_up_to.format(distance=format_distance(meters, labels)))
                for meters in BEACH_BANDS_M
            ],
            css=read_static("site.css"),
            script=Markup(f"{read_static('filters.js')}\n{read_static('site.js')}"),
        )
    )
