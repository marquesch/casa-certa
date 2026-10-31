"""Ordered chains of sources: houses a source cannot answer go to the next one."""

from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol, TypeVar

from casa_certa.config import TripConfig
from casa_certa.sources.apify import ApifyError, PriceData, ReviewData
from casa_certa.sources.protocols import PriceSource, ReviewSource, SourceBatch


class Named(Protocol):
    name: str


T = TypeVar("T")
S = TypeVar("S", bound=Named)


async def resolve(
    sources: Sequence[S],
    room_ids: list[str],
    kind: str,
    ask: Callable[[S, list[str]], Awaitable[SourceBatch[T]]],
    rejection: Callable[[str, T], str | None] = lambda room_id, value: None,
) -> SourceBatch[T]:
    """Ask each source only for the houses still unanswered. `rejection` returns why an answer is unusable.

    Duplicate room ids are asked once and the results follow the input order. Notes a source attaches to a house it
    answered are kept (so a chain inside a chain does not lose them); answers about other houses are ignored."""
    room_ids = list(dict.fromkeys(room_ids))
    answers: dict[str, T | ApifyError] = {}
    notes: dict[str, str] = {}
    failures: dict[str, list[tuple[str, str]]] = {}  # room id -> (source name, reason) in chain order
    pending = room_ids
    for source in sources:
        if not pending:
            break
        try:
            batch = await ask(source, pending)
        except ApifyError as error:
            batch = SourceBatch(dict.fromkeys(pending, error))
        still_pending: list[str] = []
        for room_id in pending:
            answer = batch.results.get(room_id)
            reason: str | None
            if answer is None:
                reason = "no result"
            elif isinstance(answer, ApifyError):
                reason = str(answer)
            else:
                reason = rejection(room_id, answer)
                if reason is None:
                    answers[room_id] = answer
                    parts: list[str] = []
                    if room_id in failures:
                        first_name, first_reason = failures[room_id][0]
                        parts.append(f"{kind} from {source.name} because {first_name} failed: {first_reason}")
                    if room_id in batch.notes:
                        parts.append(batch.notes[room_id])
                    if parts:
                        notes[room_id] = "; ".join(parts)
                    continue
            failures.setdefault(room_id, []).append((source.name, reason))
            still_pending.append(room_id)
        pending = still_pending
    for room_id in pending:
        history = "; ".join(f"{name}: {reason}" for name, reason in failures[room_id])
        answers[room_id] = ApifyError(f"All {kind} sources failed: {history}")
    return SourceBatch({room_id: answers[room_id] for room_id in room_ids}, notes)


def _non_empty(sources: Sequence[S]) -> list[S]:
    if not sources:
        raise ValueError("A fallback chain needs at least one source")
    return list(sources)


class FallbackPriceSource:
    def __init__(self, sources: Sequence[PriceSource]) -> None:
        self._sources = _non_empty(sources)
        self.name = "+".join(source.name for source in self._sources)

    async def fetch_prices(self, room_ids: list[str], trip: TripConfig, guests: int) -> SourceBatch[PriceData]:
        return await resolve(
            self._sources, room_ids, "price", lambda source, rooms: source.fetch_prices(rooms, trip, guests)
        )

    def estimate_usd(self, quotes: int) -> float:
        return self._sources[0].estimate_usd(quotes)


class FallbackReviewSource:
    def __init__(self, sources: Sequence[ReviewSource]) -> None:
        self._sources = _non_empty(sources)
        self.name = "+".join(source.name for source in self._sources)

    async def fetch_reviews(
        self, room_ids: list[str], trip: TripConfig, *, expect_reviews: frozenset[str] = frozenset()
    ) -> SourceBatch[list[ReviewData]]:
        def rejection(room_id: str, reviews: list[ReviewData]) -> str | None:
            if not reviews and room_id in expect_reviews:
                return "returned no reviews although the listing has some"
            return None

        return await resolve(
            self._sources,
            room_ids,
            "reviews",
            lambda source, rooms: source.fetch_reviews(rooms, trip, expect_reviews=expect_reviews),
            rejection,
        )

    def estimate_usd(self, houses: int, review_cap: int) -> float:
        return self._sources[0].estimate_usd(houses, review_cap)
