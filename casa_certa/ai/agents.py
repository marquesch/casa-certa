"""DeepSeek agents built on Pydantic AI: a namer and an analyst. Each takes its model, so tiers are a setting."""

from pydantic_ai import Agent, PromptedOutput, RunContext
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider

from casa_certa.ai.aspects import canonicalise_aspects
from casa_certa.ai.prompts import naming_instructions, naming_prompt
from casa_certa.ai.schemas import HouseAnalysis, HouseName
from casa_certa.sources.airbnb_page import ListingFacts


def make_deepseek_model(api_key: str, model_name: str) -> OpenAIChatModel:
    return OpenAIChatModel(model_name, provider=DeepSeekProvider(api_key=api_key))


def make_unique_name(name: str, taken: set[str]) -> str:
    """'Casa da Piscina' -> 'Casa da Piscina 2' (3, 4...) when the name is already used (case-insensitive)."""
    used = {existing.casefold() for existing in taken}
    if name.casefold() not in used:
        return name
    number = 2
    while f"{name} {number}".casefold() in used:
        number += 1
    return f"{name} {number}"


class HouseNamer:
    """Cheap model: suggests a short descriptive name for a new house."""

    def __init__(self, model: Model) -> None:
        self._agent: Agent[None, HouseName] = Agent(model, output_type=PromptedOutput(HouseName))

    async def name(self, facts: ListingFacts, language: str) -> str:
        result = await self._agent.run(naming_prompt(facts), instructions=naming_instructions(language))
        return result.output.name.strip()


class HouseAnalyst:
    """Judges a house against the aspects using the listing, reviews and notes. On the cheap model today."""

    def __init__(self, model: Model, retries: int = 2) -> None:
        self._agent: Agent[list[str], HouseAnalysis] = Agent(
            model, output_type=PromptedOutput(HouseAnalysis), deps_type=list[str], retries={"output": retries}
        )
        self._agent.output_validator(self._canonicalise_aspects)
        self.model_name = model.model_name

    @staticmethod
    def _canonicalise_aspects(ctx: RunContext[list[str]], analysis: HouseAnalysis) -> HouseAnalysis:
        canonicalise_aspects(ctx.deps, analysis.aspects)
        return analysis

    async def analyze(self, prompt: str, instructions: str, aspect_names: list[str]) -> HouseAnalysis:
        result = await self._agent.run(prompt, instructions=instructions, deps=aspect_names)
        return result.output
