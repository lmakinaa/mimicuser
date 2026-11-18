"""Study persistence. SQL only, no business logic."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from domain.findings import StudyReport
from domain.study import TERMINAL_RUN_STATUSES
from repositories.orm import (
    ConsensusFindingRow,
    RunReportRow,
    RunRow,
    StudyReportRow,
    StudyRow,
)


class StudyRepo:
    @staticmethod
    async def create(session: AsyncSession, **fields: Any) -> StudyRow:
        row = StudyRow(**fields)
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def get(session: AsyncSession, study_id: UUID) -> StudyRow | None:
        return (
            await session.execute(
                select(StudyRow)
                .where(StudyRow.id == study_id)
                .options(
                    selectinload(StudyRow.report).selectinload(StudyReportRow.findings)
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_recent(session: AsyncSession, *, limit: int = 50) -> list[StudyRow]:
        return list(
            (
                await session.execute(
                    select(StudyRow)
                    .order_by(StudyRow.created_at.desc())
                    .limit(limit)
                    .options(selectinload(StudyRow.report))
                )
            ).scalars()
        )

    @staticmethod
    async def set_status(
        session: AsyncSession, study_id: UUID, status: str, fields: dict[str, Any] | None = None
    ) -> None:
        row = await session.get(StudyRow, study_id)
        if row is None:
            return
        row.status = status
        if status in {"completed", "failed", "cancelled"}:
            row.finished_at = datetime.now(UTC)
        for key, value in (fields or {}).items():
            if value is not None and hasattr(row, key):
                setattr(row, key, value)
        await session.flush()

    @staticmethod
    async def runs(session: AsyncSession, study_id: UUID) -> list[RunRow]:
        return list(
            (
                await session.execute(
                    select(RunRow)
                    .where(RunRow.study_id == study_id)
                    .order_by(RunRow.created_at)
                    # `findings` must be eager too: aggregation reads every
                    # finding, and lazily touching a relationship on an
                    # AsyncSession raises MissingGreenlet rather than querying.
                    .options(
                        selectinload(RunRow.report).selectinload(RunReportRow.findings)
                    )
                )
            ).scalars()
        )

    @staticmethod
    async def pending_run_count(session: AsyncSession, study_id: UUID) -> int:
        """How many of this study's runs have not reached a terminal state.

        The run that observes zero is the last one home and triggers
        aggregation — no sweeper, no long-blocking actor.
        """
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(RunRow)
                    .where(
                        RunRow.study_id == study_id,
                        RunRow.status.notin_(tuple(TERMINAL_RUN_STATUSES)),
                    )
                )
            ).scalar_one()
        )

    @staticmethod
    async def claim_aggregation(session: AsyncSession, study_id: UUID) -> bool:
        """Atomically move `running → aggregating`. Returns True for the winner.

        Two runs can finish in the same instant; a row lock makes exactly one of
        them the aggregator so the study is never analysed twice.
        """
        row = (
            await session.execute(
                select(StudyRow).where(StudyRow.id == study_id).with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or row.status in {"aggregating", "completed", "failed", "cancelled"}:
            return False
        row.status = "aggregating"
        await session.flush()
        return True


class StudyReportRepo:
    @staticmethod
    async def save(
        session: AsyncSession,
        *,
        study_id: UUID,
        report: StudyReport,
        aggregator_version: str,
    ) -> StudyReportRow:
        existing = (
            await session.execute(
                select(StudyReportRow).where(StudyReportRow.study_id == study_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            # Re-aggregation replaces the report; the runs beneath it are untouched.
            await session.delete(existing)
            await session.flush()

        row = StudyReportRow(
            study_id=study_id,
            aggregator_version=aggregator_version,
            headline=report.headline,
            run_count=report.run_count,
            analysed_count=report.analysed_count,
            would_continue_rate=report.would_continue_rate,
            purchase_intent=report.purchase_intent,
            retention_intent=report.retention_intent,
            segments=[s.model_dump(mode="json") for s in report.segments],
        )
        session.add(row)
        await session.flush()

        ordered = [*report.consensus_findings, *report.environment_warnings]
        for ordinal, finding in enumerate(ordered):
            session.add(
                ConsensusFindingRow(
                    report_id=row.id,
                    ordinal=ordinal,
                    statement=finding.statement,
                    dimension=finding.dimension.value,
                    severity=finding.severity.value,
                    claim_type=finding.claim_type.value,
                    impact=finding.impact,
                    recommendation=finding.recommendation,
                    agreement_count=finding.agreement_count,
                    run_count=finding.run_count,
                    segments=[s.model_dump(mode="json") for s in finding.segments],
                    severity_spread=[s.value for s in finding.severity_spread],
                    source_finding_ids=finding.source_finding_ids,
                    run_ids=finding.run_ids,
                    quotes=finding.quotes,
                    likely_environmental=finding.likely_environmental,
                )
            )
        await session.flush()
        return row
