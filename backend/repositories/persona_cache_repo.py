"""Persona cache persistence. SQL only."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from domain.persona import Persona
from repositories.orm import PersonaCacheRow


class PersonaCacheRepo:
    @staticmethod
    async def get(
        session: AsyncSession, key: str, version: str
    ) -> tuple[Persona, str, str | None] | None:
        row = (
            await session.execute(
                select(PersonaCacheRow).where(
                    PersonaCacheRow.audience_key == key,
                    PersonaCacheRow.resolver_version == version,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return Persona.model_validate(row.persona), row.source, row.adopted_from

    @staticmethod
    async def put(
        session: AsyncSession,
        *,
        key: str,
        audience: str,
        version: str,
        persona: Persona,
        source: str,
        adopted_from: str | None,
    ) -> None:
        # Two runs created at once can both miss the cache and both write. This
        # has to be a real upsert, not select-then-insert: between the SELECT and
        # the INSERT the other writer commits, and the loser then raises
        # IntegrityError on uq_persona_cache_key. That error escapes the resolver's
        # own try/except and surfaces as a 500 on study creation — a double-clicked
        # submit button was enough to trigger it.
        #
        # DO NOTHING rather than DO UPDATE: whoever got there first resolved the
        # same audience at the same resolver version, so the rows are equivalent
        # and the winner's persona is the one already handed to a live run.
        await session.execute(
            pg_insert(PersonaCacheRow)
            .values(
                audience_key=key,
                audience=audience,
                resolver_version=version,
                source=source,
                adopted_from=adopted_from,
                persona=persona.model_dump(mode="json"),
            )
            .on_conflict_do_nothing(constraint="uq_persona_cache_key")
        )
