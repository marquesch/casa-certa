import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from casa_certa.ai.agents import HouseAnalyst, HouseNamer, make_deepseek_model, make_unique_name
from casa_certa.sources.airbnb_page import ListingFacts
from tests.fakes import sample_analysis


def reply_with(text: str, seen: list[list[ModelMessage]]) -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(respond)


def test_make_unique_name() -> None:
    assert make_unique_name("Casa da Piscina", set()) == "Casa da Piscina"
    assert make_unique_name("Casa da Piscina", {"Casa da Piscina"}) == "Casa da Piscina 2"
    assert make_unique_name("Casa da Piscina", {"casa da piscina", "Casa da Piscina 2"}) == "Casa da Piscina 3"
    assert make_unique_name("Casa", {"Casa", "Casa 2"}) == "Casa 3"


async def test_namer_returns_a_trimmed_name_and_sends_facts_and_language(matinhos_facts: ListingFacts) -> None:
    seen: list[list[ModelMessage]] = []
    namer = HouseNamer(reply_with('{"name": "  Casa da Piscina "}', seen))

    name = await namer.name(matinhos_facts, "pt-BR")

    assert name == "Casa da Piscina"
    sent = str(seen[0])
    assert "Albatroz" in sent
    assert "pt-BR" in sent


async def test_analyst_parses_structured_output() -> None:
    expected = sample_analysis(["Perto da praia", "Piscina"])
    seen: list[list[ModelMessage]] = []
    model = reply_with(expected.model_dump_json(), seen)
    analyst = HouseAnalyst(model)

    result = await analyst.analyze("PROMPT TEXT", "INSTRUCTION TEXT", ["Perto da praia", "Piscina"])

    assert result == expected
    assert "PROMPT TEXT" in str(seen[0])
    assert "INSTRUCTION TEXT" in str(seen[0])
    assert analyst.model_name == model.model_name


def test_make_deepseek_model_uses_the_requested_model_name() -> None:
    assert make_deepseek_model("key", "deepseek-chat").model_name == "deepseek-chat"


def reply_in_sequence(texts: list[str], seen: list[list[ModelMessage]]) -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(messages)
        return ModelResponse(parts=[TextPart(texts[min(len(seen), len(texts)) - 1])])

    return FunctionModel(respond)


async def test_analyst_retries_when_an_aspect_is_reworded() -> None:
    seen: list[list[ModelMessage]] = []
    reworded = sample_analysis(["Ar condicionado", "Piscina"]).model_dump_json()
    correct = sample_analysis(["Ar-condicionado", "Piscina"]).model_dump_json()
    analyst = HouseAnalyst(reply_in_sequence([reworded, correct], seen))

    result = await analyst.analyze("P", "I", ["Ar-condicionado", "Piscina"])

    assert [v.aspect for v in result.aspects] == ["Ar-condicionado", "Piscina"]
    assert len(seen) == 2
    assert "Ar-condicionado" in str(seen[1])  # the retry message lists the expected names
    assert "Ar condicionado" in str(seen[1])  # and the received ones


async def test_analyst_canonicalises_names_that_differ_only_in_case_and_spacing() -> None:
    seen: list[list[ModelMessage]] = []
    sloppy = sample_analysis(["  perto  da PRAIA ", "piscina"]).model_dump_json()
    analyst = HouseAnalyst(reply_with(sloppy, seen))

    result = await analyst.analyze("P", "I", ["Perto da praia", "Piscina"])

    assert [v.aspect for v in result.aspects] == ["Perto da praia", "Piscina"]
    assert len(seen) == 1


async def test_analyst_gives_up_when_the_names_never_match() -> None:
    seen: list[list[ModelMessage]] = []
    analyst = HouseAnalyst(reply_with(sample_analysis(["Outra coisa"]).model_dump_json(), seen))

    with pytest.raises(UnexpectedModelBehavior):
        await analyst.analyze("P", "I", ["Piscina"])


def test_importing_the_package_silences_the_pydantic_ai_banner() -> None:
    import os

    import casa_certa  # noqa: F401

    assert "PYDANTIC_AI_NO_BANNER" in os.environ
