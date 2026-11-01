from datetime import date

import pytest
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.messages import ModelMessage

from casa_certa.ai.prompts import must_haves, screen_hash, screen_instructions, screen_prompt
from casa_certa.ai.schemas import ScreenOutput, ScreenVerdictOut
from casa_certa.ai.screener import HouseScreener
from casa_certa.config import Aspect, AspectKind, TripConfig
from casa_certa.enums import ScreenStatus
from casa_certa.sources.airbnb_page import ListingFacts
from tests.test_agents import reply_in_sequence, reply_with


def make_trip(*aspects: Aspect) -> TripConfig:
    return TripConfig(
        urls=["x"], check_in=date(2026, 12, 5), check_out=date(2026, 12, 9), min_people=9, max_people=13,
        aspects=list(aspects),
    )  # fmt: skip


BEACH = Aspect(name="Perto da praia", kind=AspectKind.MUST_HAVE, details="até 500 m a pé")
POOL = Aspect(name="Piscina", kind=AspectKind.NICE_TO_HAVE)
TRIP = make_trip(BEACH, POOL)


def output(status: ScreenStatus, name: str = "Perto da praia") -> str:
    return ScreenOutput(verdicts=[ScreenVerdictOut(aspect=name, status=status, reason="r", quote="")]).model_dump_json()


def test_prompt_has_listing_text_and_must_have_details_but_no_reviews(matinhos_facts: ListingFacts) -> None:
    prompt = screen_prompt(matinhos_facts, TRIP)

    assert matinhos_facts.title and matinhos_facts.title in prompt
    assert matinhos_facts.description and matinhos_facts.description.split()[0] in prompt
    assert "Perto da praia: até 500 m a pé" in prompt
    assert "## Guest reviews" not in prompt
    assert [line for line in prompt.splitlines() if line.startswith("## ")] == [
        "## Listing",
        "## Description",
        "## Highlights",
        "## Amenities available",
        "## Amenities NOT available",
        "## MUST_HAVE requirements to judge",
    ]


def test_nice_to_haves_are_not_asked(matinhos_facts: ListingFacts) -> None:
    prompt = screen_prompt(matinhos_facts, TRIP)

    assert "## MUST_HAVE requirements" in prompt
    assert "Piscina" not in prompt.split("## MUST_HAVE requirements")[1]


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([ScreenStatus.MEETS], True),
        ([ScreenStatus.UNCLEAR], True),
        ([ScreenStatus.FAILS], False),
        ([ScreenStatus.MEETS, ScreenStatus.FAILS], False),
    ],
)
def test_passed_is_false_only_when_a_must_have_fails(statuses, expected) -> None:
    verdicts = [ScreenVerdictOut(aspect=str(i), status=s, reason="r") for i, s in enumerate(statuses)]

    assert ScreenOutput(verdicts=verdicts).passed is expected


async def test_a_verdict_for_an_unrequested_aspect_is_retried(matinhos_facts: ListingFacts) -> None:
    seen: list[list[ModelMessage]] = []
    screener = HouseScreener(reply_in_sequence([output(ScreenStatus.MEETS, "Outra"), output(ScreenStatus.MEETS)], seen))

    result = await screener.screen(matinhos_facts, TRIP)

    assert [v.aspect for v in result.verdicts] == ["Perto da praia"]
    assert len(seen) == 2


async def test_a_missing_verdict_is_retried(matinhos_facts: ListingFacts) -> None:
    seen: list[list[ModelMessage]] = []
    empty = ScreenOutput(verdicts=[]).model_dump_json()
    screener = HouseScreener(reply_in_sequence([empty, output(ScreenStatus.UNCLEAR)], seen))

    result = await screener.screen(matinhos_facts, TRIP)

    assert result.verdicts[0].status is ScreenStatus.UNCLEAR
    assert len(seen) == 2


@pytest.mark.parametrize("reply", [output(ScreenStatus.MEETS, "Outra"), ScreenOutput(verdicts=[]).model_dump_json()])
async def test_invalid_replies_forever_raise(reply: str, matinhos_facts: ListingFacts) -> None:
    screener = HouseScreener(reply_with(reply, []))

    with pytest.raises(AgentRunError):
        await screener.screen(matinhos_facts, TRIP)


def test_hash_changes_with_must_have_details_not_with_nice_to_haves() -> None:
    with_nice = make_trip(BEACH, POOL)
    without_nice = make_trip(BEACH)
    base = screen_hash(must_haves(without_nice))

    assert screen_hash(must_haves(with_nice)) == base
    assert screen_hash([Aspect(name=BEACH.name, kind=BEACH.kind, details="1 km")]) != base


def test_hash_ignores_group_size_and_language() -> None:
    changed = TRIP.model_copy(update={"min_people": 2, "max_people": 4, "language": "en"})

    assert screen_hash(must_haves(changed)) == screen_hash(must_haves(TRIP))


def test_instructions_say_fails_only_when_the_listing_clearly_contradicts() -> None:
    text = screen_instructions(TRIP)

    assert 'Use "fails" only when the listing clearly contradicts' in text
    assert 'Use "unclear" when the listing is silent or vague' in text
    assert TRIP.language in text
