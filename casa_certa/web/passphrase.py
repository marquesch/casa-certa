"""Generates the shared site passphrase: random words drawn from a vendored word list."""

import secrets
from pathlib import Path

WORD_COUNT = 5
WORDS = tuple((Path(__file__).parent / "static" / "wordlist.txt").read_text(encoding="utf8").split())


def generate_passphrase(words: int = WORD_COUNT) -> str:
    return "-".join(secrets.choice(WORDS) for _ in range(words))
