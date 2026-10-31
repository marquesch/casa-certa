from casa_certa.i18n import labels_for


def test_labels_for_exact_and_prefix_and_fallback() -> None:
    assert labels_for("pt-BR").house == "Casa"
    assert labels_for("pt").house == "Casa"
    assert labels_for("en").house == "House"
    assert labels_for("fr").house == "House"  # unknown languages fall back to English
