from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from casa_certa.config import Aspect, AspectKind, Settings, TripConfig
from casa_certa.config_sources.base import ConfigError
from casa_certa.config_sources.yaml_source import YamlConfigSource
from casa_certa.sources.registry import SourceConfigError, build_price_source
from tests.fakes import FakeRunner


def make_trip(**overrides: object) -> TripConfig:
    values: dict[str, object] = {
        "urls": ["https://www.airbnb.com.br/rooms/1"],
        "check_in": date(2026, 12, 5),
        "check_out": date(2026, 12, 9),
    }
    values.update(overrides)
    return TripConfig.model_validate(values)


def test_nights_and_group_sizes() -> None:
    trip = make_trip(min_people=9, max_people=13)
    assert trip.nights == 4
    assert list(trip.group_sizes()) == [9, 10, 11, 12, 13]


def test_check_out_must_be_after_check_in() -> None:
    with pytest.raises(ValidationError):
        make_trip(check_in=date(2026, 12, 9), check_out=date(2026, 12, 5))


def test_min_people_cannot_exceed_max_people() -> None:
    with pytest.raises(ValidationError):
        make_trip(min_people=14, max_people=13)


def test_aspect_kind_defaults_to_nice_to_have() -> None:
    assert Aspect(name="Pool").kind is AspectKind.NICE_TO_HAVE


def test_settings_can_be_built_without_env_file() -> None:
    settings = Settings(_env_file=None, apify_token="a", deepseek_api_key="b")
    assert settings.apify_token.get_secret_value() == "a"
    assert settings.database_url.startswith("sqlite+aiosqlite")


def test_source_chains_default_to_the_cheapest_first() -> None:
    settings = Settings(_env_file=None, apify_token="a", deepseek_api_key="b")
    assert settings.price_sources == ["thelastscraper"]
    assert settings.review_sources == ["kaix", "steadyscrape", "tri_angle"]


def test_source_chains_are_read_from_comma_separated_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRICE_SOURCES", " tri_angle , thelastscraper ")
    monkeypatch.setenv("REVIEW_SOURCES", "steadyscrape")
    settings = Settings(_env_file=None, apify_token="a", deepseek_api_key="b")
    assert settings.price_sources == ["tri_angle", "thelastscraper"]
    assert settings.review_sources == ["steadyscrape"]


def test_an_empty_source_env_var_gives_an_empty_chain_for_the_registry_to_reject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PRICE_SOURCES", "")
    settings = Settings(_env_file=None, apify_token="a", deepseek_api_key="b")
    assert settings.price_sources == []
    with pytest.raises(SourceConfigError):
        build_price_source(settings.price_sources, FakeRunner({}))


async def test_yaml_source_loads_trip(tmp_path: Path) -> None:
    path = tmp_path / "trip.yaml"
    path.write_text(
        """
urls:
  - https://www.airbnb.com.br/rooms/46397403
check_in: 2026-12-05
check_out: 2026-12-09
infants: 1
aspects:
  - name: Close to the beach
    kind: must_have
  - name: Pool
""",
        encoding="utf8",
    )
    trip = await YamlConfigSource(path).load()
    assert trip.infants == 1
    assert [a.kind for a in trip.aspects] == [AspectKind.MUST_HAVE, AspectKind.NICE_TO_HAVE]


async def test_yaml_source_reports_invalid_file(tmp_path: Path) -> None:
    path = tmp_path / "trip.yaml"
    path.write_text("urls: []\n", encoding="utf8")
    with pytest.raises(ConfigError):
        await YamlConfigSource(path).load()


async def test_yaml_source_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        await YamlConfigSource(tmp_path / "nope.yaml").load()


def test_notes_default_to_empty() -> None:
    assert make_trip().notes == {}


async def test_yaml_source_loads_notes(tmp_path: Path) -> None:
    path = tmp_path / "trip.yaml"
    path.write_text(
        """
urls:
  - https://www.airbnb.com.br/rooms/1
check_in: 2026-12-05
check_out: 2026-12-09
notes:
  https://www.airbnb.com.br/rooms/1: A praia fica a 2 minutos, vimos nas fotos.
  "2": Quartos sem ar-condicionado.
""",
        encoding="utf8",
    )
    trip = await YamlConfigSource(path).load()
    assert trip.notes == {
        "https://www.airbnb.com.br/rooms/1": "A praia fica a 2 minutos, vimos nas fotos.",
        "2": "Quartos sem ar-condicionado.",
    }
