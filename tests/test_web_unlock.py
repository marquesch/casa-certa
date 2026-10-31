import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from casa_certa.i18n import labels_for
from casa_certa.web.crypto import encrypt
from casa_certa.web.render import render_shell

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "tests" / "js" / "run_unlock.js"
PASSPHRASE = "cavalo nuvem tijolo lampada rio"
PAGE = "<!doctype html><html><body>Casa do Albatroz ✅ R$ 408,34</body></html>"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def build_shell(tmp_path: Path, payload: str | None = None) -> Path:
    labels = labels_for("pt-BR")
    shell = render_shell(payload or encrypt(PAGE, PASSPHRASE, iterations=1000), 1000, labels, "pt-BR")
    path = tmp_path / "index.html"
    path.write_text(shell, encoding="utf8")
    return path


def unlock(shell: Path, passphrase: str, remember: bool = False) -> dict[str, object]:
    result = subprocess.run(
        ["node", str(RUNNER), str(shell), passphrase, "remember" if remember else "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    output: dict[str, object] = json.loads(result.stdout)
    return output


def test_the_shell_does_not_contain_the_page(tmp_path: Path) -> None:
    text = build_shell(tmp_path).read_text(encoding="utf8")
    assert "Albatroz" not in text and PASSPHRASE not in text
    assert 'name="robots" content="noindex"' in text


def test_the_right_passphrase_writes_the_decrypted_page(tmp_path: Path) -> None:
    result = unlock(build_shell(tmp_path), PASSPHRASE)
    assert result == {"written": [PAGE], "message": "", "stored": []}


def test_a_passphrase_with_stray_spaces_and_decomposed_accents_still_works(tmp_path: Path) -> None:
    composed = "maçã verde céu azul sol"
    decomposed = "maçã verde céu azul sol"
    payload = encrypt(PAGE, composed, iterations=1000)
    result = unlock(build_shell(tmp_path, payload), f"  {decomposed} ")
    assert result["written"] == [PAGE]


def test_a_wrong_passphrase_shows_the_error_and_writes_nothing(tmp_path: Path) -> None:
    result = unlock(build_shell(tmp_path), "outra senha qualquer aqui sim")
    assert result == {"written": [], "message": "WRONG", "stored": []}


def test_a_modified_payload_is_rejected(tmp_path: Path) -> None:
    raw = bytearray(base64.b64decode(encrypt(PAGE, PASSPHRASE, iterations=1000)))
    raw[-1] ^= 0x01  # flip a bit inside the GCM tag, so the base64 stays valid
    tampered = base64.b64encode(bytes(raw)).decode("ascii")
    result = unlock(build_shell(tmp_path, tampered), PASSPHRASE)
    assert result["written"] == [] and result["message"] == "WRONG"


def test_the_passphrase_is_stored_only_when_asked(tmp_path: Path) -> None:
    shell = build_shell(tmp_path)
    assert unlock(shell, PASSPHRASE, remember=True)["stored"] == ["casa-certa-passphrase"]
    assert unlock(shell, PASSPHRASE, remember=False)["stored"] == []
