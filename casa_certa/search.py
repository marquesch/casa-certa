"""The search service: run the configured searches, store the last one, report what is new."""

import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from casa_certa.config import TripConfig
from casa_certa.costs import CostTracker
from casa_certa.db.repository import HouseRepository, SearchRepository
from casa_certa.sources.protocols import SearchSource
from casa_certa.sources.search_sources import SearchError, SearchHit, SearchQuery

log = logging.getLogger(__name__)


@dataclass
class SearchOutcome:
    hits: list[SearchHit] = field(default_factory=list)  # deduplicated by house, in search order
    new_hits: list[SearchHit] = field(default_factory=list)  # hits that are not yet a stored house
    failures: dict[str, str] = field(default_factory=dict)  # location -> message


class SearchService:
    def __init__(
        self,
        source: SearchSource,
        session_factory: async_sessionmaker[AsyncSession],
        tracker: CostTracker | None,
    ) -> None:
        self._source = source
        self._session_factory = session_factory
        self._tracker = tracker

    def plan_lines(self, trip: TripConfig) -> list[str]:
        """What a dry run prints; spends nothing."""
        lines = []
        for location, query in self._queries(trip):
            estimate = self._source.estimate_usd(query.max_results)
            lines.append(f'search "{location}": up to {query.max_results} results, ~${estimate:.2f}')
        return lines

    async def run(self, trip: TripConfig) -> SearchOutcome:
        outcome = SearchOutcome()
        found: dict[str, SearchHit] = {}
        succeeded = 0
        for location, query in self._queries(trip):
            try:
                hits = await self._search_one(location, query)
            except SearchError as error:
                log.warning('search "%s" failed: %s', location, error)
                outcome.failures[location] = str(error)
                continue
            succeeded += 1
            for hit in hits:
                found.setdefault(hit.airbnb_id, hit)
        outcome.hits = list(found.values())
        async with self._session_factory() as session:
            if succeeded:
                await SearchRepository(session).replace_last_search(trip, outcome.hits)
                await session.commit()
            houses = HouseRepository(session)
            outcome.new_hits = [hit for hit in outcome.hits if await houses.get(hit.airbnb_id) is None]
        return outcome

    def _queries(self, trip: TripConfig) -> list[tuple[str, SearchQuery]]:
        locations = trip.search.locations if trip.search else []
        return [(location, SearchQuery.for_location(trip, location)) for location in locations]

    async def _search_one(self, location: str, query: SearchQuery) -> list[SearchHit]:
        if self._tracker is None:
            hits = await self._source.search(query)
            log.info('search "%s": %d hits', location, len(hits))
            return hits
        key = f"search:{location}"
        with self._tracker.batch([key], self._source.estimate_usd(query.max_results)):
            hits = await self._source.search(query)
        cost = self._tracker.cost_of(key)
        mark = "~" if cost.estimated else ""
        log.info('search "%s": %d hits, Apify %s$%.4f', location, len(hits), mark, cost.apify_usd)
        return hits
