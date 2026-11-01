"""Builds the encrypted site files from the stored houses."""

from collections.abc import Sequence
from pathlib import Path

from casa_certa.config import TripConfig
from casa_certa.db.models import House
from casa_certa.i18n import labels_for
from casa_certa.web.crypto import PBKDF2_ITERATIONS, check_passphrase, encrypt
from casa_certa.web.model import build_site
from casa_certa.web.render import render_page, render_shell


def build_site_html(houses: list[House], trip: TripConfig, passphrase: str, hit_ids: Sequence[str] = ()) -> str:
    """The unlock shell holding the encrypted page. Raises WeakPassphraseError before doing any work."""
    check_passphrase(passphrase)
    labels = labels_for(trip.language)
    page = render_page(build_site(houses, trip, hit_ids), trip, labels)
    return render_shell(encrypt(page, passphrase), PBKDF2_ITERATIONS, labels, trip.language)


def write_site(
    houses: list[House], trip: TripConfig, passphrase: str, output_dir: Path, hit_ids: Sequence[str] = ()
) -> Path:
    html = build_site_html(houses, trip, passphrase, hit_ids)
    output_dir.mkdir(parents=True, exist_ok=True)
    index = output_dir / "index.html"
    index.write_text(html, encoding="utf8")
    (output_dir / ".nojekyll").write_text("", encoding="utf8")  # GitHub Pages: serve the files as they are
    return index
