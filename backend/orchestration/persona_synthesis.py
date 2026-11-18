"""The agent that turns a target audience into a persona.

It is given the audience and the whole catalogue, and does one of two things:

- **Adopts** a catalogue persona when that persona genuinely *is* this audience.
  Curated entries are hand-written and better than anything generated on the
  spot, so recognising one is the preferred outcome.
- **Writes** a persona when nothing in the catalogue covers the audience.

This replaced token-overlap matching. Scoring shared words is a bag-of-words
heuristic dressed up as a confidence number — it matched "IT Recruiter" to
`enterprise_buyer` because both contain "IT". A model can tell a recruiter from
a buyer; a word counter cannot.
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, ConfigDict, Field

from domain.persona import Persona, PersonaTraits
from orchestration.completion import INTERACTIVE_BACKOFF_S, StructuredCompletion

log = logging.getLogger(__name__)

SYNTHESIS_PROMPT_VERSION = "persona-resolver/2"


class PersonaDecision(BaseModel):
    """One schema for both outcomes — unions are awkward as structured output."""

    model_config = ConfigDict(frozen=True)

    adopt_catalogue_id: str | None = Field(
        default=None,
        description=(
            "id of a catalogue persona that genuinely IS this audience, or null "
            "if none of them is"
        ),
    )
    reasoning: str = Field(description="one sentence: why adopt, or why not")

    # Ignored when adopting.
    label: str = ""
    summary: str = ""
    background: str = ""
    motivation: str = ""
    goals: list[str] = Field(default_factory=list)
    typical_objections: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    vocabulary: list[str] = Field(default_factory=list)
    context_notes: list[str] = Field(default_factory=list)
    expected_behavior: list[str] = Field(default_factory=list)
    traits: PersonaTraits | None = None


_SYSTEM = """
You turn a target audience into simulation parameters for a synthetic customer
who will be sent to use a product's website for the first time.

You get the audience the operator typed and the full catalogue of existing
personas. Do exactly one of two things.

# 1. Adopt a catalogue persona

Set `adopt_catalogue_id` when one of the catalogue personas genuinely *is* the
audience described. These are hand-written and better than anything you would
produce on the spot, so prefer adopting whenever it is honestly right.

Adopt on **who the person is**, not on shared words. "IT Recruiter" is a
recruiter, not an "IT manager" — the modifier is not the person. A wrong
adoption is worse than writing a new persona, because the whole study will then
simulate the wrong human being.

Adopt when the audience is the same role in different words ("solo consultant"
→ freelancer, "talent acquisition" → recruiter). Do **not** adopt when the
audience is a narrower or materially different specialism than the catalogue
entry — "veterinary practice manager" is not the generic operations manager,
and "Series B RevOps lead" is not a generic marketer.

# 2. Write a new persona

Otherwise leave `adopt_catalogue_id` null and fill in every other field for one
*specific, ordinary member* of that audience — not an archetype, not a
composite, not a spokesperson.

- **Write a person, not a segment.** "Runs recruitment for a 200-person software
  company and is measured on time-to-hire" beats "recruitment professional".
- **Ground the goals in the actual job.** What are they trying to get done this
  week? What would make them look good, or stop the phone ringing?
- **Objections must be theirs.** What would make *this* person close the tab — a
  price, a missing integration, a compliance question, a suspicion it won't
  survive contact with their team?
- **Vocabulary is how we catch terminology problems.** List words this person
  uses naturally; the gap between their words and the product's words is a
  finding.
- **expected_behavior is the most valuable field.** Concrete, observable things
  they'd do on a website: what they look for first, what they skip, where they
  stall, what makes them leave. Write them as predictions someone could check.
- **Choose traits honestly.** Do not default everything to medium; an audience
  with unusual patience or no price sensitivity is a real and useful thing to
  model.
- Do not invent a named company, a real person, or demographic detail the
  audience string does not imply.
- If the audience is vague ("users", "everyone"), write the most ordinary
  plausible reading rather than refusing.

Always give one sentence of `reasoning` for whichever route you took.
""".strip()


def _slug(audience: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", audience.lower()).strip("_")
    return (slug or "custom_audience")[:48]


class LLMPersonaResolver:
    """Implements `PersonaResolverAgent` from `orchestration.audience`."""

    def __init__(self, client: StructuredCompletion) -> None:
        self._client = client

    async def resolve(
        self, audience: str, catalogue: list[Persona]
    ) -> tuple[Persona, str | None]:
        index = "\n".join(
            f"- {p.id}: {p.label} — {p.summary} (also called: "
            f"{', '.join(p.aliases[:6]) or 'nothing else'})"
            for p in catalogue
        )
        user = (
            f"Target audience: {audience!r}\n\n"
            f"# Catalogue\n\n{index}\n\n"
            f"Adopt one of these if it genuinely is this audience. Otherwise write "
            f"the persona for one ordinary member of {audience!r}."
        )

        decision = await self._client.parse(
            system=_SYSTEM,
            user=user,
            schema=PersonaDecision,
            max_tokens=8_000,
            # Someone is waiting on a form submit; fail fast rather than holding
            # the request open through a long rate limit.
            backoff=INTERACTIVE_BACKOFF_S,
        )

        if decision.adopt_catalogue_id:
            log.info(
                "resolver adopted %s for %r: %s",
                decision.adopt_catalogue_id, audience, decision.reasoning,
            )
            match = next((p for p in catalogue if p.id == decision.adopt_catalogue_id), None)
            if match is not None:
                return match, match.id
            log.warning("resolver adopted unknown id %r", decision.adopt_catalogue_id)

        if not decision.label or decision.traits is None:
            raise ValueError(
                f"resolver neither adopted a persona nor wrote one for {audience!r}"
            )

        log.info("resolver wrote a new persona for %r: %s", audience, decision.reasoning)
        return (
            Persona(
                # Forced, not taken from the model: two audiences must never
                # collide on one identifier.
                id=_slug(audience),
                version=1,
                label=decision.label,
                aliases=[],
                summary=decision.summary,
                background=decision.background,
                goals=decision.goals,
                motivation=decision.motivation,
                typical_objections=decision.typical_objections,
                preferences=decision.preferences,
                vocabulary=decision.vocabulary,
                context_notes=decision.context_notes,
                expected_behavior=decision.expected_behavior,
                traits=decision.traits,
            ),
            None,
        )
