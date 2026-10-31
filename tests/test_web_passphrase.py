from casa_certa.web.crypto import check_passphrase
from casa_certa.web.passphrase import WORDS, generate_passphrase


def test_the_word_list_is_large_and_clean() -> None:
    assert len(WORDS) >= 2048
    assert len(set(WORDS)) == len(WORDS)
    assert all(word.isalpha() for word in WORDS)


def test_a_passphrase_has_five_words_from_the_list_and_passes_the_check() -> None:
    passphrase = generate_passphrase()
    words = passphrase.split("-")
    assert len(words) == 5 and all(word in WORDS for word in words)
    check_passphrase(passphrase)


def test_passphrases_differ() -> None:
    assert generate_passphrase() != generate_passphrase()
