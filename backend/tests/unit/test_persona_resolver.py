"""The agent that resolves an audience: adopt a catalogue entry, or write one."""
from __future__ import annotations

import pytest

from domain.persona import Persona, PersonaTraits
from orchestration.completion import INTERACTIVE_BACKOFF_S
from orchestration.persona_synthesis import (
    LLMPersonaResolver,
    PersonaDecision,
    _slug,
)
from personas import all_personas, get_persona

pytestmark = pytest.mark.unit


class StubClient:
    def __init__(self, decision: PersonaDecision) -> None:
        self.decision = decision
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.decision


def _written(**kw) -> PersonaDecision:
    base = dict(
        adopt_catalogue_id=None,
        reasoning="nothing in the catalogue is a vet practice manager",
        label="Veterinary practice manager",
        summary="Runs the business side of a small animal clinic.",
        background="b", motivation="m",
        goals=["g"], typical_objections=["o"], preferences=["p"],
        vocabulary=["v"], context_notes=["c"], expected_behavior=["e"],
        traits=PersonaTraits(
            technical_ability="low", domain_knowledge="high", patience="medium",
            price_sensitivity="high", risk_tolerance="low",
            exploration_style="methodical",
        ),
    )
    base.update(kw)
    return PersonaDecision(**base)


async def test_adoption_returns_the_catalogue_persona_itself():
    client = StubClient(_written(adopt_catalogue_id="recruiter"))
    persona, adopted = await LLMPersonaResolver(client).resolve(
        "IT Recruiter", all_personas()
    )
    assert adopted == "recruiter"
    assert persona.model_dump() == get_persona("recruiter").model_dump()


async def test_a_written_persona_gets_an_id_derived_from_the_audience():
    """Two audiences must never collide on one identifier, and the model
    happily echoes an example's id if allowed to."""
    client = StubClient(_written())
    persona, adopted = await LLMPersonaResolver(client).resolve(
        "veterinary practice managers", all_personas()
    )
    assert adopted is None
    assert persona.id == "veterinary_practice_managers"
    assert persona.label == "Veterinary practice manager"
    assert isinstance(persona, Persona)


async def test_the_whole_catalogue_is_offered_for_adoption():
    client = StubClient(_written())
    await LLMPersonaResolver(client).resolve("vets", all_personas())
    user = client.calls[0]["user"]
    for persona in all_personas():
        assert persona.id in user, f"{persona.id} was not offered"


async def test_resolution_fails_fast_so_it_cannot_hang_a_form_submit():
    client = StubClient(_written())
    await LLMPersonaResolver(client).resolve("vets", all_personas())
    assert client.calls[0]["backoff"] == INTERACTIVE_BACKOFF_S


async def test_an_unknown_adopted_id_falls_through_to_the_written_persona():
    client = StubClient(_written(adopt_catalogue_id="not_a_real_id"))
    persona, adopted = await LLMPersonaResolver(client).resolve("vets", all_personas())
    assert adopted is None
    assert persona.label == "Veterinary practice manager"


async def test_neither_adopting_nor_writing_is_an_error():
    """Better to fail than to hand back an empty persona."""
    client = StubClient(
        PersonaDecision(adopt_catalogue_id=None, reasoning="shrug", traits=None)
    )
    with pytest.raises(ValueError, match="neither adopted"):
        await LLMPersonaResolver(client).resolve("vets", all_personas())


@pytest.mark.parametrize(
    ("audience", "expected"),
    [("IT Recruiter", "it_recruiter"), ("  Series B RevOps lead ", "series_b_revops_lead"),
     ("!!!", "custom_audience")],
)
def test_slug(audience, expected):
    assert _slug(audience) == expected


def test_decision_schema_is_usable_as_a_structured_output():
    schema = PersonaDecision.model_json_schema()
    assert schema["type"] == "object"
    assert "adopt_catalogue_id" in schema["properties"]


@pytest.mark.parametrize(
    ("model", "sends_thinking"),
    [("claude-opus-5", True), ("claude-sonnet-5", True),
     ("claude-haiku-4-5", False), ("claude-sonnet-4-5", False)],
)
def test_reasoning_params_are_only_sent_to_models_that_accept_them(model, sends_thinking):
    """Haiku 4.5 rejects adaptive thinking and `effort` with a 400, which made
    the cheap resolver model unusable."""
    from orchestration.completion import AnthropicCompletionClient

    client = AnthropicCompletionClient(
        credential_kind="api_key", secret="x", model=model
    )
    params = client._reasoning_params()
    assert bool(params) is sends_thinking
    if sends_thinking:
        assert params["thinking"] == {"type": "adaptive"}
