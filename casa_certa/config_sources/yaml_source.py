"""Load a TripConfig from a YAML file."""

import asyncio
from pathlib import Path

import yaml
from pydantic import ValidationError

from casa_certa.config import TripConfig
from casa_certa.config_sources.base import ConfigError


class YamlConfigSource:
    def __init__(self, path: Path) -> None:
        self._path = path

    async def load(self) -> TripConfig:
        try:
            text = await asyncio.to_thread(self._path.read_text, encoding="utf8")
        except OSError as e:
            raise ConfigError(f"Cannot read {self._path}: {e}") from e
        try:
            return TripConfig.model_validate(yaml.safe_load(text))
        except (yaml.YAMLError, ValidationError) as e:
            raise ConfigError(f"Invalid trip file {self._path}: {e}") from e
