"""The epistemic contract: no observation without resolvable evidence."""
from __future__ import annotations

import pytest

from domain.findings import (
    ClaimType,
    Confidence,
    Dimension,
    Evidence,
    Finding,
    RunReport,
    SessionOutcome,
    Severity,
    validate_findings,
)
from domain.research import RunMode
from orchestration.evaluator import ResearchEvaluator

pytestmark = pytest.mark.unit


def _finding(claim_type: ClaimType, seqs: list[int]) -> Finding:
    return Finding(
        dimension=Dimension.confusion,
        claim_type=claim_type,
        observation="They did not understand the word 'Workspace'.",
        evidence=[Evidence(event_seq=s) for s in seqs],
        impact="Sign-up drop-off at the first screen.",
        severity=Severity.high,
        confidence=Confidence.medium,
    )


def test_observation_without_evidence_is_a_violation():
    problems = validate_findings([_finding(ClaimType.observed_behavior, [])], {1, 2})
    assert len(problems) == 1
    assert "cites no evidence" in problems[0]


def test_evidence_pointing_at_a_nonexistent_event_is_a_violation():
    problems = validate_findings([_finding(ClaimType.observed_behavior, [99])], {1, 2})
    assert any("99" in p for p in problems)


def test_interpretation_may_stand_without_evidence():
    assert validate_findings([_finding(ClaimType.interpretation, [])], {1}) == []


def test_hypothesis_may_stand_without_evidence():
    assert validate_findings([_finding(ClaimType.hypothesis, [])], {1}) == []


def test_valid_observation_passes():
    assert validate_findings([_finding(ClaimType.observed_behavior, [1, 2])], {1, 2}) == []


# -- evaluator repair ------------------------------------------------------


def _report(findings: list[Finding], **kw) -> RunReport:
    base = dict(
        summary="s",
        first_impression="f",
        what_they_thought_it_was="w",
        task_completed=None,
        outcome=SessionOutcome.gave_up,
        would_continue=False,
        purchase_intent="low",
        retention_intent="low",
        abandonment_reason=None,
        customer_satisfaction=2,
        quotes=[],
        findings=findings,
    )
    base.update(kw)
    return RunReport(**base)


def test_unverifiable_observation_is_demoted_not_deleted():
    """Losing the content would throw away a real observation that was
    mis-cited; demoting keeps it and labels it honestly."""
    repaired = ResearchEvaluator._enforce_evidence(
        _report([_finding(ClaimType.observed_behavior, [99])]), {1, 2}
    )
    assert len(repaired.findings) == 1
    finding = repaired.findings[0]
    assert finding.claim_type is ClaimType.interpretation
    assert finding.evidence == []
    assert "could not be verified" in finding.observation


def test_partially_bad_evidence_keeps_the_good_citations():
    repaired = ResearchEvaluator._enforce_evidence(
        _report([_finding(ClaimType.observed_behavior, [1, 99])]), {1, 2}
    )
    finding = repaired.findings[0]
    assert finding.claim_type is ClaimType.observed_behavior
    assert [e.event_seq for e in finding.evidence] == [1]


def test_autonomous_mode_clears_task_completed(monkeypatch):
    """Models fill this in even when no task existed."""
    brief = _brief(RunMode.autonomous)
    reconciled = ResearchEvaluator._reconcile(
        _report([], task_completed=True), brief=brief, stop_reason="customer_finished"
    )
    assert reconciled.task_completed is None


def test_budget_cutoff_is_never_reported_as_the_customers_choice():
    reconciled = ResearchEvaluator._reconcile(
        _report([], outcome=SessionOutcome.gave_up),
        brief=_brief(RunMode.autonomous),
        stop_reason="tool_call_budget",
    )
    assert reconciled.outcome is SessionOutcome.budget_exhausted


def test_completed_task_can_still_be_a_bad_experience():
    """The brief's central case must be representable."""
    report = _report(
        [_finding(ClaimType.observed_behavior, [1])],
        task_completed=True,
        outcome=SessionOutcome.accomplished,
        would_continue=False,
        customer_satisfaction=2,
    )
    reconciled = ResearchEvaluator._reconcile(
        report, brief=_brief(RunMode.directed), stop_reason="customer_finished"
    )
    assert reconciled.task_completed is True
    assert reconciled.would_continue is False
    assert reconciled.findings[0].severity is Severity.high


def _brief(mode: RunMode):
    from uuid import uuid4

    from domain.persona import AudienceResolution, SyntheticCustomer
    from domain.research import ResearchBrief
    from personas import get_persona

    persona = get_persona("freelancer")
    assert persona is not None
    return ResearchBrief(
        run_id=uuid4(),
        project_id=uuid4(),
        product_url="https://example.com",
        product_description=None,
        customer=SyntheticCustomer(
            persona=persona,
            source="catalogue",
            resolution=AudienceResolution(
                audience="freelancers", method="alias_match",
                matched_id="freelancer", match_confidence=1.0,
            ),
        ),
        task="Do a thing" if mode is RunMode.directed else None,
        mode=mode,
    )
