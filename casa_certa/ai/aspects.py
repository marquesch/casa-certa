"""Shared check that a model answered with exactly the requested aspects."""

from collections.abc import Sequence
from typing import Protocol

from pydantic_ai import ModelRetry

from casa_certa.names import normalize_name


class HasAspect(Protocol):
    aspect: str


def canonicalise_aspects(requested_names: Sequence[str], verdicts: Sequence[HasAspect]) -> None:
    """Require exactly the requested aspects (ignoring case and spacing) and restore their spelling in place."""
    requested = {normalize_name(name): name for name in requested_names}
    received = [normalize_name(verdict.aspect) for verdict in verdicts]
    if len(received) != len(requested) or set(received) != set(requested):
        raise ModelRetry(
            "The aspects must be exactly these, spelled as given, one verdict each: "
            f"{list(requested_names)}. You returned: {[verdict.aspect for verdict in verdicts]}."
        )
    for verdict in verdicts:
        verdict.aspect = requested[normalize_name(verdict.aspect)]
