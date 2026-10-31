"""Prompt text for the language models. Prompts are code, so they are written in English; the models are
told which language to answer in."""

import hashlib

from casa_certa.config import TripConfig
from casa_certa.db.models import House
from casa_certa.sources.airbnb_page import ListingFacts

MAX_REVIEW_CHARS = 700
MAX_DESCRIPTION_CHARS = 300


def analysis_instructions(trip: TripConfig) -> str:
    return f"""You are helping a family choose an Airbnb house for a group trip of about \
{trip.min_people} to {trip.max_people} people.

You receive the listing facts, recent guest reviews, optional notes from the family (EXTRA) and a list of aspects, \
each MUST_HAVE or NICE_TO_HAVE.

Rules:
- The listing is the baseline. Recent guest reviews can override it when they contradict or refine it (for example \
the real distance to the beach, the beach quality, or whether air conditioning really exists in every room). When a \
review contradicts the listing, say so and quote the review.
- EXTRA notes come from the family. Accept them as additional information and label them with source "extra", but \
never use them to contradict an explicit statement in the listing.
- Return exactly one verdict per requested aspect: met, partial, unmet or unknown. Use unknown when there is no \
evidence. Never guess.
- Evidence quotes must be short and verbatim from the listing, a review or the EXTRA notes. The source must be \
"listing", "review" or "extra".
- An amenity that appears in neither the available nor the NOT available list is unknown.
- Weigh the rating together with the number and age of the reviews. Few or old reviews are weaker evidence: say so \
in confidence_note. Review dates are absolute; judge their age relative to the newest review.
- Guest reviews are mostly in Portuguese. Quote them verbatim in their original language; write everything else in \
the output language.
- Pros and cons must be concrete and relevant for a group of that size.
- Write every free-text field (reason, pros, cons, summary, confidence_note) in {trip.language}. Keep JSON keys and \
enum values in English."""


def _one_line(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _show(value: object, unit: str = "") -> str:
    """Render a nullable fact for the prompt: missing as 'unknown', booleans as yes/no."""
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return f"{value}{unit}"


def analysis_prompt(house: House, trip: TripConfig) -> str:
    """Everything the analyst may use, rendered from the stored data. The house name is left out on purpose."""
    facts = house.facts
    if facts is None:
        raise ValueError("Cannot build an analysis prompt for a house without stored facts")

    category_ratings = {
        "cleanliness": facts.rating_cleanliness,
        "accuracy": facts.rating_accuracy,
        "check-in": facts.rating_checkin,
        "communication": facts.rating_communication,
        "location": facts.rating_location,
        "value": facts.rating_value,
    }
    rated = [f"{name} {value}" for name, value in category_ratings.items() if value is not None]
    lines = [
        "## Listing facts",
        f"Title: {_show(facts.title)}",
        f"Type: {_show(facts.property_type)}",
        f"Capacity: {_show(facts.person_capacity, ' guests')}, {_show(facts.bedrooms, ' bedrooms')}, "
        f"{_show(facts.beds, ' beds')}, {_show(facts.bathrooms, ' bathrooms')}",
        f"Rating: {_show(facts.rating)} from {_show(facts.review_count)} reviews "
        f"(guest favorite: {_show(facts.is_guest_favorite)})",
        "Category ratings: " + (", ".join(rated) or "unknown"),
        f"Location: {_show(facts.location_name)}",
        f"Host: {_show(facts.host_name)}, superhost: {_show(facts.host_is_superhost)}, "
        f"rating {_show(facts.host_rating)}, {_show(facts.host_years)} years hosting",
        "",
        "## Description",
        facts.description or "(none)",
        "",
        "## Rooms",
        *[f"- {room.name}: {room.beds_text}" for room in house.rooms],
        "",
        "## Amenities available",
        ", ".join(a.name for a in house.amenities if a.available) or "(none)",
        "## Amenities NOT available",
        ", ".join(a.name for a in house.amenities if not a.available) or "(none)",
        "",
        "## House rules and safety",
        *([f"- {rule.title} {rule.description}".strip() for rule in house.rules] or ["(none)"]),
        "",
        "## Highlights",
        *([f"- {h.headline}: {h.body}" for h in house.highlights] or ["(none)"]),
        "",
    ]

    reviews = sorted(house.reviews, key=lambda review: review.created_at, reverse=True)[: trip.review_cap]
    if reviews:
        lines.append(f"## Guest reviews (most recent {len(reviews)} of {_show(facts.review_count)} total)")
        lines.append(
            f"Reviews shown span {reviews[-1].created_at:%Y-%m-%d} to {reviews[0].created_at:%Y-%m-%d}; "
            "treat the newest date as roughly the present when judging how recent the evidence is."
        )
        for review in reviews:
            rating = "no rating" if review.rating is None else f"{review.rating} stars"
            lines.append(f"- [{review.created_at:%Y-%m-%d}, {rating}] {_one_line(review.text, MAX_REVIEW_CHARS)}")
    else:
        lines += ["## Guest reviews (none included)", "(none)"]
    lines += ["", "## EXTRA notes from the family", house.extra.strip() or "(none)", "", "## Aspects to judge"]
    for aspect in trip.aspects:
        detail = f": {aspect.details}" if aspect.details else ""
        lines.append(f"- [{aspect.kind.name}] {aspect.name}{detail}")
    return "\n".join(lines)


def prompt_hash(instructions: str, prompt: str) -> str:
    """Identifies the exact analysis input. A different hash means the analysis must be redone."""
    return hashlib.sha256(f"{instructions}\n\x00\n{prompt}".encode()).hexdigest()


def naming_instructions(language: str) -> str:
    return (
        f"Create a short, descriptive, memorable name (2 to 5 words) in {language} for the house below, based on "
        "its most distinctive feature or neighbourhood, for example 'Casa da Piscina' or 'Sobrado Vista Mar'. "
        "Do not include the city unless it is the distinctive part. Do not use quotes or emoji."
    )


def naming_prompt(facts: ListingFacts) -> str:
    amenities = ", ".join(facts.available_amenities()[:15])
    return "\n".join(
        [
            f"Title: {facts.title}",
            f"Type: {facts.property_type}",
            f"Place: {facts.location_name}",
            f"Main amenities: {amenities}",
            f"Description: {_one_line(facts.description or '', MAX_DESCRIPTION_CHARS)}",
        ]
    )
