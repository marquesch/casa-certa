"""Name matching shared by the analyst (output validation) and the exporter."""


def normalize_name(name: str) -> str:
    """Casefold, trim and collapse whitespace, so 'Ar  Condicionado ' matches 'ar condicionado'."""
    return " ".join(name.casefold().split())
