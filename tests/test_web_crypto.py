import base64

import pytest

from casa_certa.web.crypto import DecryptionError, WeakPassphraseError, check_passphrase, decrypt, encrypt

PASSPHRASE = "cavalo nuvem tijolo lampada rio"
FAST = 1000  # iterations: keep the tests quick


def test_a_page_survives_a_round_trip() -> None:
    page = "<html>Casa do Albatroz — R$ 408,34 ✅</html>"
    assert decrypt(encrypt(page, PASSPHRASE, iterations=FAST), PASSPHRASE, iterations=FAST) == page


def test_the_payload_does_not_contain_the_plaintext() -> None:
    assert "Albatroz" not in encrypt("Casa do Albatroz", PASSPHRASE, iterations=FAST)


def test_every_encryption_is_different() -> None:
    assert encrypt("x", PASSPHRASE, iterations=FAST) != encrypt("x", PASSPHRASE, iterations=FAST)


def test_a_wrong_passphrase_fails() -> None:
    payload = encrypt("secret", PASSPHRASE, iterations=FAST)
    with pytest.raises(DecryptionError):
        decrypt(payload, "outra senha qualquer aqui sim", iterations=FAST)


def test_a_modified_payload_fails() -> None:
    raw = bytearray(base64.b64decode(encrypt("secret", PASSPHRASE, iterations=FAST)))
    raw[-1] ^= 0x01  # flip a bit inside the GCM tag, so the base64 stays valid
    with pytest.raises(DecryptionError):
        decrypt(base64.b64encode(bytes(raw)).decode("ascii"), PASSPHRASE, iterations=FAST)


def test_accents_and_spacing_do_not_change_the_key() -> None:
    composed = "maçã verde céu azul sol"
    decomposed = "maçã verde céu azul sol"
    payload = encrypt(f"  {composed}  ", f"  {composed}  ", iterations=FAST)
    assert decrypt(payload, composed, iterations=FAST) == f"  {composed}  "
    assert decrypt(payload, decomposed, iterations=FAST) == f"  {composed}  "


@pytest.mark.parametrize(
    "passphrase",
    [
        "casa2026",
        "a-b-c-d-e",
        "um dois tres quatro",
        "the cat dog red sun",
        "x" * 30,
        "x" * 100,
        "abab-abab-abab-abab-abab-abab",
        "",
    ],
)
def test_weak_passphrases_are_refused(passphrase: str) -> None:
    with pytest.raises(WeakPassphraseError):
        check_passphrase(passphrase)


@pytest.mark.parametrize(
    "passphrase",
    [
        "cavalo-nuvem-tijolo-lampada-rio",
        "cavalo nuvem tijolo lampada rio",
        "abcdefghijklmnopqrstuvwxyz0123456789ABCD",
    ],
)
def test_strong_enough_passphrases_are_accepted(passphrase: str) -> None:
    check_passphrase(passphrase)
