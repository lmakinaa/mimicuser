"""Research evaluator — a separate role, a separate pass, a separate prompt.

Reads the event stream and nothing else. It never touches the browser, which is
what makes three things possible: re-evaluating an old session under a better
methodology, running alternative evaluators over the same evidence, and
aggregating across runs later without re-driving anything.

The customer agent never writes a `Finding`; it writes `note` events. Turning
reactions into findings is this module's job, and keeping the roles apart is
what stops the customer breaking character to author a UX report mid-session.
"""
from __future__ import annotations

import logging

from domain.findings import (
    ClaimType,
    RunReport,
    SessionOutcome,
    validate_findings,
)
from domain.research import ResearchBrief
from orchestration.completion import CompletionError, StructuredCompletion
from orchestration.prompts import EVALUATOR_SYSTEM, build_evaluator_input

log = logging.getLogger(__name__)


class ResearchEvaluator:
    def __init__(self, client: StructuredCompletion) -> None:
        self._client = client

    async def evaluate(
        self,
        *,
        brief: ResearchBrief,
        transcript: str,
        valid_seqs: set[int],
        stop_reason: str,
        finish_payload: dict | None,
    ) -> RunReport:
        user = build_evaluator_input(
            brief=brief,
            transcript=transcript,
            stop_reason=stop_reason,
            finish_payload=finish_payload,
        )

        report = await self._client.parse(
            system=EVALUATOR_SYSTEM, user=user, schema=RunReport
        )

        report = self._enforce_evidence(report, valid_seqs)
        return self._reconcile(report, brief=brief, stop_reason=stop_reason)

    # -- the epistemic contract -------------------------------------------

    @staticmethod
    def _enforce_evidence(report: RunReport, valid_seqs: set[int]) -> RunReport:
        """Drop citations that don't resolve; demote claims that lose all of them.

        Deleting the finding outright would silently lose a real observation the
        model made but mis-cited. Demoting it to `interpretation` keeps the
        content while telling the reader honestly that it is not directly
        evidenced — which is the whole point of `claim_type`.
        """
        problems = validate_findings(list(report.findings), valid_seqs)
        if not problems:
            return report

        log.warning("evaluator produced %d evidence problems: %s", len(problems), problems)

        repaired = []
        for finding in report.findings:
            kept = [e for e in finding.evidence if e.event_seq in valid_seqs]
            data = finding.model_dump()
            data["evidence"] = [e.model_dump() for e in kept]
            if finding.claim_type is ClaimType.observed_behavior and not kept:
                data["claim_type"] = ClaimType.interpretation
                data["observation"] = (
                    f"{finding.observation} "
                    "[evidence citation could not be verified against the session]"
                )
            repaired.append(type(finding).model_validate(data))

        return report.model_copy(update={"findings": repaired})

    @staticmethod
    def _reconcile(report: RunReport, *, brief: ResearchBrief, stop_reason: str) -> RunReport:
        """Correct the two things the model is structurally likely to get wrong."""
        updates: dict = {}

        # In autonomous mode there was no task, so task completion is undefined.
        # Models reliably want to fill it in anyway.
        if not brief.is_directed and report.task_completed is not None:
            updates["task_completed"] = None

        # Our own cut-off must never be reported as the customer's decision.
        if stop_reason in {"tool_call_budget", "wall_clock_budget"} and report.outcome not in {
            SessionOutcome.budget_exhausted,
            SessionOutcome.accomplished,
        }:
            updates["outcome"] = SessionOutcome.budget_exhausted
        elif stop_reason == "engine_error" and report.outcome != SessionOutcome.error:
            updates["outcome"] = SessionOutcome.error

        return report.model_copy(update=updates) if updates else report


def fallback_report(*, stop_reason: str, error: str) -> RunReport:
    """Used when evaluation itself fails.

    A run whose evaluation broke still has a complete event stream and
    screenshots. Returning an honest, empty report keeps the run inspectable
    instead of throwing away the session that produced it.
    """
    return RunReport(
        summary=(
            "This session was recorded but could not be analysed automatically. "
            "The event timeline and screenshots are intact and can be reviewed "
            "directly, and the run can be re-evaluated once the problem is fixed."
        ),
        first_impression="(not analysed)",
        what_they_thought_it_was="(not analysed)",
        task_completed=None,
        outcome=SessionOutcome.error if stop_reason == "engine_error" else SessionOutcome.partially,
        would_continue=False,
        purchase_intent="none",
        retention_intent="none",
        abandonment_reason=f"evaluation failed: {error}",
        customer_satisfaction=3,
        quotes=[],
        findings=[],
    )


__all__ = ["ResearchEvaluator", "fallback_report", "cancelled_report", "CompletionError"]


def cancelled_report() -> RunReport:
    """For a run someone stopped on purpose.

    Not a failure and not a verdict about the product — just an honest record
    that a human ended the session. The events and screenshots are intact, so
    `POST /v1/runs/{id}/evaluate` can still analyse it later if wanted.
    """
    return RunReport(
        summary=(
            "This session was stopped before the customer finished. The timeline "
            "and screenshots up to that point are intact and can be analysed with "
            "a re-evaluation if the partial session is still of interest."
        ),
        first_impression="(stopped before the customer formed one)",
        what_they_thought_it_was="(not analysed)",
        task_completed=None,
        outcome=SessionOutcome.error,
        would_continue=False,
        purchase_intent="none",
        retention_intent="none",
        abandonment_reason="the session was stopped by an operator",
        customer_satisfaction=3,
        quotes=[],
        findings=[],
    )
