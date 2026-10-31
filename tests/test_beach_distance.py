import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from casa_certa.ai.prompts import analysis_instructions
from casa_certa.ai.schemas import EvidenceOut, HouseAnalysis
from casa_certa.db.repository import HouseRepository
from casa_certa.enums import EvidenceSource
from tests.fakes import make_trip, sample_analysis


def analysis_with(distance: int | None, evidence: EvidenceOut | None) -> HouseAnalysis:
    data = sample_analysis(["Perto da praia"]).model_dump()
    data.update(beach_distance_m=distance, beach_distance_evidence=evidence)
    return HouseAnalysis.model_validate(data)


QUOTE = EvidenceOut(source=EvidenceSource.REVIEW, quote="a praia fica a 300 metros")


def test_distance_defaults_to_unknown() -> None:
    analysis = sample_analysis(["Perto da praia"])
    assert analysis.beach_distance_m is None
    assert analysis.beach_distance_evidence is None


def test_a_distance_needs_its_evidence() -> None:
    with pytest.raises(ValidationError, match="beach_distance_evidence"):
        analysis_with(300, None)


def test_a_distance_cannot_be_negative() -> None:
    with pytest.raises(ValidationError):
        analysis_with(-1, QUOTE)


def test_zero_means_beachfront_and_is_valid() -> None:
    assert analysis_with(0, QUOTE).beach_distance_m == 0


def test_instructions_describe_the_distance_field() -> None:
    text = analysis_instructions(make_trip())
    assert "beach_distance_m" in text
    assert "beach_distance_evidence" in text


async def test_the_distance_and_its_evidence_are_stored(session: AsyncSession) -> None:
    repo = HouseRepository(session)
    house = await repo.add("1", "Casa")
    await repo.save_analysis(house, "hash", "model", analysis_with(300, QUOTE))
    await session.commit()

    loaded = await repo.get("1")

    assert loaded is not None and loaded.analysis is not None
    assert loaded.analysis.beach_distance_m == 300
    assert loaded.analysis.beach_distance_source is EvidenceSource.REVIEW
    assert loaded.analysis.beach_distance_quote == "a praia fica a 300 metros"


async def test_an_unknown_distance_stores_nothing(session: AsyncSession) -> None:
    repo = HouseRepository(session)
    house = await repo.add("1", "Casa")
    data = sample_analysis(["Perto da praia"]).model_dump()
    data.update(beach_distance_m=None, beach_distance_evidence=QUOTE)  # a stray quote without a number is dropped
    await repo.save_analysis(house, "hash", "model", HouseAnalysis.model_validate(data))
    await session.commit()

    loaded = await repo.get("1")

    assert loaded is not None and loaded.analysis is not None
    assert (loaded.analysis.beach_distance_m, loaded.analysis.beach_distance_source) == (None, None)
    assert loaded.analysis.beach_distance_quote is None
