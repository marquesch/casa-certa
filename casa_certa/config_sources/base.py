"""Where a TripConfig comes from. Today a YAML file; later possibly WhatsApp messages."""

from typing import Protocol

from casa_certa.config import TripConfig


class ConfigError(Exception):
    """The trip configuration could not be read or is invalid."""


class ConfigSource(Protocol):
    async def load(self) -> TripConfig: ...
