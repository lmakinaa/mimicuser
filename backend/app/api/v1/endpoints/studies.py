"""Study endpoints. Routing only."""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.api.v1.public_mode import OPERATOR_ONLY, VISITOR_KEY_REQUIRED
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import (
    CreateStudyRequest,
    StudyDetailResponse,
    StudySummaryResponse,
)
from app.core.config import Settings
from app.core.runtime_settings import get_runtime_settings
from app.db.session import get_session
from domain.findings import (
    ConsensusFinding,
    SegmentAgreement,
    SegmentSummary,
    Severity,
    StudyReport,
)
from domain.study import SegmentSpec
from orchestration.audience import AudienceUnresolved
from repositories.orm import StudyRow
from repositories.study_repo import StudyRepo
from services.study_executor import cancel_study, create_study
from worker.tasks import enqueue_aggregation, enqueue_run

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/studies", dependencies=VISITOR_KEY_REQUIRED, response_model=StudyDetailResponse, status_code=status.HTTP_201_CREATED)
async def create(
    body: CreateStudyRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> StudyDetailResponse:
    try:
        study_id, run_ids = await create_study(
            session,
            settings=settings,
            product_url=str(body.product_url),
            segments=[SegmentSpec(**s.model_dump()) for s in body.segments],
            name=body.name,
            task=body.task,
            budget=body.budget.model_dump() if body.budget else None,
            project_id=body.project_id,
        )
    except AudienceUnresolved as exc:
        # No persona could be resolved. Guessing here is exactly the failure
        # this system exists to avoid, so the operator is told instead.
        raise HTTPException(503, str(exc)) from exc
    except LookupError as exc:
        # An explicitly named project that does not exist. Previously this fell
        # through and silently created a *different* project.
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except SQLAlchemyError as exc:
        # Everything outside the two cases above used to fall through as a bare
        # 500 with no logged cause. A column too narrow for a legal input landed
        # here and was invisible from the response alone.
        log.exception("study creation failed against the database")
        raise HTTPException(500, f"Could not save the study: {type(exc).__name__}") from exc
    await session.commit()

    for run_id in run_ids:
        enqueue_run(str(run_id))

    row = await StudyRepo.get(session, study_id)
    if row is None:  # pragma: no cover - the row was just committed
        raise HTTPException(500, "Study was created but could not be read back")
    return await _detail(session, row)


@router.get("/studies", response_model=list[StudySummaryResponse])
async def list_studies(
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[StudySummaryResponse]:
    rows = await StudyRepo.list_recent(session, limit=limit)
    return [await _summary(session, r) for r in rows]


@router.get("/studies/{study_id}", response_model=StudyDetailResponse)
async def get_study(
    study_id: UUID, session: AsyncSession = Depends(get_session)
) -> StudyDetailResponse:
    row = await StudyRepo.get(session, study_id)
    if row is None:
        raise HTTPException(404, "Study not found")
    return await _detail(session, row)


@router.post("/studies/{study_id}/cancel", response_model=StudyDetailResponse)
async def cancel(
    study_id: UUID, session: AsyncSession = Depends(get_session)
) -> StudyDetailResponse:
    """Stop a study and every run still in flight."""
    row = await StudyRepo.get(session, study_id)
    if row is None:
        raise HTTPException(404, "Study not found")
    await cancel_study(study_id)

    fresh = await StudyRepo.get(session, study_id)
    assert fresh is not None
    return await _detail(session, fresh)


@router.post("/studies/{study_id}/aggregate", dependencies=OPERATOR_ONLY, response_model=StudyDetailResponse)
async def reaggregate(
    study_id: UUID, session: AsyncSession = Depends(get_session)
) -> StudyDetailResponse:
    """Re-cluster a finished study. The study-level analogue of re-evaluating a run.

    Enqueued rather than run inline: aggregation reads every finding in the study
    and can wait out a long rate limit, which would hang the request.
    """
    row = await StudyRepo.get(session, study_id)
    if row is None:
        raise HTTPException(404, "Study not found")
    enqueue_aggregation(str(study_id))
    return await _detail(session, row)


# -- serialisation ----------------------------------------------------------


async def _progress(session: AsyncSession, study_id: UUID) -> dict[str, int]:
    runs = await StudyRepo.runs(session, study_id)
    counts: dict[str, int] = {}
    for run in runs:
        counts[run.status] = counts.get(run.status, 0) + 1
    counts["total"] = len(runs)
    return counts


async def _summary(session: AsyncSession, row: StudyRow) -> StudySummaryResponse:
    progress = await _progress(session, row.id)
    # The stored status only advances when a run *finishes* — that write is what
    # claims aggregation, and moving it earlier would race. For display, a study
    # whose runs have started is running, whatever the row still says.
    status = row.status
    if status == "queued" and any(
        progress.get(s) for s in ("running", "evaluating", "completed", "failed")
    ):
        status = "running"

    return StudySummaryResponse(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        status=status,
        product_url=row.product_url,
        task=row.task,
        segments=row.segments or [],
        progress=progress,
        headline=row.report.headline if row.report else None,
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


async def _detail(session: AsyncSession, row: StudyRow) -> StudyDetailResponse:
    base = (await _summary(session, row)).model_dump()
    runs = await StudyRepo.runs(session, row.id)

    report = None
    if row.report is not None:
        r = row.report
        ordered = sorted(r.findings, key=lambda f: f.ordinal)
        consensus = [_finding(f) for f in ordered if not f.likely_environmental]
        warnings = [_finding(f) for f in ordered if f.likely_environmental]
        report = StudyReport(
            headline=r.headline,
            run_count=r.run_count,
            analysed_count=r.analysed_count,
            would_continue_rate=r.would_continue_rate,
            purchase_intent=r.purchase_intent or {},
            retention_intent=r.retention_intent or {},
            segments=[SegmentSummary(**s) for s in (r.segments or [])],
            consensus_findings=consensus,
            environment_warnings=warnings,
        )

    return StudyDetailResponse(
        **base,
        models=row.models or {},
        budget=row.budget or {},
        prompt_versions=row.prompt_versions or {},
        error=row.error,
        report=report,
        runs=[
            {
                "id": str(run.id),
                "status": run.status,
                "segment": run.segment_key or run.audience,
                "persona_label": (run.customer or {}).get("persona", {}).get("label", ""),
                "model": run.model,
                "outcome": run.report.outcome if run.report else None,
                "would_continue": run.report.would_continue if run.report else None,
                "duration_ms": run.duration_ms,
                "stop_reason": run.stop_reason,
            }
            for run in runs
        ],
    )


def _finding(row) -> ConsensusFinding:
    return ConsensusFinding(
        statement=row.statement,
        dimension=row.dimension,
        severity=row.severity,
        claim_type=row.claim_type,
        impact=row.impact,
        recommendation=row.recommendation,
        agreement_count=row.agreement_count,
        run_count=row.run_count,
        segments=[SegmentAgreement(**s) for s in (row.segments or [])],
        severity_spread=[Severity(s) for s in (row.severity_spread or [])],
        source_finding_ids=row.source_finding_ids or [],
        run_ids=row.run_ids or [],
        quotes=row.quotes or [],
        likely_environmental=row.likely_environmental,
    )
