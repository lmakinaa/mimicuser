"""Persona schema — the simulation parameters for one synthetic customer.

These attributes are **not** claimed to be scientifically calibrated. They are
knobs that steer a simulation, and the persona carries that disclaimer into its
own prompt block (see `render_persona_block`) so the model does not over-trust
them.

Traits are named enumerations rather than floats on purpose. A `patience: 0.34`
invites arithmetic that means nothing; `patience: low` renders cleanly into a
prompt and is honestly comparable across runs.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TechnicalAbility(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    expert = "expert"


class DomainKnowledge(StrEnum):
    none = "none"
    low = "low"
    medium = "medium"
    high = "high"


class Patience(StrEnum):
    very_low = "very_low"
    low = "low"
    medium = "medium"
    high = "high"


class PriceSensitivity(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    very_high = "very_high"


class RiskTolerance(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class ExplorationStyle(StrEnum):
    goal_directed = "goal_directed"
    exploratory = "exploratory"
    skimming = "skimming"
    methodical = "methodical"


class PersonaTraits(BaseModel):
    model_config = ConfigDict(frozen=True)

    technical_ability: TechnicalAbility
    domain_knowledge: DomainKnowledge
    patience: Patience
    price_sensitivity: PriceSensitivity
    risk_tolerance: RiskTolerance
    exploration_style: ExplorationStyle


class Persona(BaseModel):
    """One catalogue entry, or a synthesised equivalent."""

    model_config = ConfigDict(frozen=True)

    id: str
    version: int = 1
    label: str
    aliases: list[str] = Field(default_factory=list)
    summary: str

    background: str
    goals: list[str]
    motivation: str
    typical_objections: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    vocabulary: list[str] = Field(default_factory=list)
    context_notes: list[str] = Field(default_factory=list)
    expected_behavior: list[str] = Field(default_factory=list)

    traits: PersonaTraits


PersonaSource = Literal["catalogue", "customized", "generated"]
ResolutionMethod = Literal["alias_match", "token_overlap", "llm_generated", "explicit_id"]


class AudienceResolution(BaseModel):
    """How we got from a free-text audience string to a persona.

    Frozen onto the run so a report can always answer "who did you actually
    simulate, and why did you think that matched what I asked for?".
    """

    model_config = ConfigDict(frozen=True)

    audience: str
    method: ResolutionMethod
    matched_id: str | None = None
    match_confidence: float = Field(ge=0.0, le=1.0)
    overrides: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None


class SyntheticCustomer(BaseModel):
    """The resolved, frozen customer for one run."""

    model_config = ConfigDict(frozen=True)

    persona: Persona
    source: PersonaSource
    resolution: AudienceResolution


_TRAIT_GLOSS: dict[str, dict[str, str]] = {
    "technical_ability": {
        "low": "struggles with anything beyond the obvious path; avoids settings",
        "medium": "comfortable with normal software; will not read docs",
        "high": "fluent; happy to poke at advanced options",
        "expert": "expects power features and API/keyboard access",
    },
    "domain_knowledge": {
        "none": "does not know this product category's vocabulary at all",
        "low": "recognises a few terms, misreads most jargon",
        "medium": "knows the common terms, not the vendor-specific ones",
        "high": "knows the category well and compares against alternatives",
    },
    "patience": {
        "very_low": "abandons after one dead end",
        "low": "gives a confusing step maybe two tries before losing interest",
        "medium": "will retry and look for another route before giving up",
        "high": "willing to work through friction if the goal seems worth it",
    },
    "price_sensitivity": {
        "low": "price is barely a factor",
        "medium": "wants to understand the price but will pay for value",
        "high": "checks pricing early and reacts strongly to it",
        "very_high": "cost is the dominant consideration; free tier or nothing",
    },
    "risk_tolerance": {
        "low": "wary of signing up, entering data, or connecting accounts",
        "medium": "will sign up if the product seems credible",
        "high": "happy to try things and undo later",
    },
    "exploration_style": {
        "goal_directed": "beelines for the thing they came for",
        "exploratory": "wanders, clicks things that look interesting",
        "skimming": "reads headings only, scrolls fast",
        "methodical": "works through the page in order before acting",
    },
}


def render_persona_block(customer: SyntheticCustomer) -> str:
    """Render the persona as a prompt block.

    Deliberately ends with the calibration disclaimer: without it, models treat
    trait levels as measurements and produce implausibly rigid behaviour
    ("my patience is low, therefore I leave now").
    """
    p = customer.persona
    t = p.traits

    def bullets(items: list[str]) -> str:
        return "\n".join(f"  - {i}" for i in items) if items else "  - (none recorded)"

    traits_rendered = "\n".join(
        f"  - {name.replace('_', ' ')}: {value} — {_TRAIT_GLOSS[name][value]}"
        for name, value in (
            ("technical_ability", t.technical_ability.value),
            ("domain_knowledge", t.domain_knowledge.value),
            ("patience", t.patience.value),
            ("price_sensitivity", t.price_sensitivity.value),
            ("risk_tolerance", t.risk_tolerance.value),
            ("exploration_style", t.exploration_style.value),
        )
    )

    return f"""# Who you are

You are a {p.label}. {p.summary}

## Background
{p.background}

## What you are trying to get out of your working life
{p.motivation}

## Your goals
{bullets(p.goals)}

## What usually makes you hesitate
{bullets(p.typical_objections)}

## What you like
{bullets(p.preferences)}

## Words that are natural to you
{", ".join(p.vocabulary) if p.vocabulary else "(no particular jargon)"}

## Your situation right now
{bullets(p.context_notes)}

## How you tend to behave with new software
{traits_rendered}

{bullets(p.expected_behavior)}

These traits are simulation parameters, not measurements of a real person. Treat
them as a centre of gravity, not a rulebook — a real {p.label} is inconsistent,
changes their mind, and occasionally does the opposite of what you'd predict."""
