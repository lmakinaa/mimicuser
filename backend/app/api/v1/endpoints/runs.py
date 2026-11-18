"""Project and run endpoints. Routing only: validate → call → serialise."""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.public_mode import OPERATOR_ONLY, VISITOR_KEY_REQUIRED
from app.api.v1.schemas import (
    CreateRunRequest,
    EventPage,
    EventResponse,
    RunDetailResponse,
    RunSummaryResponse,
)
from app.core.config import Settings
from app.core.runtime_settings import get_runtime_settings
from app.db.session import get_session
from domain.findings import Finding, RunReport
from domain.research import RunMode
from orchestration.audience import AudienceUnresolved
from orchestration.prompts import CUSTOMER_PROMPT_VERSION, EVALUATOR_PROMPT_VERSION
from repositories.orm import RunRow
from repositories.run_repo import EventRepo, ProjectRepo, RunRepo
from services.run_executor import build_audience_resolver
from storage.local import LocalStorageProvider
from worker.tasks import enqueue_run

log = logging.getLogger(__name__)
router = APIRouter()


# -- runs -------------------------------------------------------------------


@router.post("/runs", dependencies=VISITOR_KEY_REQUIRED, response_model=RunDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_run(
    body: CreateRunRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> RunDetailResponse:
    product_url = str(body.product_url)

    # A project is an organising convenience, not something the operator should
    # have to create first — the brief's minimum input is URL + audience.
    try:
        project = await ProjectRepo.get_or_create(
            session,
            product_url=product_url,
            project_id=body.project_id,
            default_audience=body.audience,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc

    # Resolves against the catalogue first; crafts a persona with an LLM when
    # nothing matches, so an audience we don't ship is still simulated as itself.
    resolver = await build_audience_resolver(session, settings)
    try:
        customer = await resolver.resolve(
            body.audience,
            persona_id=body.persona_id,
            overrides=body.persona_overrides,
        )
    except AudienceUnresolved as exc:
        # No persona could be resolved. Guessing here is exactly the failure
        # this system exists to avoid, so the operator is told instead.
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    budget = (body.budget.model_dump() if body.budget else {
        "max_tool_calls": settings.DEFAULT_MAX_TOOL_CALLS,
        "max_wall_seconds": settings.DEFAULT_MAX_WALL_SECONDS,
        "max_cost_usd": settings.DEFAULT_MAX_COST_USD,
    })
    if body.model and body.model not in settings.valid_models(settings.LLM_PROVIDER):
        raise HTTPException(422, f"Unknown {settings.LLM_PROVIDER} model: {body.model}")

    run = await RunRepo.create(
        session,
        project_id=project.id,
        status="queued",
        mode=(RunMode.directed if body.task else RunMode.autonomous).value,
        product_url=product_url,
        audience=body.audience,
        task=body.task,
        customer=customer.model_dump(mode="json"),
        engine=settings.engine_name,
        model=body.model or settings.model_for("default"),
        budget=budget,
        prompt_versions={
            "customer": CUSTOMER_PROMPT_VERSION,
            "evaluator": EVALUATOR_PROMPT_VERSION,
        },
        segment_key=body.segment_key,
    )
    await session.commit()
    enqueue_run(str(run.id))

    # Re-fetch so `report` is eagerly loaded; the freshly-inserted instance would
    # otherwise lazy-load it and raise MissingGreenlet on the async session.
    fresh = await RunRepo.get(session, run.id)
    if fresh is None:  # pragma: no cover - the row was just committed
        raise HTTPException(500, "Run was created but could not be read back")
    return await _detail(session, fresh, settings)


@router.get("/runs", response_model=list[RunSummaryResponse])
async def list_runs(
    project_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[RunSummaryResponse]:
    rows = await RunRepo.list_recent(session, project_id=project_id, limit=limit)
    return [_summary(r) for r in rows]


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
async def get_run(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> RunDetailResponse:
    row = await RunRepo.get(session, run_id)
    if row is None:
        raise HTTPException(404, "Run not found")
    return await _detail(session, row, settings)


@router.post("/runs/{run_id}/cancel", response_model=RunDetailResponse)
async def cancel_run(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> RunDetailResponse:
    """Stop a run.

    A queued run is stopped here; a running one is flagged and stops at its next
    tool call, so the browser closes cleanly and the partial session is kept.
    """
    from services import cancellation

    row = await RunRepo.get(session, run_id)
    if row is None:
        raise HTTPException(404, "Run not found")
    if row.status in {"completed", "failed", "cancelled"}:
        raise HTTPException(409, f"Run is already {row.status}")

    cancellation.request_cancel("run", str(run_id))
    if row.status == "queued":
        await RunRepo.set_status(
            session, run_id, "cancelled", {"stop_reason": "cancelled"}
        )
        await session.commit()

    fresh = await RunRepo.get(session, run_id)
    assert fresh is not None
    return await _detail(session, fresh, settings)


@router.post("/runs/{run_id}/evaluate", dependencies=OPERATOR_ONLY, response_model=RunDetailResponse)
async def reevaluate(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> RunDetailResponse:
    """Analyse a recorded session again, without re-driving the browser.

    Recovers a run whose evaluation failed (a rate limit, a transient error) and
    lets an improved evaluator prompt be applied to sessions already captured.
    """
    from services.run_executor import RunNotFound, reevaluate_run

    try:
        await reevaluate_run(run_id)
    except RunNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Evaluation failed: {exc}") from exc

    row = await RunRepo.get(session, run_id)
    if row is None:
        raise HTTPException(404, "Run not found")
    return await _detail(session, row, settings)


@router.get("/runs/{run_id}/events", response_model=EventPage)
async def get_events(
    run_id: UUID,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_runtime_settings),
) -> EventPage:
    if await RunRepo.get(session, run_id) is None:
        raise HTTPException(404, "Run not found")

    storage = LocalStorageProvider(settings.STORAGE_ROOT)
    rows = await EventRepo.list_for_run(session, run_id, after_seq=after_seq, limit=limit)
    events = [
        EventResponse(
            seq=r.seq,
            ts=r.ts,
            type=r.type,
            url=r.url,
            action=r.action,
            target=r.target,
            input=r.input,
            result=r.result,
            observation=r.observation,
            screenshot_url=storage.url_for(r.screenshot_key) if r.screenshot_key else None,
            error=r.error,
            meta=r.meta or {},
        )
        for r in rows
    ]
    return EventPage(
        run_id=run_id,
        events=events,
        next_after_seq=events[-1].seq if len(events) == limit else None,
    )


# -- serialisation helpers --------------------------------------------------


def _summary(row: RunRow) -> RunSummaryResponse:
    customer = row.customer or {}
    persona = customer.get("persona", {})
    resolution = customer.get("resolution", {})
    report = row.report
    return RunSummaryResponse(
        id=row.id,
        project_id=row.project_id,
        status=row.status,
        mode=row.mode,
        product_url=row.product_url,
        audience=row.audience,
        task=row.task,
        persona_label=persona.get("label", "unknown"),
        persona_id=persona.get("id", "unknown"),
        persona_source=customer.get("source", "catalogue"),
        match_confidence=float(resolution.get("match_confidence", 0.0)),
        engine=row.engine,
        model=row.model,
        outcome=report.outcome if report else None,
        would_continue=report.would_continue if report else None,
        stop_reason=row.stop_reason,
        duration_ms=row.duration_ms,
        cost_usd=row.cost_usd,
        error=row.error,
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


async def _detail(
    session: AsyncSession, row: RunRow, settings: Settings
) -> RunDetailResponse:
    base = _summary(row).model_dump()
    report = None
    if row.report is not None:
        r = row.report
        report = RunReport(
            summary=r.summary,
            first_impression=r.first_impression,
            what_they_thought_it_was=r.what_they_thought_it_was,
            task_completed=r.task_completed,
            outcome=r.outcome,
            would_continue=r.would_continue,
            purchase_intent=r.purchase_intent,
            retention_intent=r.retention_intent,
            abandonment_reason=r.abandonment_reason,
            customer_satisfaction=r.customer_satisfaction,
            quotes=r.quotes or [],
            findings=[
                Finding(
                    dimension=f.dimension,
                    claim_type=f.claim_type,
                    observation=f.observation,
                    evidence=f.evidence or [],
                    impact=f.impact,
                    severity=f.severity,
                    confidence=f.confidence,
                    recommendation=f.recommendation,
                )
                for f in sorted(r.findings, key=lambda x: x.ordinal)
            ],
        )

    return RunDetailResponse(
        **base,
        customer=row.customer or {},
        budget=row.budget or {},
        prompt_versions=row.prompt_versions or {},
        event_count=await EventRepo.count_for_run(session, row.id),
        report=report,
    )
