"""Generates the shared site passphrase: random words drawn from a vendored word list."""

import secrets
from pathlib import Path

from casa_certa.web.crypto import WeakPassphraseError, check_passphrase

WORD_COUNT = 5
WORDS = tuple((Path(__file__).parent / "static" / "wordlist.txt").read_text(encoding="utf8").split())


def generate_passphrase(words: int = WORD_COUNT) -> str:
    while True:
        candidate = "-".join(secrets.choice(WORDS) for _ in range(words))
        try:
            check_passphrase(candidate)
        except WeakPassphraseError:  # the rare draw of five very short or repetitive words: draw again
            continue
        return candidate
