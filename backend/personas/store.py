"""Where personas come from.

The catalogue is a JSON file loaded once and frozen — which is right for
built-ins, and useless the moment an operator wants to author or adjust one. So
lookup goes through a store instead of through module-level functions.

Two implementations, layered rather than alternative:

    CatalogueStore   the 17 built-ins, read-only, always available
    DbPersonaStore   authored rows on top of the built-ins

Layering matters for availability. If the database is unreachable the catalogue
still resolves, which keeps the sixteen-odd known audiences working during an
outage instead of failing every run.
"""
from __future__ import annotations

import re
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.persona import Persona
from personas import all_personas, get_persona
from repositories.orm import PersonaRow


def slugify(label: str) -> str:
    """A persona id from a human label.

    Ids are referenced by studies and stored on runs, so they stay ASCII, lower
    case and underscore-separated — the same shape the catalogue already uses.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    return slug[:60] or "persona"


class PersonaStore(Protocol):
    async def list(self) -> list[Persona]: ...

    async def get(self, persona_id: str) -> Persona | None: ...


class CatalogueStore:
    """The built-ins. No writes — the catalogue is a data file, not a database."""

    async def list(self) -> list[Persona]:
        return all_personas()

    async def get(self, persona_id: str) -> Persona | None:
        return get_persona(persona_id)


class DbPersonaStore:
    """Authored personas layered over the built-ins."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _rows(self) -> dict[str, PersonaRow]:
        rows = (await self._session.execute(select(PersonaRow))).scalars().all()
        return {r.persona_id: r for r in rows}

    async def list(self) -> list[Persona]:
        rows = await self._rows()
        merged = {p.id: p for p in all_personas()}
        # Authored rows win on id collision — that is what "override" means.
        for pid, row in rows.items():
            merged[pid] = Persona.model_validate(row.persona)
        return list(merged.values())

    async def get(self, persona_id: str) -> Persona | None:
        row = (
            await self._session.execute(
                select(PersonaRow).where(PersonaRow.persona_id == persona_id)
            )
        ).scalar_one_or_none()
        if row is not None:
            return Persona.model_validate(row.persona)
        return get_persona(persona_id)

    # -- writes ------------------------------------------------------------

    async def upsert(self, persona: Persona) -> PersonaRow:
        """Create or replace an authored persona.

        `origin` records whether this shadows a built-in, so a delete knows
        whether to restore something or remove it outright.
        """
        origin = "override" if get_persona(persona.id) is not None else "custom"
        row = (
            await self._session.execute(
                select(PersonaRow).where(PersonaRow.persona_id == persona.id)
            )
        ).scalar_one_or_none()
        if row is None:
            row = PersonaRow(persona_id=persona.id, origin=origin)
            self._session.add(row)
        row.origin = origin
        row.persona = persona.model_dump(mode="json")
        await self._session.flush()
        return row

    async def delete(self, persona_id: str) -> bool:
        """Remove an authored persona. Returns False if there was no row.

        Deleting an override is a revert, not a deletion: the built-in behind it
        reappears from the catalogue on the next read.
        """
        result = await self._session.execute(
            delete(PersonaRow).where(PersonaRow.persona_id == persona_id)
        )
        return bool(result.rowcount)

    async def origins(self) -> dict[str, str]:
        """persona_id -> origin, for marking which entries are editable."""
        return {pid: row.origin for pid, row in (await self._rows()).items()}
