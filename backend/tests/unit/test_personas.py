"""Persona catalogue + audience resolution."""
from __future__ import annotations

import pytest

from domain.persona import render_persona_block
from orchestration.audience import AudienceResolver, AudienceUnresolved
from personas import all_personas, get_persona, load_catalogue

pytestmark = pytest.mark.unit


def test_catalogue_validates_and_has_unique_ids():
    catalogue = load_catalogue()
    assert len(catalogue.personas) >= 16
    ids = [p.id for p in catalogue.personas]
    assert len(ids) == len(set(ids))


def test_every_persona_renders_a_prompt_block():
    from domain.persona import AudienceResolution, SyntheticCustomer

    for persona in all_personas():
        customer = SyntheticCustomer(
            persona=persona,
            source="catalogue",
            resolution=AudienceResolution(
                audience=persona.label, method="alias_match",
                matched_id=persona.id, match_confidence=1.0,
            ),
        )
        block = render_persona_block(customer)
        assert persona.label in block
        # The calibration disclaimer must survive any catalogue edit — without
        # it models treat trait levels as measurements.
        assert "simulation parameters, not measurements" in block


class FakeAgent:
    """Stands in for the LLM resolver."""

    def __init__(self, persona=None, adopt=None, error=None):
        self.persona, self.adopt, self.error = persona, adopt, error
        self.calls: list[str] = []

    async def resolve(self, audience, catalogue):
        self.calls.append(audience)
        if self.error:
            raise self.error
        if self.adopt:
            match = next(p for p in catalogue if p.id == self.adopt)
            return match, match.id
        return self.persona, None


class FakeCache:
    def __init__(self):
        self.store: dict = {}
        self.reads: list[str] = []

    async def get(self, key):
        self.reads.append(key)
        return self.store.get(key)

    async def put(self, key, audience, persona, source, adopted_from):
        self.store[key] = (persona, source, adopted_from)


def _crafted():
    base = get_persona("recruiter")
    return base.model_copy(
        update={"id": "veterinary_practice_managers", "label": "Vet practice manager"}
    )


async def test_anything_short_of_an_exact_name_goes_to_the_agent():
    """No fuzzy matching anywhere. "SOC manager" shares words with several
    catalogue entries and must still be decided by the agent."""
    agent = FakeAgent(persona=_crafted())
    customer = await AudienceResolver(agent).resolve("SOC manager")

    assert agent.calls == ["SOC manager"]
    assert customer.source == "generated"


@pytest.mark.parametrize(
    ("audience", "expected"),
    [("Recruiter", "recruiter"), ("it recruiter", "recruiter"),
     ("  FREELANCE  DESIGNER! ", "freelancer"), ("Startup founders", "startup_founder")],
)
async def test_an_exact_name_needs_no_model(audience, expected):
    """Literal identity with a persona's label or alias is a lookup, not a
    guess — it cannot mismatch, and it keeps the catalogue usable when the
    model is rate-limited."""
    agent = FakeAgent(error=AssertionError("must not be called"))
    customer = await AudienceResolver(agent).resolve(audience)

    assert customer.persona.id == expected
    assert agent.calls == []
    assert customer.resolution.method == "alias_match"


async def test_a_near_miss_is_not_treated_as_an_exact_name():
    """"IT Recruiters for fintech" is not the recruiter alias; only identity counts."""
    agent = FakeAgent(persona=_crafted())
    await AudienceResolver(agent).resolve("IT Recruiters for fintech startups")
    assert agent.calls == ["IT Recruiters for fintech startups"]


async def test_adopting_uses_the_curated_entry_not_a_paraphrase():
    """Catalogue personas are hand-written; the model's restatement of one is
    strictly worse, so adoption returns the real thing."""
    agent = FakeAgent(adopt="freelancer")
    customer = await AudienceResolver(agent).resolve("solo consultants")

    curated = get_persona("freelancer")
    assert customer.persona.model_dump() == curated.model_dump()
    assert customer.resolution.matched_id == "freelancer"


async def test_an_uncovered_audience_gets_a_written_persona():
    agent = FakeAgent(persona=_crafted())
    customer = await AudienceResolver(agent).resolve("veterinary practice managers")

    assert customer.source == "generated"
    assert customer.resolution.method == "llm_generated"
    assert customer.persona.label == "Vet practice manager"


async def test_a_failed_resolution_raises_instead_of_guessing():
    """The whole point. A wrong persona produces confidently wrong research,
    and nothing downstream can detect it."""
    agent = FakeAgent(error=RuntimeError("rate limited"))
    with pytest.raises(AudienceUnresolved, match="rate limited"):
        await AudienceResolver(agent).resolve("veterinary practice managers")


async def test_no_agent_and_no_cache_is_an_error_not_a_fallback():
    with pytest.raises(AudienceUnresolved, match="no persona-resolver"):
        await AudienceResolver().resolve("anything")


async def test_a_repeat_audience_never_asks_the_agent_twice():
    agent, cache = FakeAgent(persona=_crafted()), FakeCache()
    resolver = AudienceResolver(agent, cache=cache)

    first = await resolver.resolve("veterinary practice managers")
    second = await resolver.resolve("Veterinary Practice Managers!")

    # Same words, same person — which is what keeps a study's segments
    # reproducible across re-runs.
    assert len(agent.calls) == 1
    assert first.persona.id == second.persona.id
    assert "reused" in (second.resolution.notes or "")


async def test_cache_hit_works_without_any_agent():
    cache = FakeCache()
    # key must be the canonical form the resolver will compute
    await cache.put("vet", "vets", _crafted(), "generated", None)
    customer = await AudienceResolver(cache=cache).resolve("vets")
    assert customer.persona.id == "veterinary_practice_managers"


async def test_explicit_persona_id_skips_the_agent_entirely():
    agent = FakeAgent(error=AssertionError("must not be called"))
    customer = await AudienceResolver(agent).resolve("anything", persona_id="designer")
    assert customer.persona.id == "designer"
    assert customer.resolution.method == "explicit_id"
    assert agent.calls == []


async def test_unknown_persona_id_is_an_error():
    with pytest.raises(ValueError, match="unknown persona"):
        await AudienceResolver().resolve("x", persona_id="nope")


async def test_overrides_are_applied_and_flagged():
    agent = FakeAgent(adopt="freelancer")
    customer = await AudienceResolver(agent).resolve(
        "freelancers",
        overrides={"traits": {"patience": "high"}, "motivation": "Testing overrides."},
    )
    assert customer.persona.traits.patience.value == "high"
    assert customer.persona.motivation == "Testing overrides."
    assert customer.source == "customized"
    # Untouched traits survive the merge.
    assert customer.persona.traits.price_sensitivity.value == "high"


async def test_an_adopted_id_that_does_not_exist_falls_back_to_the_written_one():
    class BadAgent:
        async def resolve(self, audience, catalogue):
            return _crafted(), "no_such_persona"

    customer = await AudienceResolver(BadAgent()).resolve("vets")
    assert customer.persona.id == "veterinary_practice_managers"
    assert customer.source == "generated"


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("IT Recruiter", "it recruiter"),
        ("  Freelance  Designers! ", "freelance designer"),
        # Plural and singular are the same audience, so the same cache entry —
        # otherwise one audience yields two different people.
        ("SOC managers", "soc manager"),
        ("SOC manager", "soc manager"),
    ],
)
def test_normalise_is_the_cache_key(raw, key):
    from orchestration.audience import normalise

    assert normalise(raw) == key


def test_get_persona_roundtrip():
    assert get_persona("designer") is not None
    assert get_persona("does_not_exist") is None
