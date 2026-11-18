"""Study fan-out, completion detection, and aggregation.

Three responsibilities, deliberately event-driven:

1. **Fan out** — resolve each segment's persona once, create every run row, enqueue.
2. **Notice the end** — the run that finds no siblings still pending claims
   aggregation. No polling sweeper, no actor blocked for twenty minutes.
3. **Aggregate** — cluster findings across runs into a study report.
"""
from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.runtime_settings import resolve
from app.db.session import get_sessionmaker
from domain.findings import ClaimType, Dimension, Severity, StudyReport
from domain.persona import SyntheticCustomer
from domain.research import RunMode
from domain.study import TERMINAL_RUN_STATUSES, SegmentSpec, StudyStatus
from orchestration.aggregator import (
    AGGREGATOR_PROMPT_VERSION,
    RunFinding,
    RunOutcome,
    StudyAggregator,
)
from orchestration.audience import AudienceUnresolved
from orchestration.completion import build_completion_client
from orchestration.prompts import CUSTOMER_PROMPT_VERSION, EVALUATOR_PROMPT_VERSION
from repositories.run_repo import ProjectRepo, RunRepo
from repositories.study_repo import StudyRepo, StudyReportRepo
from services.run_executor import RunNotFound, build_audience_resolver, resolve_credential

log = logging.getLogger(__name__)

# Matches String(200) on both ProjectRow.name and StudyRow.name.
_NAME_LIMIT = 200

# Resolving an audience is one model call per segment, made inline so the
# operator learns immediately who will be simulated. Under a rate limit those
# calls retry with backoff and the request can outlive the proxy in front of it,
# which the browser reports as a 5xx even though the study was created. Failing
# cleanly at a known bound is better than an ambiguous timeout.
_RESOLVE_TIMEOUT_S = 90


async def create_study(
    session: AsyncSession,
    *,
    settings: Settings,
    product_url: str,
    segments: list[SegmentSpec],
    name: str | None = None,
    task: str | None = None,
    budget: dict | None = None,
    project_id: UUID | None = None,
    sessionmaker: async_sessionmaker | None = None,
) -> tuple[UUID, list[UUID]]:
    """Create the study and all of its run rows. Returns (study_id, run_ids)."""
    project = await ProjectRepo.get_or_create(
        session,
        product_url=product_url,
        project_id=project_id,
        default_audience=segments[0].audience,
    )

    # The persona cache opens its own short sessions; it must use the caller's
    # factory or it binds to a cached engine on someone else's event loop.
    resolver = await build_audience_resolver(session, settings, sessionmaker=sessionmaker)

    resolved: list[tuple[SegmentSpec, SyntheticCustomer]] = []
    try:
        async with asyncio.timeout(_RESOLVE_TIMEOUT_S):
            for spec in segments:
                # Resolved once per segment, not per run: "3 of 3 freelancers hit
                # this" must be a statement about the product, not about three
                # invented people.
                customer = await resolver.resolve(spec.audience, persona_id=spec.persona_id)
                resolved.append((spec, customer))
    except TimeoutError as exc:
        done = len(resolved)
        raise AudienceUnresolved(
            f"Gave up building personas after {_RESOLVE_TIMEOUT_S}s "
            f"({done} of {len(segments)} segments resolved). The model is most "
            "likely rate-limited. Retry, or pick a persona explicitly to skip "
            "synthesis entirely."
        ) from exc

    models = {
        "customer": settings.model_for("customer"),
        "evaluator": settings.model_for("evaluator"),
        "aggregator": settings.model_for("aggregator"),
    }
    study = await StudyRepo.create(
        session,
        project_id=project.id,
        # The derived name embeds the project name, which is itself allowed the
        # full column width — so the result can overflow even though neither
        # input did.
        name=(name or f"{project.name} · {len(segments)} segments")[:_NAME_LIMIT],
        status=StudyStatus.queued.value,
        product_url=product_url,
        task=task,
        segments=[
            {
                "audience": spec.audience,
                "count": spec.count,
                "persona_id": customer.persona.id,
                "persona_label": customer.persona.label,
                "source": customer.source,
                "match_confidence": customer.resolution.match_confidence,
            }
            for spec, customer in resolved
        ],
        budget=budget or {
            "max_tool_calls": settings.DEFAULT_MAX_TOOL_CALLS,
            "max_wall_seconds": settings.DEFAULT_MAX_WALL_SECONDS,
            "max_cost_usd": settings.DEFAULT_MAX_COST_USD,
        },
        models=models,
        prompt_versions={
            "customer": CUSTOMER_PROMPT_VERSION,
            "evaluator": EVALUATOR_PROMPT_VERSION,
            "aggregator": AGGREGATOR_PROMPT_VERSION,
        },
    )

    run_ids: list[UUID] = []
    for spec, customer in resolved:
        for _ in range(spec.count):
            run = await RunRepo.create(
                session,
                project_id=project.id,
                study_id=study.id,
                status="queued",
                mode=(RunMode.directed if task else RunMode.autonomous).value,
                product_url=product_url,
                audience=spec.audience,
                task=task,
                customer=customer.model_dump(mode="json"),
                engine=settings.engine_name,
                # The customer agent runs on the cheaper tier; judgement work
                # (evaluation, aggregation) stays on the stronger model.
                model=settings.model_for("customer"),
                budget=study.budget,
                prompt_versions=study.prompt_versions,
                segment_key=spec.key,
            )
            run_ids.append(run.id)

    return study.id, run_ids


async def note_run_finished(
    study_id: UUID, *, sessionmaker: async_sessionmaker | None = None
) -> bool:
    """Called when a study run reaches a terminal state.

    Returns True if this caller won the race to aggregate.
    """
    maker = sessionmaker or get_sessionmaker()
    async with maker() as session:
        pending = await StudyRepo.pending_run_count(session, study_id)
        if pending:
            await StudyRepo.set_status(session, study_id, StudyStatus.running.value)
            await session.commit()
            return False
        claimed = await StudyRepo.claim_aggregation(session, study_id)
        await session.commit()
        return claimed


async def aggregate_study(
    study_id: UUID, *, sessionmaker: async_sessionmaker | None = None
) -> StudyReport:
    """Cluster every run's findings into the study report."""
    maker = sessionmaker or get_sessionmaker()

    async with maker() as session:
        settings = await resolve(session)
        study = await StudyRepo.get(session, study_id)
        if study is None:
            raise RunNotFound(f"study {study_id} does not exist")
        runs = await StudyRepo.runs(session, study_id)
        provider = "openai" if runs and runs[0].engine == "openai" else "anthropic"
        credential_kind, secret = await resolve_credential(
            session, settings, provider=provider
        )

        findings: list[RunFinding] = []
        outcomes: list[RunOutcome] = []
        for run in runs:
            persona = (run.customer or {}).get("persona", {})
            segment = run.segment_key or run.audience
            report = run.report
            outcomes.append(
                RunOutcome(
                    run_id=str(run.id),
                    segment=segment,
                    persona_label=persona.get("label", run.audience),
                    status=run.status,
                    outcome=report.outcome if report else None,
                    would_continue=report.would_continue if report else None,
                    purchase_intent=report.purchase_intent if report else None,
                    retention_intent=report.retention_intent if report else None,
                    satisfaction=report.customer_satisfaction if report else None,
                    quotes=tuple(report.quotes or ()) if report else (),
                )
            )
            if report is None:
                continue
            for finding in sorted(report.findings, key=lambda f: f.ordinal):
                findings.append(
                    RunFinding(
                        # Short, stable, and unambiguous for the model to echo back.
                        finding_id=f"{str(run.id)[:8]}-{finding.ordinal}",
                        run_id=str(run.id),
                        segment=segment,
                        persona_label=persona.get("label", run.audience),
                        # Stored as plain strings; RunFinding declares the enums.
                        # Dataclasses don't coerce, so do it here rather than let
                        # a str reach code that reads `.value`.
                        dimension=Dimension(finding.dimension),
                        severity=Severity(finding.severity),
                        claim_type=ClaimType(finding.claim_type),
                        observation=finding.observation,
                        impact=finding.impact,
                        quotes=tuple(
                            e.get("quote") for e in (finding.evidence or []) if e.get("quote")
                        ),
                    )
                )
        product_url, task = study.product_url, study.task
        await session.commit()

    aggregator = StudyAggregator(
        build_completion_client(
            provider=provider,
            credential_kind=credential_kind,
            secret=secret,
            model=settings.model_for_provider(provider, "aggregator"),
            effort=settings.MODEL_EFFORT,
        )
    )
    report = await aggregator.aggregate(
        product_url=product_url, task=task, findings=findings, outcomes=outcomes
    )

    async with maker() as session:
        await StudyReportRepo.save(
            session,
            study_id=study_id,
            report=report,
            aggregator_version=AGGREGATOR_PROMPT_VERSION,
        )
        await StudyRepo.set_status(session, study_id, StudyStatus.completed.value)
        await session.commit()

    log.info(
        "study %s aggregated: %d consensus findings from %d runs",
        study_id, len(report.consensus_findings), report.run_count,
    )
    return report


async def cancel_study(
    study_id: UUID, *, sessionmaker: async_sessionmaker | None = None
) -> int:
    """Stop a study and every run still in flight. Returns runs cancelled.

    The study is marked first: `claim_aggregation` refuses a cancelled study, so
    the last run finishing cannot then trigger an aggregation nobody wants.
    """
    from services import cancellation

    maker = sessionmaker or get_sessionmaker()
    cancelled = 0
    async with maker() as session:
        await StudyRepo.set_status(session, study_id, StudyStatus.cancelled.value)
        for run in await StudyRepo.runs(session, study_id):
            if run.status in TERMINAL_RUN_STATUSES:
                continue
            cancellation.request_cancel("run", str(run.id))
            if run.status == "queued":
                # Never started; stop it here rather than waiting for a worker.
                await RunRepo.set_status(
                    session, run.id, "cancelled", {"stop_reason": "cancelled"}
                )
            cancelled += 1
        await session.commit()
    log.info("cancelled study %s (%d runs)", study_id, cancelled)
    return cancelled
