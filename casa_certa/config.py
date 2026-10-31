"""Trip configuration (what to compare) and application settings (secrets, models, database)."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///casa_certa.db"


class AspectKind(StrEnum):
    MUST_HAVE = "must_have"
    NICE_TO_HAVE = "nice_to_have"


class Aspect(BaseModel):
    name: str
    kind: AspectKind = AspectKind.NICE_TO_HAVE
    details: str = ""


class TripConfig(BaseModel):
    """Everything the pipeline needs to know about the trip. The only input contract of the pipeline."""

    urls: list[str] = Field(min_length=1)
    check_in: date
    check_out: date
    # Infants are a fixed number passed unchanged to every quote; they never count as paying guests.
    infants: int = Field(default=0, ge=0)
    # min_people to max_people count paying guests (adults + children), never infants.
    min_people: int = Field(default=9, ge=1)
    max_people: int = Field(default=13, ge=1)
    aspects: list[Aspect] = Field(default_factory=list)
    language: str = "pt-BR"
    review_cap: int = Field(default=30, ge=0)
    # What the family knows about a house (for example from the photos), keyed by listing URL or id.
    notes: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_ranges(self) -> TripConfig:
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        if self.min_people > self.max_people:
            raise ValueError("min_people cannot exceed max_people")
        return self

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days

    def group_sizes(self) -> range:
        return range(self.min_people, self.max_people + 1)


class DatabaseSettings(BaseSettings):
    """Only the database URL; used by Alembic, which must not require API keys."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = DEFAULT_DATABASE_URL


class SiteSettings(DatabaseSettings):
    """What `casa-certa site` needs: the database and the shared passphrase (no API keys)."""

    site_passphrase: SecretStr


class Settings(DatabaseSettings):
    apify_token: SecretStr
    deepseek_api_key: SecretStr
    cheap_model: str = "deepseek-chat"
    # Model for the review analysis. Cheap model for now; set ANALYSIS_MODEL=deepseek-reasoner later to move
    # this task to the reasoning model without code changes.
    analysis_model: str = "deepseek-chat"
    page_delay_min: float = 2.0
    page_delay_max: float = 5.0
    # Ordered fallback chains of source names (see casa_certa.sources.registry), as comma-separated env vars.
    price_sources: Annotated[list[str], NoDecode] = ["thelastscraper"]
    review_sources: Annotated[list[str], NoDecode] = ["kaix", "steadyscrape", "tri_angle"]

    @field_validator("price_sources", "review_sources", mode="before")
    @classmethod
    def _split_names(cls, value: object) -> object:
        if isinstance(value, str):
            return [name.strip() for name in value.split(",") if name.strip()]
        return value
