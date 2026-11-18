"""Cluster findings across a study's runs into corroborated results.

**The LLM clusters; code counts.** The model is asked only to group
semantically-equivalent findings and return their ids. Agreement counts,
per-segment hit rates and severity rollups are all computed here from those ids,
so a model that miscounts cannot produce "8 of 9" — it never writes that figure.

Same discipline as `validate_findings`: a cluster citing a finding id that isn't
in the study is dropped before it can reach a report.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass

from domain.findings import (
    ClaimType,
    ClusterSet,
    ConsensusFinding,
    Dimension,
    SegmentAgreement,
    SegmentSummary,
    Severity,
    StudyReport,
    rank_consensus,
    severity_rank,
)
from orchestration.completion import StructuredCompletion
from orchestration.prompts import AGGREGATOR_SYSTEM, build_aggregator_input

log = logging.getLogger(__name__)

AGGREGATOR_PROMPT_VERSION = "aggregator/1"


@dataclass(frozen=True)
class RunFinding:
    """One per-run finding, flattened with the context the aggregator needs."""

    finding_id: str
    run_id: str
    segment: str
    persona_label: str
    dimension: Dimension
    severity: Severity
    claim_type: ClaimType
    observation: str
    impact: str
    quotes: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunOutcome:
    """Per-run report facts the study rolls up without asking a model."""

    run_id: str
    segment: str
    persona_label: str
    status: str
    outcome: str | None
    would_continue: bool | None
    purchase_intent: str | None
    retention_intent: str | None
    satisfaction: int | None
    quotes: tuple[str, ...] = ()


class StudyAggregator:
    def __init__(self, client: StructuredCompletion) -> None:
        self._client = client

    async def aggregate(
        self,
        *,
        product_url: str,
        task: str | None,
        findings: list[RunFinding],
        outcomes: list[RunOutcome],
    ) -> StudyReport:
        analysed = [o for o in outcomes if o.status == "completed"]
        run_count = len(analysed) or len(outcomes)

        if not findings:
            return _empty_report(
                headline=(
                    "No findings were produced by this study — the runs completed "
                    "but nothing was recorded to analyse."
                ),
                outcomes=outcomes,
                run_count=run_count,
            )

        cluster_set = await self._client.parse(
            system=AGGREGATOR_SYSTEM,
            user=build_aggregator_input(
                product_url=product_url, task=task, findings=findings
            ),
            schema=ClusterSet,
        )

        by_id = {f.finding_id: f for f in findings}
        segment_totals = Counter(o.segment for o in analysed)

        consensus: list[ConsensusFinding] = []
        for cluster in cluster_set.clusters:
            members = [by_id[i] for i in cluster.source_finding_ids if i in by_id]
            dropped = len(cluster.source_finding_ids) - len(members)
            if dropped:
                # The model referenced findings that don't exist in this study.
                log.warning(
                    "aggregator cited %d unknown finding id(s) in cluster %r",
                    dropped,
                    cluster.statement[:60],
                )
            if not members:
                continue
            consensus.append(
                _build_consensus(cluster, members, run_count, segment_totals)
            )

        warnings = [c for c in consensus if c.likely_environmental]
        real = [c for c in consensus if not c.likely_environmental]
        real.sort(key=rank_consensus, reverse=True)

        return StudyReport(
            headline=cluster_set.headline,
            run_count=len(outcomes),
            analysed_count=len(analysed),
            would_continue_rate=_rate(analysed, lambda o: o.would_continue is True),
            purchase_intent=_distribution(analysed, lambda o: o.purchase_intent),
            retention_intent=_distribution(analysed, lambda o: o.retention_intent),
            segments=_segment_summaries(outcomes),
            consensus_findings=real,
            environment_warnings=warnings,
        )


# ---------------------------------------------------------------------------
# Everything below is arithmetic — never model output.
# ---------------------------------------------------------------------------


def _build_consensus(
    cluster,
    members: list[RunFinding],
    run_count: int,
    segment_totals: Counter,
) -> ConsensusFinding:
    # One run reporting the same thing twice is still one run agreeing.
    run_ids = sorted({m.run_id for m in members})
    hits_by_segment: dict[str, set[str]] = defaultdict(set)
    for m in members:
        hits_by_segment[m.segment].add(m.run_id)

    severities = [m.severity for m in members]
    worst = max(severities, key=severity_rank)

    # A cluster of interpretations does not become an observation by being
    # popular. Take the weakest claim type present.
    claim_order = [ClaimType.hypothesis, ClaimType.interpretation, ClaimType.observed_behavior]
    weakest = min((m.claim_type for m in members), key=claim_order.index)

    quotes: list[str] = []
    for m in members:
        for q in m.quotes:
            if q and q not in quotes:
                quotes.append(q)

    return ConsensusFinding(
        statement=cluster.statement,
        dimension=cluster.dimension,
        severity=worst,
        claim_type=weakest,
        impact=cluster.impact,
        recommendation=cluster.recommendation,
        agreement_count=len(run_ids),
        run_count=run_count,
        segments=[
            SegmentAgreement(
                segment=segment,
                hit=len(hits_by_segment.get(segment, ())),
                total=total,
            )
            for segment, total in sorted(segment_totals.items())
        ],
        severity_spread=sorted(set(severities), key=severity_rank, reverse=True),
        source_finding_ids=[m.finding_id for m in members],
        run_ids=run_ids,
        quotes=quotes[:5],
        likely_environmental=cluster.likely_environmental,
    )


def _rate(outcomes: list[RunOutcome], predicate) -> float:
    if not outcomes:
        return 0.0
    return round(sum(1 for o in outcomes if predicate(o)) / len(outcomes), 3)


def _distribution(outcomes: list[RunOutcome], key) -> dict[str, int]:
    counts = Counter(key(o) for o in outcomes if key(o) is not None)
    return dict(sorted(counts.items()))


def _segment_summaries(outcomes: list[RunOutcome]) -> list[SegmentSummary]:
    grouped: dict[str, list[RunOutcome]] = defaultdict(list)
    for o in outcomes:
        grouped[o.segment].append(o)

    summaries = []
    for segment, runs in sorted(grouped.items()):
        done = [r for r in runs if r.status == "completed"]
        scores = [r.satisfaction for r in done if r.satisfaction is not None]
        summaries.append(
            SegmentSummary(
                segment=segment,
                persona_label=runs[0].persona_label,
                runs=len(runs),
                completed=len(done),
                would_continue=sum(1 for r in done if r.would_continue),
                outcomes=dict(Counter(r.outcome for r in done if r.outcome)),
                mean_satisfaction=round(sum(scores) / len(scores), 2) if scores else None,
            )
        )
    return summaries


def _empty_report(*, headline: str, outcomes: list[RunOutcome], run_count: int) -> StudyReport:
    analysed = [o for o in outcomes if o.status == "completed"]
    return StudyReport(
        headline=headline,
        run_count=len(outcomes),
        analysed_count=len(analysed),
        would_continue_rate=_rate(analysed, lambda o: o.would_continue is True),
        purchase_intent=_distribution(analysed, lambda o: o.purchase_intent),
        retention_intent=_distribution(analysed, lambda o: o.retention_intent),
        segments=_segment_summaries(outcomes),
    )
