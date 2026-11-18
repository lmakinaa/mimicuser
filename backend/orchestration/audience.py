"""Audience → SyntheticCustomer.

**An agent resolves every audience it is asked about.** The catalogue is
reference material the agent may adopt, not a table to be fuzzily matched
against — the only lookup left is exact identity, where the audience string *is*
a persona's own name.

The previous design scored token overlap between the audience string and each
persona's label and aliases. That is a bag-of-words heuristic wearing a
confidence score: "IT Recruiter" matched `enterprise_buyer` at 50% because both
contain "IT". Gating on the head noun patched that one case and left the method
intact — a crude matcher producing a number that looks like evidence.

Bad matches are worse than no match. The whole product rests on simulating the
audience the operator actually named; a confidently wrong persona produces
confidently wrong research, and nothing downstream can detect it.

So: exact identity is a lookup, a repeat audience is a cache hit, and everything
else asks a model that can tell a recruiter from a buyer. If no persona can be
resolved, that is an error — never a silent approximation.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Protocol

from domain.persona import AudienceResolution, Persona, SyntheticCustomer
from personas.store import CatalogueStore, PersonaStore

log = logging.getLogger(__name__)


class AudienceUnresolved(RuntimeError):
    """No persona could be resolved, and guessing is not an option.

    Raised rather than falling back: running a simulation as the wrong person
    is the failure mode this whole module exists to prevent.
    """


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def normalise(audience: str) -> str:
    """Cache key. Case, spacing, punctuation and plurality are not meaningful.

    Canonicalising number is what stops "SOC manager" and "SOC managers" from
    becoming two cache entries — two model calls, and worse, potentially two
    different people for what the operator meant as one audience. This is key
    canonicalisation, not the fuzzy matching that was removed: it never decides
    *which* persona applies, only whether two strings name the same audience.
    """
    words = re.sub(r"[^a-z0-9]+", " ", audience.lower()).split()
    return " ".join(_singular(w) for w in words)


class PersonaResolverAgent(Protocol):
    """Adopts a catalogue persona or writes a new one for this audience."""

    async def resolve(self, audience: str, catalogue: list[Persona]) -> tuple[Persona, str | None]:
        """Returns (persona, adopted_catalogue_id | None). Raises on failure."""
        ...


class PersonaCache(Protocol):
    """Durable, so the same audience yields the same person across runs."""

    async def get(self, key: str) -> tuple[Persona, str, str | None] | None: ...

    async def put(
        self, key: str, audience: str, persona: Persona, source: str, adopted_from: str | None
    ) -> None: ...


class AudienceResolver:
    def __init__(
        self,
        agent: PersonaResolverAgent | None = None,
        cache: PersonaCache | None = None,
        personas: PersonaStore | None = None,
    ) -> None:
        self._agent = agent
        self._cache = cache
        # Defaults to the built-in catalogue so resolution still works with no
        # database in reach — and so every existing caller keeps its behaviour.
        self._personas: PersonaStore = personas or CatalogueStore()

    async def resolve(
        self,
        audience: str,
        *,
        persona_id: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> SyntheticCustomer:
        overrides = overrides or {}

        # 1. The operator picked one. Nothing to infer.
        if persona_id:
            persona = await self._personas.get(persona_id)
            if persona is None:
                raise ValueError(f"unknown persona id {persona_id!r}")
            return self._build(
                persona,
                AudienceResolution(
                    audience=audience,
                    method="explicit_id",
                    matched_id=persona.id,
                    match_confidence=1.0,
                    overrides=overrides,
                ),
                overrides,
            )

        key = normalise(audience)

        # 2. Seen this audience before. Same words, same person — which is what
        #    makes a study's segments reproducible across re-runs.
        if self._cache is not None:
            cached = await self._cache.get(key)
            if cached is not None:
                persona, source, adopted = cached
                log.info("persona cache hit for %r -> %s", audience, persona.id)
                return self._build(
                    persona,
                    AudienceResolution(
                        audience=audience,
                        method="alias_match" if adopted else "llm_generated",
                        matched_id=adopted or persona.id,
                        match_confidence=1.0,
                        overrides=overrides,
                        notes=f"reused a previously resolved persona for {audience!r}",
                    ),
                    overrides,
                    source=source,
                )

        # 3. The catalogue's own names, treated as pre-resolved.
        #
        #    This is exact identity — the audience string *is* a persona's label
        #    or one of its aliases — not the token-overlap scoring that was
        #    removed. A lookup cannot mismatch, it costs nothing, and it keeps
        #    the sixteen catalogue audiences working when the model is
        #    unavailable. Anything less than identity goes to the agent.
        for persona in await self._personas.list():
            names = {normalise(persona.label)} | {normalise(a) for a in persona.aliases}
            if key in names:
                if self._cache is not None:
                    await self._cache.put(key, audience, persona, "catalogue", persona.id)
                return self._build(
                    persona,
                    AudienceResolution(
                        audience=audience,
                        method="alias_match",
                        matched_id=persona.id,
                        match_confidence=1.0,
                        overrides=overrides,
                        notes=f"{audience!r} is exactly this persona's name",
                    ),
                    overrides,
                )

        # 4. Ask the agent.
        if self._agent is None:
            raise AudienceUnresolved(
                f"Cannot resolve the audience {audience!r}: no persona-resolver is "
                "configured. Link an Anthropic account, or choose a persona "
                "explicitly."
            )

        try:
            persona, adopted = await self._agent.resolve(
                audience, await self._personas.list()
            )
        except Exception as exc:  # noqa: BLE001
            raise AudienceUnresolved(
                f"Could not build a persona for {audience!r}: {exc}. Retry, or "
                "choose a persona explicitly."
            ) from exc

        # The agent may recognise the audience as one the catalogue already
        # covers. Prefer the curated entry over the model's paraphrase of it.
        if adopted:
            curated = await self._personas.get(adopted)
            if curated is not None:
                persona = curated
            else:
                log.warning("agent adopted unknown catalogue id %r; using its own", adopted)
                adopted = None

        source = "catalogue" if adopted else "generated"
        if self._cache is not None:
            await self._cache.put(key, audience, persona, source, adopted)

        return self._build(
            persona,
            AudienceResolution(
                audience=audience,
                method="alias_match" if adopted else "llm_generated",
                matched_id=adopted or persona.id,
                match_confidence=1.0,
                overrides=overrides,
                notes=(
                    f"recognised {audience!r} as the catalogue's {adopted!r}"
                    if adopted
                    else f"no catalogue persona covers {audience!r}; one was written for it"
                ),
            ),
            overrides,
            source=source,
        )

    # -- helpers -----------------------------------------------------------

    def _build(
        self,
        persona: Persona,
        resolution: AudienceResolution,
        overrides: dict[str, Any],
        *,
        source: str = "catalogue",
    ) -> SyntheticCustomer:
        return SyntheticCustomer(
            persona=self._apply_overrides(persona, overrides),
            source="customized" if overrides else source,  # type: ignore[arg-type]
            resolution=resolution,
        )

    @staticmethod
    def _apply_overrides(persona: Persona, overrides: dict[str, Any]) -> Persona:
        """Shallow merge, with `traits` merged one level deeper.

        Validation happens through the Pydantic model, so a bad override is a
        400 at run creation rather than a strange simulation.
        """
        if not overrides:
            return persona
        data = persona.model_dump()
        traits = {**data["traits"], **(overrides.get("traits") or {})}
        data.update({k: v for k, v in overrides.items() if k != "traits"})
        data["traits"] = traits
        return Persona.model_validate(data)
