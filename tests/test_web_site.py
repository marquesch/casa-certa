import re
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from casa_certa.db.models import House
from casa_certa.web.crypto import PBKDF2_ITERATIONS, WeakPassphraseError, decrypt
from casa_certa.web.site import build_site_html, write_site
from tests.fakes import make_trip
from tests.test_xlsx import analysed_house

PASSPHRASE = "cavalo-nuvem-tijolo-lampada-rio"
PAYLOAD = re.compile(r'<script type="text/plain" id="payload" data-iterations="\d+">([^<]*)</script>')


async def test_write_site_creates_an_encrypted_index_and_nojekyll(
    session: AsyncSession, stored_house: House, tmp_path: Path
) -> None:
    house = await analysed_house(session, stored_house)

    index = write_site([house], make_trip(), PASSPHRASE, tmp_path / "site")

    assert index == tmp_path / "site" / "index.html"
    assert (tmp_path / "site" / ".nojekyll").is_file()
    shell = index.read_text(encoding="utf8")
    assert "Casa do Albatroz" not in shell and "R$" not in shell and PASSPHRASE not in shell
    match = PAYLOAD.search(shell)
    assert match is not None
    page = decrypt(match.group(1), PASSPHRASE)
    assert "Casa do Albatroz" in page and "Têm todos os essenciais" in page


async def test_each_build_produces_a_different_file(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)
    assert build_site_html([house], make_trip(), PASSPHRASE) != build_site_html([house], make_trip(), PASSPHRASE)


async def test_a_weak_passphrase_builds_nothing(session: AsyncSession, stored_house: House, tmp_path: Path) -> None:
    house = await analysed_house(session, stored_house)
    with pytest.raises(WeakPassphraseError):
        write_site([house], make_trip(), "casa2026", tmp_path / "site")
    assert not (tmp_path / "site").exists()


def test_the_default_cost_factor_is_the_documented_one() -> None:
    assert PBKDF2_ITERATIONS == 600_000
