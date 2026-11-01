"""Enumerations shared by the database models and the AI output schemas."""

from enum import StrEnum


class AspectStatus(StrEnum):
    MET = "met"
    PARTIAL = "partial"
    UNMET = "unmet"
    UNKNOWN = "unknown"


class EvidenceSource(StrEnum):
    LISTING = "listing"
    REVIEW = "review"
    EXTRA = "extra"


class RuleKind(StrEnum):
    RULE = "rule"
    SAFETY = "safety"


class PointKind(StrEnum):
    PRO = "pro"
    CON = "con"


class ScreenStatus(StrEnum):
    MEETS = "meets"
    UNCLEAR = "unclear"
    FAILS = "fails"
