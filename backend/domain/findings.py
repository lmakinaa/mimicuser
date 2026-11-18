"""Evaluation schema — structured research findings, not an essay.

Three ideas are load-bearing here:

1. `ClaimType` separates what was *observed* from the evaluator's *reading* of it
   from a *hypothesis* about cause or fix. Speculation must never be presented as
   fact, and the distinction is enforced mechanically (`validate_findings`), not
   requested in prose.
2. `severity=positive` is legal. A research system that can only report problems
   is a bug tracker.
3. `task_completed` sits beside nineteen other dimensions with no privileged
   position. A technically successful task can still produce a damning report —
   that case is the product's whole reason to exist.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Dimension(StrEnum):
    first_impression = "first_impression"
    value_proposition_clarity = "value_proposition_clarity"
    discoverability = "discoverability"
    usability = "usability"
    friction = "friction"
    confusion = "confusion"
    terminology = "terminology"
    information_architecture = "information_architecture"
    trust = "trust"
    motivation = "motivation"
    perceived_value = "perceived_value"
    pricing_perception = "pricing_perception"
    product_fit = "product_fit"
    purchase_intent = "purchase_intent"
    retention_intent = "retention_intent"
    abandonment = "abandonment"
    feature_understanding = "feature_understanding"
    expectations_vs_reality = "expectations_vs_reality"
    task_completion = "task_completion"
    customer_satisfaction = "customer_satisfaction"


class ClaimType(StrEnum):
    observed_behavior = "observed_behavior"
    interpretation = "interpretation"
    hypothesis = "hypothesis"


class Severity(StrEnum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    positive = "positive"


class Confidence(StrEnum):
    high = "high"
    medium = "medium"
    low = "low"


class Intent(StrEnum):
    none = "none"
    low = "low"
    medium = "medium"
    high = "high"


class SessionOutcome(StrEnum):
    """How the customer's session ended. Only `error` is a failure of ours."""

    accomplished = "accomplished"
    partially = "partially"
    gave_up = "gave_up"
    blocked = "blocked"
    not_interested = "not_interested"
    satisfied_exploring = "satisfied_exploring"
    budget_exhausted = "budget_exhausted"
    error = "error"


class Evidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_seq: int = Field(description="seq of the SessionEvent this claim rests on")
    url: str | None = None
    quote: str | None = Field(
        default=None, description="verbatim from a note or agent message"
    )
    screenshot_key: str | None = None


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True)

    dimension: Dimension
    claim_type: ClaimType
    observation: str = Field(description="what happened or what is true, stated plainly")
    evidence: list[Evidence] = Field(default_factory=list)
    impact: str = Field(description="why this matters to the business")
    severity: Severity
    confidence: Confidence
    recommendation: str | None = None


class RunReport(BaseModel):
    """The run-level narrative. One per (run, evaluator prompt version)."""

    model_config = ConfigDict(frozen=True)

    summary: str
    first_impression: str
    what_they_thought_it_was: str = Field(
        description="what the customer believed the product did, in their own words"
    )

    task_completed: bool | None = Field(
        default=None, description="null in autonomous mode — there was no assigned task"
    )
    outcome: SessionOutcome
    would_continue: bool
    purchase_intent: Intent
    retention_intent: Intent
    abandonment_reason: str | None = None
    customer_satisfaction: int = Field(
        ge=1, le=5, description="simulation parameter, not a measured metric"
    )

    quotes: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Study-level aggregation
# ---------------------------------------------------------------------------


class FindingCluster(BaseModel):
    """What the aggregator model is allowed to return: a grouping, nothing more.

    Deliberately carries **no counts**. The model groups semantically-equivalent
    findings and names the group; every number a reader sees is computed in code
    from `source_finding_ids`. A model that miscounts cannot invent "8 of 9",
    because it never writes that figure.
    """

    model_config = ConfigDict(frozen=True)

    statement: str = Field(description="the shared finding, stated once, neutrally")
    dimension: Dimension
    impact: str
    recommendation: str | None = None
    source_finding_ids: list[str] = Field(
        description="ids of the per-run findings in this cluster, verbatim"
    )
    likely_environmental: bool = Field(
        default=False,
        description=(
            "true when this looks like our browser failing (blank page, blocked, "
            "nothing rendered) rather than a defect in the product"
        ),
    )


class ClusterSet(BaseModel):
    """The aggregator's whole response."""

    model_config = ConfigDict(frozen=True)

    headline: str
    clusters: list[FindingCluster] = Field(default_factory=list)


class SegmentAgreement(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment: str
    hit: int
    total: int

    @property
    def rate(self) -> float:
        return self.hit / self.total if self.total else 0.0


class ConsensusFinding(BaseModel):
    """One finding, corroborated across runs. Every number here is computed."""

    model_config = ConfigDict(frozen=True)

    statement: str
    dimension: Dimension
    severity: Severity
    claim_type: ClaimType
    impact: str
    recommendation: str | None = None

    agreement_count: int = Field(description="runs that produced a finding in this cluster")
    run_count: int = Field(description="runs analysed in the study")
    segments: list[SegmentAgreement] = Field(default_factory=list)
    severity_spread: list[Severity] = Field(default_factory=list)

    source_finding_ids: list[str] = Field(default_factory=list)
    run_ids: list[str] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)

    likely_environmental: bool = False

    @property
    def agreement_rate(self) -> float:
        return self.agreement_count / self.run_count if self.run_count else 0.0


class SegmentSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    segment: str
    persona_label: str
    runs: int
    completed: int
    would_continue: int
    outcomes: dict[str, int] = Field(default_factory=dict)
    mean_satisfaction: float | None = None


class StudyReport(BaseModel):
    """The study-level result."""

    model_config = ConfigDict(frozen=True)

    headline: str
    run_count: int
    analysed_count: int
    would_continue_rate: float
    purchase_intent: dict[str, int] = Field(default_factory=dict)
    retention_intent: dict[str, int] = Field(default_factory=dict)
    segments: list[SegmentSummary] = Field(default_factory=list)
    consensus_findings: list[ConsensusFinding] = Field(default_factory=list)
    # Near-universal "the page was blank" findings are more likely our headless
    # browser being blocked than a product defect; they are surfaced apart from
    # the ranked findings so they cannot masquerade as research.
    environment_warnings: list[ConsensusFinding] = Field(default_factory=list)


_SEVERITY_RANK = {
    Severity.critical: 4,
    Severity.high: 3,
    Severity.medium: 2,
    Severity.low: 1,
    Severity.positive: 0,
}


def severity_rank(severity: Severity) -> int:
    return _SEVERITY_RANK[severity]


def rank_consensus(finding: ConsensusFinding) -> tuple[int, float, int]:
    """Sort key: how bad it is, then how many people hit it."""
    return (
        severity_rank(finding.severity),
        finding.agreement_rate,
        finding.agreement_count,
    )


class EvidenceViolation(ValueError):
    """A finding claimed observed behaviour it cannot cite."""


def validate_findings(findings: list[Finding], valid_seqs: set[int]) -> list[str]:
    """Enforce the evidence contract. Returns human-readable problems.

    Rules:
      - `observed_behavior` must cite at least one piece of evidence.
      - Every cited `event_seq` must resolve to a real event on this run.

    Interpretations and hypotheses may cite evidence but are not required to —
    that is exactly what makes them interpretations and hypotheses.
    """
    problems: list[str] = []
    for i, f in enumerate(findings):
        if f.claim_type is ClaimType.observed_behavior and not f.evidence:
            problems.append(
                f"finding[{i}] ({f.dimension.value}) claims observed_behavior "
                f"but cites no evidence"
            )
        for e in f.evidence:
            if e.event_seq not in valid_seqs:
                problems.append(
                    f"finding[{i}] ({f.dimension.value}) cites event_seq="
                    f"{e.event_seq}, which is not an event on this run"
                )
    return problems
