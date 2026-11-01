"""Structured outputs of the language models."""

from typing import Self

from pydantic import BaseModel, Field, model_validator

from casa_certa.enums import AspectStatus, EvidenceSource, ScreenStatus


class EvidenceOut(BaseModel):
    source: EvidenceSource
    quote: str = Field(description="Short verbatim quote from the listing, a guest review or the family's notes")


class VerdictOut(BaseModel):
    aspect: str = Field(description="Exactly the aspect name given in the request")
    status: AspectStatus
    reason: str
    evidence: list[EvidenceOut] = Field(default_factory=list)


class HouseAnalysis(BaseModel):
    aspects: list[VerdictOut]
    pros: list[str]
    cons: list[str]
    summary: str = Field(description="One-line verdict on the house")
    confidence_note: str | None = Field(default=None, description="Thin or old evidence, missing reviews, etc.")
    beach_distance_m: int | None = Field(
        default=None,
        ge=0,
        description="Walking distance in meters to the nearest beach; 0 when beachfront; null when unknown",
    )
    beach_distance_evidence: EvidenceOut | None = Field(
        default=None, description="Verbatim quote the distance is based on; required when beach_distance_m is set"
    )

    @model_validator(mode="after")
    def _distance_needs_evidence(self) -> Self:
        if self.beach_distance_m is not None and self.beach_distance_evidence is None:
            raise ValueError("beach_distance_m needs beach_distance_evidence")
        return self


class HouseName(BaseModel):
    name: str = Field(description="Short descriptive house name, 2 to 5 words")


class ScreenVerdictOut(BaseModel):
    aspect: str = Field(description="Exactly the must-have name given in the request")
    status: ScreenStatus
    reason: str = Field(description="One short sentence")
    quote: str = Field(default="", description="Short verbatim quote from the listing; empty when unclear")


class ScreenOutput(BaseModel):
    verdicts: list[ScreenVerdictOut]

    @property
    def passed(self) -> bool:
        return all(verdict.status is not ScreenStatus.FAILS for verdict in self.verdicts)
