"""Project / run / event / report persistence. SQL only, no business logic."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from domain.events import SessionEvent
from domain.findings import RunReport
from repositories.orm import (
    FindingRow,
    ProjectRow,
    RunReportRow,
    RunRow,
    SessionEventRow,
)


# ProjectRepo moved to repositories/project_repo.py when projects stopped being
# an implementation detail. Re-exported so existing imports keep working.
from repositories.project_repo import ProjectRepo  # noqa: E402,F401


class RunRepo:
    @staticmethod
    async def create(session: AsyncSession, **fields: Any) -> RunRow:
        row = RunRow(**fields)
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def get(session: AsyncSession, run_id: UUID) -> RunRow | None:
        return (
            await session.execute(
                select(RunRow)
                .where(RunRow.id == run_id)
                .options(selectinload(RunRow.report).selectinload(RunReportRow.findings))
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_recent(
        session: AsyncSession, *, project_id: UUID | None = None, limit: int = 50
    ) -> list[RunRow]:
        # `report` is eager-loaded because the list view renders the outcome.
        # Lazily touching a relationship on an AsyncSession raises MissingGreenlet
        # rather than issuing the query, so it has to be loaded here.
        stmt = (
            select(RunRow)
            .order_by(RunRow.created_at.desc())
            .limit(limit)
            .options(selectinload(RunRow.report))
        )
        if project_id:
            stmt = stmt.where(RunRow.project_id == project_id)
        return list((await session.execute(stmt)).scalars())

    @staticmethod
    async def set_status(
        session: AsyncSession, run_id: UUID, status: str, fields: dict[str, Any]
    ) -> None:
        row = await session.get(RunRow, run_id)
        if row is None:
            return
        row.status = status
        now = datetime.now(UTC)
        if status == "running" and row.started_at is None:
            row.started_at = now
        if status in {"completed", "failed", "cancelled"}:
            row.finished_at = now
        for key, value in fields.items():
            if value is not None and hasattr(row, key):
                setattr(row, key, value)
        await session.flush()


class EventRepo:
    @staticmethod
    async def append(session: AsyncSession, event: SessionEvent) -> None:
        session.add(
            SessionEventRow(
                run_id=event.run_id,
                seq=event.seq,
                ts=event.ts,
                type=event.type.value,
                url=event.url,
                action=event.action,
                target=event.target,
                input=event.input,
                result=event.result,
                observation=event.observation,
                screenshot_key=event.screenshot_key,
                error=event.error,
                meta=event.meta,
            )
        )
        await session.flush()

    @staticmethod
    async def list_for_run(
        session: AsyncSession, run_id: UUID, *, after_seq: int = 0, limit: int = 500
    ) -> list[SessionEventRow]:
        return list(
            (
                await session.execute(
                    select(SessionEventRow)
                    .where(SessionEventRow.run_id == run_id, SessionEventRow.seq > after_seq)
                    .order_by(SessionEventRow.seq)
                    .limit(limit)
                )
            ).scalars()
        )

    @staticmethod
    async def count_for_run(session: AsyncSession, run_id: UUID) -> int:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(SessionEventRow)
                    .where(SessionEventRow.run_id == run_id)
                )
            ).scalar_one()
        )


class ReportRepo:
    @staticmethod
    async def save(
        session: AsyncSession,
        *,
        run_id: UUID,
        report: RunReport,
        evaluator_version: str,
    ) -> RunReportRow:
        existing = (
            await session.execute(
                select(RunReportRow).where(RunReportRow.run_id == run_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            # Re-evaluation replaces the report but never the event stream.
            await session.delete(existing)
            await session.flush()

        row = RunReportRow(
            run_id=run_id,
            evaluator_version=evaluator_version,
            summary=report.summary,
            first_impression=report.first_impression,
            what_they_thought_it_was=report.what_they_thought_it_was,
            task_completed=report.task_completed,
            outcome=report.outcome.value,
            would_continue=report.would_continue,
            purchase_intent=report.purchase_intent.value
            if hasattr(report.purchase_intent, "value")
            else str(report.purchase_intent),
            retention_intent=report.retention_intent.value
            if hasattr(report.retention_intent, "value")
            else str(report.retention_intent),
            abandonment_reason=report.abandonment_reason,
            customer_satisfaction=report.customer_satisfaction,
            quotes=list(report.quotes),
        )
        session.add(row)
        await session.flush()

        for ordinal, finding in enumerate(report.findings):
            session.add(
                FindingRow(
                    report_id=row.id,
                    ordinal=ordinal,
                    dimension=finding.dimension.value,
                    claim_type=finding.claim_type.value,
                    observation=finding.observation,
                    evidence=[e.model_dump(mode="json") for e in finding.evidence],
                    impact=finding.impact,
                    severity=finding.severity.value,
                    confidence=finding.confidence.value,
                    recommendation=finding.recommendation,
                )
            )
        await session.flush()
        return row
