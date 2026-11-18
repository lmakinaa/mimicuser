"""Project endpoints.

A project is the grouping the brief asks for — one product, with its audiences,
studies and runs underneath. It has always existed as a row; these routes make it
something the operator can actually see and manage.

Creating one stays optional. Pasting a URL into a study still creates a project
implicitly, because the brief's minimum input is URL + audience and demanding a
project first would break that.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.api.v1.public_mode import OPERATOR_ONLY
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import (
    CreateProjectRequest,
    ProjectResponse,
    UpdateProjectRequest,
)
from app.db.session import get_session
from repositories.project_repo import ProjectRepo

router = APIRouter(dependencies=OPERATOR_ONLY)


async def _response(session: AsyncSession, row) -> ProjectResponse:
    counts = await ProjectRepo.counts(session, row.id)
    return ProjectResponse(
        id=row.id,
        name=row.name,
        product_url=row.product_url,
        product_description=row.product_description,
        default_audience=row.default_audience,
        created_at=row.created_at,
        run_count=counts["runs"],
        study_count=counts["studies"],
    )


@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create(
    body: CreateProjectRequest, session: AsyncSession = Depends(get_session)
) -> ProjectResponse:
    row = await ProjectRepo.create(
        session,
        name=body.name,
        product_url=str(body.product_url),
        product_description=body.product_description,
        default_audience=body.default_audience,
    )
    await session.commit()
    return await _response(session, row)


@router.get("/projects", response_model=list[ProjectResponse])
async def list_projects(session: AsyncSession = Depends(get_session)) -> list[ProjectResponse]:
    rows = await ProjectRepo.list_all(session)
    return [await _response(session, r) for r in rows]


@router.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_one(
    project_id: UUID, session: AsyncSession = Depends(get_session)
) -> ProjectResponse:
    row = await ProjectRepo.get(session, project_id)
    if row is None:
        raise HTTPException(404, "Project not found")
    return await _response(session, row)


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
async def update(
    project_id: UUID,
    body: UpdateProjectRequest,
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    patch = body.model_dump(exclude_unset=True)
    if "product_url" in patch and patch["product_url"] is not None:
        patch["product_url"] = str(patch["product_url"])
    row = await ProjectRepo.update(session, project_id, patch)
    if row is None:
        raise HTTPException(404, "Project not found")
    await session.commit()
    return await _response(session, row)


@router.delete("/projects/{project_id}", status_code=204)
async def remove(
    project_id: UUID,
    confirm: bool = Query(
        default=False,
        description="Required. Deleting a project removes its studies and runs.",
    ),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a project and everything under it.

    Both FKs are ON DELETE CASCADE, so this takes the studies, runs, events and
    reports with it — months of research behind one call. The explicit `confirm`
    is there so it cannot happen by accident.
    """
    row = await ProjectRepo.get(session, project_id)
    if row is None:
        raise HTTPException(404, "Project not found")
    if not confirm:
        counts = await ProjectRepo.counts(session, project_id)
        raise HTTPException(
            409,
            f"This deletes {counts['studies']} studies and {counts['runs']} runs "
            "with it. Repeat with ?confirm=true.",
        )
    await ProjectRepo.delete(session, project_id)
    await session.commit()
