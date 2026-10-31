import re

from sqlalchemy.ext.asyncio import AsyncSession

from casa_certa.ai.prompts import analysis_instructions, analysis_prompt, naming_prompt, prompt_hash
from casa_certa.db.models import House
from casa_certa.db.repository import HouseRepository
from casa_certa.sources.airbnb_page import ListingFacts
from tests.fakes import make_trip


def test_instructions_state_the_override_rule_and_language() -> None:
    text = analysis_instructions(make_trip(language="pt-BR"))
    assert "override" in text
    assert "pt-BR" in text
    assert "9 to 13" in text


def test_analysis_prompt_contains_facts_reviews_extra_and_aspects(stored_house: House) -> None:
    prompt = analysis_prompt(stored_house, make_trip())

    assert "100 metros do mar" in prompt  # listing description
    assert "NOT available" in prompt
    assert "Ar-condicionado" in prompt
    assert "Adoramos a estadia" in prompt  # a review
    assert "3 minutos a pé" in prompt  # the family's extra note
    assert "[MUST_HAVE] Perto da praia" in prompt
    assert "[NICE_TO_HAVE] Piscina" in prompt
    assert stored_house.name not in prompt  # renaming a house must not invalidate its analysis


def _review_lines(prompt: str) -> list[str]:
    return [line for line in prompt.splitlines() if re.match(r"- \[\d{4}-\d\d-\d\d,", line)]


def test_analysis_prompt_respects_the_review_cap(stored_house: House) -> None:
    prompt = analysis_prompt(stored_house, make_trip(review_cap=1))
    assert len(_review_lines(prompt)) == 1


def test_analysis_prompt_without_reviews_says_so(stored_house: House) -> None:
    prompt = analysis_prompt(stored_house, make_trip(review_cap=0))
    assert "## Guest reviews (none included)" in prompt
    assert "most recent 0" not in prompt
    assert "Reviews shown span" not in prompt


def test_reviews_are_listed_newest_first_with_a_date_anchor(stored_house: House) -> None:
    prompt = analysis_prompt(stored_house, make_trip())
    dates = [line[3:13] for line in _review_lines(prompt)]
    assert len(dates) > 1
    assert dates == sorted(dates, reverse=True)
    assert f"Reviews shown span {dates[-1]} to {dates[0]}; treat the newest date as roughly the present" in prompt


def test_review_without_rating_is_rendered_as_no_rating(stored_house: House) -> None:
    newest = max(stored_house.reviews, key=lambda review: review.created_at)
    newest.rating = None
    prompt = analysis_prompt(stored_house, make_trip())
    assert f"[{newest.created_at:%Y-%m-%d}, no rating]" in prompt


def test_prompt_never_contains_none(stored_house: House) -> None:
    assert "None" not in analysis_prompt(stored_house, make_trip())


async def test_prompt_for_a_house_with_null_facts_has_no_none(session: AsyncSession) -> None:
    repo = HouseRepository(session)
    house = await repo.add("1", "Casa vazia")
    await repo.save_facts(house, ListingFacts(listing_id="1"))
    await session.commit()

    prompt = analysis_prompt(house, make_trip())

    assert "None" not in prompt
    assert "Title: unknown" in prompt
    assert "Category ratings: unknown" in prompt
    assert "## Guest reviews (none included)" in prompt
    assert "Reviews shown span" not in prompt
    for header in ("## House rules and safety", "## Highlights"):
        assert prompt.split(header + "\n")[1].startswith("(none)")


def test_zero_category_rating_is_kept(stored_house: House) -> None:
    assert stored_house.facts is not None
    stored_house.facts.rating_value = 0.0
    assert "value 0.0" in analysis_prompt(stored_house, make_trip())


def test_instructions_explain_dates_unknown_amenities_and_quotes() -> None:
    text = analysis_instructions(make_trip())
    assert "relative to the newest review" in text
    assert "neither the available nor the NOT available list is unknown" in text
    assert "Quote them verbatim in their original language" in text


def test_prompt_hash_is_stable(stored_house: House) -> None:
    trip = make_trip()
    first = prompt_hash(analysis_instructions(trip), analysis_prompt(stored_house, trip))
    second = prompt_hash(analysis_instructions(trip), analysis_prompt(stored_house, trip))
    assert first == second


def test_prompt_hash_changes_with_cap_language_and_aspects(stored_house: House) -> None:
    def hash_for(**overrides) -> str:
        trip = make_trip(**overrides)
        return prompt_hash(analysis_instructions(trip), analysis_prompt(stored_house, trip))

    base = hash_for()
    assert hash_for(review_cap=1) != base
    assert hash_for(language="en") != base
    assert hash_for(aspects=[{"name": "Churrasqueira", "kind": "must_have"}]) != base


def test_prompt_hash_changes_with_the_extra_note(stored_house: House) -> None:
    trip = make_trip()
    before = prompt_hash(analysis_instructions(trip), analysis_prompt(stored_house, trip))
    stored_house.extra = "Nova informação"
    after = prompt_hash(analysis_instructions(trip), analysis_prompt(stored_house, trip))
    assert before != after
    assert len(before) == 64


def test_naming_prompt_mentions_title_and_place(matinhos_facts: ListingFacts) -> None:
    prompt = naming_prompt(matinhos_facts)
    assert "Albatroz" in prompt
    assert "Matinhos" in prompt
