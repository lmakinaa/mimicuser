"""Persona catalogue, CRUD, and audience-resolution preview.

Built-ins come from the JSON catalogue and are never written to. Authoring or
editing writes a row to `personas`, which layers over the catalogue — so editing
a built-in is an override that can be reverted, not a destructive change to a
file in the source tree.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from app.api.v1.public_mode import OPERATOR_ONLY
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import (
    PersonaEntryResponse,
    PersonaListResponse,
    PersonaWriteRequest,
    ResolveAudienceRequest,
    ResolveAudienceResponse,
)
from app.core.config import Settings
from app.core.runtime_settings import get_runtime_settings
from app.db.session import get_session
from domain.persona import Persona
from orchestration.audience import AudienceUnresolved
from personas import get_persona, load_catalogue
from personas.store import DbPersonaStore, slugify
from services.run_executor import build_audience_resolver

router = APIRouter()


def _validated(data: dict) -> Persona:
    """Build a Persona or fail as a 422.

    Pydantic only auto-converts ValidationError for the *request* model. A model
    validated inside the handler raises straight past FastAPI and becomes a 500,
    so an unknown trait level like "telepathic" looked like a server fault rather
    than bad input.
    """
    try:
        return Persona.model_validate(data)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


def _entry(persona: Persona, origin: str | None) -> PersonaEntryResponse:
    """`origin` is None for an untouched built-in."""
    return PersonaEntryResponse(
        persona=persona,
        origin=origin or "builtin",
        # A built-in can always be edited — doing so creates an override.
        editable=True,
        # Only an authored row can be removed; reverting an override restores
        # the built-in, deleting a custom persona removes it entirely.
        deletable=origin is not None,
    )


@router.get("/personas", response_model=PersonaListResponse)
async def list_personas(
    session: AsyncSession = Depends(get_session),
) -> PersonaListResponse:
    store = DbPersonaStore(session)
    origins = await store.origins()
    personas = sorted(await store.list(), key=lambda p: p.label.lower())
    return PersonaListResponse(
        version=load_catalogue().version,
        personas=personas,
        entries=[_entry(p, origins.get(p.id)) for p in personas],
    )


@router.get("/personas/{persona_id}", response_model=PersonaEntryResponse)
async def get_one(
    persona_id: str, session: AsyncSession = Depends(get_session)
) -> PersonaEntryResponse:
    store = DbPersonaStore(session)
    persona = await store.get(persona_id)
    if persona is None:
        raise HTTPException(404, f"No persona {persona_id!r}")
    return _entry(persona, (await store.origins()).get(persona_id))


@router.post("/personas", dependencies=OPERATOR_ONLY, response_model=PersonaEntryResponse, status_code=201)
async def create(
    body: PersonaWriteRequest, session: AsyncSession = Depends(get_session)
) -> PersonaEntryResponse:
    store = DbPersonaStore(session)
    persona_id = body.id or slugify(body.label)

    if await store.get(persona_id) is not None:
        raise HTTPException(
            409,
            f"{persona_id!r} already exists. Edit it, or choose a different id.",
        )

    persona = _validated({**body.model_dump(exclude_none=True), "id": persona_id})
    row = await store.upsert(persona)
    await session.commit()
    return _entry(persona, row.origin)


@router.patch("/personas/{persona_id}", dependencies=OPERATOR_ONLY, response_model=PersonaEntryResponse)
async def update(
    persona_id: str,
    body: PersonaWriteRequest,
    session: AsyncSession = Depends(get_session),
) -> PersonaEntryResponse:
    """Partial edit. Editing a built-in writes an override rather than the file."""
    store = DbPersonaStore(session)
    current = await store.get(persona_id)
    if current is None:
        raise HTTPException(404, f"No persona {persona_id!r}")

    patch = body.model_dump(exclude_unset=True, exclude_none=True)
    patch.pop("id", None)
    data = current.model_dump()
    # `traits` is a nested model, so a partial trait edit must merge rather than
    # replace — otherwise changing patience alone would drop the other five.
    traits = {**data["traits"], **(patch.pop("traits", None) or {})}
    data.update(patch)
    data["traits"] = traits

    persona = _validated(data)
    row = await store.upsert(persona)
    await session.commit()
    return _entry(persona, row.origin)


@router.delete("/personas/{persona_id}", dependencies=OPERATOR_ONLY, status_code=204)
async def remove(persona_id: str, session: AsyncSession = Depends(get_session)) -> None:
    store = DbPersonaStore(session)
    if not await store.delete(persona_id):
        # Nothing authored under this id. Either it does not exist, or it is an
        # untouched built-in — neither is something we can delete.
        detail = (
            f"{persona_id!r} is a built-in and cannot be deleted; edit it instead."
            if get_persona(persona_id) is not None
            else f"No persona {persona_id!r}"
        )
        raise HTTPException(404, detail)
    await session.commit()


@router.post("/personas/resolve", dependencies=OPERATOR_ONLY, response_model=ResolveAudienceResponse)
async def resolve_audience(
    body: ResolveAudienceRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> ResolveAudienceResponse:
    """Show the operator who we'd simulate, before they spend a run on it.

    Uses the same resolver as a real run, synthesis included, so the preview
    cannot disagree with what the run actually does.
    """
    resolver = await build_audience_resolver(session, settings)
    try:
        customer = await resolver.resolve(body.audience)
    except AudienceUnresolved as exc:
        # Refusing is the point: a guessed persona produces confidently wrong
        # research that nothing downstream can detect.
        raise HTTPException(503, str(exc)) from exc
    return ResolveAudienceResponse(
        persona=customer.persona,
        source=customer.source,
        method=customer.resolution.method,
        matched_id=customer.resolution.matched_id,
        match_confidence=customer.resolution.match_confidence,
        notes=customer.resolution.notes,
    )
