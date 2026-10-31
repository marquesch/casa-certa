"""Structured outputs of the language models."""

from pydantic import BaseModel, Field

from casa_certa.enums import AspectStatus, EvidenceSource


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


class HouseName(BaseModel):
    name: str = Field(description="Short descriptive house name, 2 to 5 words")
