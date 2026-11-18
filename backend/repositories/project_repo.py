"""Project persistence. SQL only, no business logic.

Lifted out of `run_repo` now that a project is a thing the operator manages
rather than a hidden row created as a side effect of the first run.
"""
from __future__ import annotations

from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repositories.orm import ProjectRow, RunRow, StudyRow

#: Matches String(200) on ProjectRow.name. A hostname can legally reach 253.
NAME_LIMIT = 200


def derive_name(product_url: str) -> str:
    host = (urlparse(product_url).hostname or product_url).removeprefix("www.")
    return host[:NAME_LIMIT]


class ProjectRepo:
    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        name: str,
        product_url: str,
        product_description: str | None = None,
        default_audience: str | None = None,
    ) -> ProjectRow:
        row = ProjectRow(
            name=name[:NAME_LIMIT],
            product_url=product_url,
            product_description=product_description,
            default_audience=default_audience,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def get(session: AsyncSession, project_id: UUID) -> ProjectRow | None:
        return await session.get(ProjectRow, project_id)

    @staticmethod
    async def list_all(session: AsyncSession) -> list[ProjectRow]:
        return list(
            (
                await session.execute(select(ProjectRow).order_by(ProjectRow.created_at.desc()))
            ).scalars()
        )

    @staticmethod
    async def find_by_url(session: AsyncSession, product_url: str) -> ProjectRow | None:
        return (
            await session.execute(
                select(ProjectRow).where(ProjectRow.product_url == product_url).limit(1)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_or_create(
        session: AsyncSession,
        *,
        product_url: str,
        project_id: UUID | None = None,
        default_audience: str | None = None,
    ) -> ProjectRow:
        """Resolve the project a run or study belongs to.

        The brief's minimum input is URL + audience, so a project is created
        implicitly rather than demanded up front.

        This existed twice — once in the runs endpoint, once in `create_study` —
        and the two disagreed: an unknown `project_id` was a 404 in one and a
        silent fallback to a *different* project in the other. Explicitly naming
        a project that does not exist is a mistake worth reporting, so the
        caller now always gets `LookupError` for that case.
        """
        if project_id is not None:
            project = await ProjectRepo.get(session, project_id)
            if project is None:
                raise LookupError(f"No project {project_id}")
            return project

        found = await ProjectRepo.find_by_url(session, product_url)
        if found is not None:
            return found
        return await ProjectRepo.create(
            session,
            name=derive_name(product_url),
            product_url=product_url,
            default_audience=default_audience,
        )

    @staticmethod
    async def update(session: AsyncSession, project_id: UUID, patch: dict) -> ProjectRow | None:
        row = await ProjectRepo.get(session, project_id)
        if row is None:
            return None
        for field in ("name", "product_url", "product_description", "default_audience"):
            if field in patch and patch[field] is not None:
                setattr(row, field, patch[field][:NAME_LIMIT] if field == "name" else patch[field])
        await session.flush()
        return row

    @staticmethod
    async def delete(session: AsyncSession, project_id: UUID) -> bool:
        """Runs and studies cascade — both FKs are ON DELETE CASCADE."""
        result = await session.execute(delete(ProjectRow).where(ProjectRow.id == project_id))
        return bool(result.rowcount)

    @staticmethod
    async def counts(session: AsyncSession, project_id: UUID) -> dict[str, int]:
        runs = (
            await session.execute(
                select(func.count()).select_from(RunRow).where(RunRow.project_id == project_id)
            )
        ).scalar_one()
        studies = (
            await session.execute(
                select(func.count()).select_from(StudyRow).where(StudyRow.project_id == project_id)
            )
        ).scalar_one()
        return {"runs": int(runs), "studies": int(studies)}
