"""Study aggregation: the LLM clusters, code counts."""
from __future__ import annotations

import pytest

from domain.findings import (
    ClaimType,
    ClusterSet,
    Dimension,
    FindingCluster,
    Severity,
)
from orchestration.aggregator import RunFinding, RunOutcome, StudyAggregator

pytestmark = pytest.mark.unit


class StubClient:
    def __init__(self, cluster_set: ClusterSet) -> None:
        self.cluster_set = cluster_set
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.cluster_set


def _finding(fid, run, segment, *, severity=Severity.high, claim=ClaimType.observed_behavior,
             dimension=Dimension.terminology, quote=None) -> RunFinding:
    return RunFinding(
        finding_id=fid, run_id=run, segment=segment, persona_label=segment.title(),
        dimension=dimension, severity=severity, claim_type=claim,
        observation="They did not understand 'Workspace'.",
        impact="Blocks activation.", quotes=(quote,) if quote else (),
    )


def _outcome(run, segment, *, status="completed", would_continue=False,
             purchase="low", retention="none", satisfaction=2) -> RunOutcome:
    return RunOutcome(
        run_id=run, segment=segment, persona_label=segment.title(), status=status,
        outcome="gave_up", would_continue=would_continue, purchase_intent=purchase,
        retention_intent=retention, satisfaction=satisfaction,
    )


def _cluster(ids, **kw) -> ClusterSet:
    return ClusterSet(
        headline="Customers could not work out what a Workspace was.",
        clusters=[
            FindingCluster(
                statement="Nobody understood the term 'Workspace'.",
                dimension=Dimension.terminology,
                impact="Blocks the first step of activation.",
                source_finding_ids=ids,
                **kw,
            )
        ],
    )


async def test_agreement_is_computed_not_taken_from_the_model():
    """The whole trustworthiness argument. The model returns a grouping; every
    number a reader sees is arithmetic over the ids in that grouping."""
    findings = [
        _finding("a-0", "run-a", "freelancers"),
        _finding("b-0", "run-b", "freelancers"),
        _finding("c-0", "run-c", "founders"),
    ]
    outcomes = [
        _outcome("run-a", "freelancers"), _outcome("run-b", "freelancers"),
        _outcome("run-c", "founders"), _outcome("run-d", "founders"),
    ]
    report = await StudyAggregator(StubClient(_cluster(["a-0", "b-0", "c-0"]))).aggregate(
        product_url="https://example.com", task=None, findings=findings, outcomes=outcomes
    )

    finding = report.consensus_findings[0]
    assert finding.agreement_count == 3
    assert finding.run_count == 4
    assert finding.agreement_rate == 0.75
    assert {(s.segment, s.hit, s.total) for s in finding.segments} == {
        ("freelancers", 2, 2), ("founders", 1, 2),
    }


async def test_a_run_reporting_twice_still_counts_once():
    findings = [
        _finding("a-0", "run-a", "freelancers"),
        _finding("a-1", "run-a", "freelancers"),
    ]
    outcomes = [_outcome("run-a", "freelancers"), _outcome("run-b", "freelancers")]
    report = await StudyAggregator(StubClient(_cluster(["a-0", "a-1"]))).aggregate(
        product_url="u", task=None, findings=findings, outcomes=outcomes
    )
    assert report.consensus_findings[0].agreement_count == 1


async def test_clusters_citing_unknown_finding_ids_drop_those_ids():
    findings = [_finding("a-0", "run-a", "freelancers")]
    outcomes = [_outcome("run-a", "freelancers")]
    report = await StudyAggregator(
        StubClient(_cluster(["a-0", "ghost-9", "nope-1"]))
    ).aggregate(product_url="u", task=None, findings=findings, outcomes=outcomes)

    finding = report.consensus_findings[0]
    assert finding.source_finding_ids == ["a-0"]
    assert finding.agreement_count == 1


async def test_a_cluster_of_only_unknown_ids_is_discarded():
    report = await StudyAggregator(StubClient(_cluster(["ghost-1"]))).aggregate(
        product_url="u", task=None,
        findings=[_finding("a-0", "run-a", "s")], outcomes=[_outcome("run-a", "s")],
    )
    assert report.consensus_findings == []


async def test_severity_rolls_up_to_the_worst_and_records_the_spread():
    findings = [
        _finding("a-0", "run-a", "s", severity=Severity.low),
        _finding("b-0", "run-b", "s", severity=Severity.critical),
    ]
    outcomes = [_outcome("run-a", "s"), _outcome("run-b", "s")]
    report = await StudyAggregator(StubClient(_cluster(["a-0", "b-0"]))).aggregate(
        product_url="u", task=None, findings=findings, outcomes=outcomes
    )
    finding = report.consensus_findings[0]
    assert finding.severity is Severity.critical
    assert finding.severity_spread == [Severity.critical, Severity.low]


async def test_a_cluster_of_interpretations_does_not_become_an_observation():
    """Popularity is not evidence. The weakest claim in the cluster wins."""
    findings = [
        _finding("a-0", "run-a", "s", claim=ClaimType.observed_behavior),
        _finding("b-0", "run-b", "s", claim=ClaimType.interpretation),
    ]
    outcomes = [_outcome("run-a", "s"), _outcome("run-b", "s")]
    report = await StudyAggregator(StubClient(_cluster(["a-0", "b-0"]))).aggregate(
        product_url="u", task=None, findings=findings, outcomes=outcomes
    )
    assert report.consensus_findings[0].claim_type is ClaimType.interpretation


async def test_environment_warnings_are_kept_out_of_the_ranked_findings():
    """A blank page reported by everyone is probably our browser being blocked,
    not a product defect — it must not top the report."""
    findings = [_finding("a-0", "run-a", "s"), _finding("b-0", "run-b", "s")]
    outcomes = [_outcome("run-a", "s"), _outcome("run-b", "s")]
    report = await StudyAggregator(
        StubClient(_cluster(["a-0", "b-0"], likely_environmental=True))
    ).aggregate(product_url="u", task=None, findings=findings, outcomes=outcomes)

    assert report.consensus_findings == []
    assert len(report.environment_warnings) == 1
    assert report.environment_warnings[0].agreement_count == 2


async def test_findings_rank_by_severity_then_agreement():
    findings = [
        _finding("a-0", "run-a", "s", severity=Severity.medium),
        _finding("b-0", "run-b", "s", severity=Severity.medium),
        _finding("c-0", "run-c", "s", severity=Severity.critical),
    ]
    outcomes = [_outcome(f"run-{c}", "s") for c in "abc"]
    cluster_set = ClusterSet(
        headline="h",
        clusters=[
            FindingCluster(
                statement="widespread but medium", dimension=Dimension.usability,
                impact="i", source_finding_ids=["a-0", "b-0"],
            ),
            FindingCluster(
                statement="rare but critical", dimension=Dimension.trust,
                impact="i", source_finding_ids=["c-0"],
            ),
        ],
    )
    report = await StudyAggregator(StubClient(cluster_set)).aggregate(
        product_url="u", task=None, findings=findings, outcomes=outcomes
    )
    assert [f.statement for f in report.consensus_findings] == [
        "rare but critical", "widespread but medium",
    ]


async def test_study_rollups_are_computed_from_run_reports():
    outcomes = [
        _outcome("run-a", "freelancers", would_continue=True, purchase="high", satisfaction=4),
        _outcome("run-b", "freelancers", would_continue=False, purchase="low", satisfaction=2),
        _outcome("run-c", "founders", would_continue=False, purchase="none", satisfaction=1),
        _outcome("run-d", "founders", status="failed", satisfaction=None),
    ]
    report = await StudyAggregator(StubClient(ClusterSet(headline="h"))).aggregate(
        product_url="u", task=None, findings=[], outcomes=outcomes
    )
    assert report.run_count == 4
    assert report.analysed_count == 3
    assert report.would_continue_rate == round(1 / 3, 3)
    assert report.purchase_intent == {"high": 1, "low": 1, "none": 1}

    freelancers = next(s for s in report.segments if s.segment == "freelancers")
    assert freelancers.completed == 2
    assert freelancers.would_continue == 1
    assert freelancers.mean_satisfaction == 3.0

    founders = next(s for s in report.segments if s.segment == "founders")
    assert founders.runs == 2 and founders.completed == 1


async def test_no_findings_still_produces_an_honest_report():
    report = await StudyAggregator(StubClient(ClusterSet(headline="unused"))).aggregate(
        product_url="u", task=None, findings=[], outcomes=[_outcome("run-a", "s")]
    )
    assert report.consensus_findings == []
    assert "No findings" in report.headline
