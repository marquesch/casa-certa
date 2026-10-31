"""Encrypts the page with a shared passphrase. The browser decrypts it with WebCrypto (see static/unlock.js).

Format: base64(salt[16] + iv[12] + AES-256-GCM ciphertext with its 16-byte tag), key = PBKDF2-HMAC-SHA256.
"""

import base64
import re
import secrets
import unicodedata

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16
IV_BYTES = 12
KEY_BYTES = 32
MIN_WORDS = 5
MIN_WORD_LENGTH = 3
MIN_WORD_CHARACTERS = 20
MIN_LONG_CHARACTERS = 40
MIN_DISTINCT = 12


class WeakPassphraseError(ValueError):
    """The passphrase is too easy to guess for a payload whose ciphertext is public."""


class DecryptionError(Exception):
    """Wrong passphrase or modified payload."""


def _normalise(passphrase: str) -> str:
    """The same normalisation the browser applies, so accents and stray spaces cannot change the key."""
    return unicodedata.normalize("NFKC", passphrase.strip())


def check_passphrase(passphrase: str) -> None:
    """A guard against obviously weak choices, not a measure of strength: it cannot tell a good passphrase from a
    predictable one (`the-cat-sat-on-mat` would pass). Use the generated one.

    Accepted: five words of 3+ letters, 20+ characters and 12+ distinct characters; or 40+ characters with 12+
    distinct characters."""
    text = _normalise(passphrase)
    words = [word for word in re.split(r"[-\s]+", text) if len(word) >= MIN_WORD_LENGTH]
    varied = len(set(text)) >= MIN_DISTINCT
    by_words = len(words) >= MIN_WORDS and len(text) >= MIN_WORD_CHARACTERS
    if not varied or not (by_words or len(text) >= MIN_LONG_CHARACTERS):
        raise WeakPassphraseError(
            f"The site passphrase needs at least {MIN_WORDS} words ({MIN_WORD_CHARACTERS}+ characters) or "
            f"{MIN_LONG_CHARACTERS}+ characters, with {MIN_DISTINCT}+ different characters. "
            "Run `make passphrase` to generate one."
        )


def _derive_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=salt, iterations=iterations)
    return kdf.derive(_normalise(passphrase).encode("utf8"))


def encrypt(plaintext: str, passphrase: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    iv = secrets.token_bytes(IV_BYTES)
    ciphertext = AESGCM(_derive_key(passphrase, salt, iterations)).encrypt(iv, plaintext.encode("utf8"), None)
    return base64.b64encode(salt + iv + ciphertext).decode("ascii")


def decrypt(payload: str, passphrase: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    raw = base64.b64decode(payload)
    salt, iv, ciphertext = raw[:SALT_BYTES], raw[SALT_BYTES : SALT_BYTES + IV_BYTES], raw[SALT_BYTES + IV_BYTES :]
    try:
        return AESGCM(_derive_key(passphrase, salt, iterations)).decrypt(iv, ciphertext, None).decode("utf8")
    except InvalidTag as error:
        raise DecryptionError("wrong passphrase or modified payload") from error
