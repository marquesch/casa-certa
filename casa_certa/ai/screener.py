"""Cheap model that screens a listing against the must-have aspects using only the free listing text."""

from pydantic_ai import Agent, PromptedOutput, RunContext
from pydantic_ai.models import Model

from casa_certa.ai.agents import report_usage
from casa_certa.ai.aspects import canonicalise_aspects
from casa_certa.ai.prompts import must_haves, screen_instructions, screen_prompt
from casa_certa.ai.schemas import ScreenOutput
from casa_certa.config import TripConfig
from casa_certa.costs import UsageSink
from casa_certa.sources.airbnb_page import ListingFacts


class HouseScreener:
    def __init__(self, model: Model, retries: int = 2, usage_sink: UsageSink | None = None) -> None:
        self._agent: Agent[list[str], ScreenOutput] = Agent(
            model, output_type=PromptedOutput(ScreenOutput), deps_type=list[str], retries={"output": retries}
        )
        self._agent.output_validator(self._canonicalise)
        self.model_name = model.model_name
        self._usage_sink = usage_sink

    @staticmethod
    def _canonicalise(ctx: RunContext[list[str]], output: ScreenOutput) -> ScreenOutput:
        canonicalise_aspects(ctx.deps, output.verdicts)
        return output

    async def screen(self, facts: ListingFacts, trip: TripConfig) -> ScreenOutput:
        names = [aspect.name for aspect in must_haves(trip)]
        result = await self._agent.run(screen_prompt(facts, trip), instructions=screen_instructions(trip), deps=names)
        report_usage(self._usage_sink, self.model_name, result.usage)
        return result.output
