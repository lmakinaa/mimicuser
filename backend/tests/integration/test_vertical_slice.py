"""End-to-end orchestration against a real browser, with the model stubbed.

Everything except the LLM itself is exercised for real: Playwright, the snapshot
handles, the tool registry, the recorder, local storage, the evidence contract
and report persistence.

The engine is a scripted stand-in that performs a realistic customer sequence.
That is exactly what the `AgentEngine` protocol is for — if this test needed to
know anything about Claude, the abstraction would not be doing its job.
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from domain.events import AgentMessageEvent, EngineOutcome, EventType
from domain.findings import (
    ClaimType,
    Confidence,
    Dimension,
    Evidence,
    Finding,
    Intent,
    RunReport,
    SessionOutcome,
    Severity,
)
from domain.persona import AudienceResolution, SyntheticCustomer
from domain.research import ModelSpec, ResearchBrief, RunBudget, RunMode, RunStatus
from orchestration.runner import execute_run
from personas import get_persona
from storage.local import LocalStorageProvider

pytestmark = pytest.mark.integration

PAGE = """
<html><head><title>Invoicely</title></head><body>
  <h1>Invoicing that gets you paid</h1>
  <p>Create a workspace to start.</p>
  <a href="#pricing" id="pricing">Pricing</a>
  <button id="start">Create workspace</button>
</body></html>
"""


class ScriptedEngine:
    """Drives a fixed, realistic customer sequence through the real tools."""

    name = "scripted"

    def supports(self, model: ModelSpec) -> bool:
        return True

    async def run(self, *, run_id, system_prompt, opening_message, tools, model,
                  budget, config, emit) -> EngineOutcome:
        # The prompt must actually carry the persona and the anti-QA framing.
        assert "Freelancer" in system_prompt
        assert "You are not QA" in system_prompt

        call = {t.name: tools.wrapped(t) for t in tools}
        await emit(AgentMessageEvent(text="Let's see what this is."))

        first = await call["look"]({})
        assert first.ok, first.error
        assert "Invoicing that gets you paid" in first.payload["page"]

        await call["note"]({
            "reaction": "I don't know what a Workspace is or why I need one.",
            "sentiment": "confused",
            "about": "Create workspace button",
        })

        # Act on a handle minted by the snapshot — the core browser contract.
        ref = next(r for r, v in _refs(first).items() if v["name"] == "Pricing")
        assert (await call["click"]({"ref": ref})).ok

        await call["note"]({
            "reaction": "Fine, but I still don't know what it costs.",
            "sentiment": "hesitant",
        })
        await call["finish"]({
            "outcome": "gave_up",
            "reason": "Couldn't tell what it costs or why I need a workspace.",
            "would_continue": False,
        })
        return EngineOutcome(stop_reason="customer_finished", turns=4)


def _refs(result) -> dict:
    """Recover handles from the snapshot text the tool returned."""
    out = {}
    for line in result.payload["page"].splitlines():
        line = line.strip()
        if line.startswith("["):
            ref = line[1 : line.index("]")]
            name = line.split('"')[1] if '"' in line else ""
            out[ref] = {"name": name}
    return out


class StubEvaluator:
    """Stands in for the LLM evaluator; the evidence contract is still real."""

    def __init__(self) -> None:
        self.transcript: str | None = None

    async def evaluate(self, *, brief, transcript, valid_seqs, stop_reason, finish_payload):
        self.transcript = transcript
        # Cite a real note event, proving evidence resolves against the stream.
        note_seq = min(valid_seqs)
        return RunReport(
            summary="They understood the pitch but not the pricing or the workspace concept.",
            first_impression="Looks like invoicing software.",
            what_they_thought_it_was="A tool to send invoices.",
            task_completed=None,
            outcome=SessionOutcome(finish_payload["outcome"]),
            would_continue=finish_payload["would_continue"],
            purchase_intent=Intent.low,
            retention_intent=Intent.none,
            abandonment_reason=finish_payload["reason"],
            customer_satisfaction=2,
            quotes=["I don't know what a Workspace is or why I need one."],
            findings=[
                Finding(
                    dimension=Dimension.terminology,
                    claim_type=ClaimType.observed_behavior,
                    observation="The customer did not understand 'Workspace'.",
                    evidence=[Evidence(event_seq=note_seq)],
                    impact="Blocks the first step of activation.",
                    severity=Severity.high,
                    confidence=Confidence.high,
                    recommendation="Say what a workspace is for at the point of creation.",
                )
            ],
        )


async def test_full_slice_produces_evidence_backed_findings(tmp_path: Path):
    page_file = tmp_path / "product.html"
    page_file.write_text(PAGE, encoding="utf-8")

    persona = get_persona("freelancer")
    assert persona is not None
    customer = SyntheticCustomer(
        persona=persona,
        source="catalogue",
        resolution=AudienceResolution(
            audience="Freelance designers", method="alias_match",
            matched_id="freelancer", match_confidence=1.0,
        ),
    )
    brief = ResearchBrief(
        run_id=uuid4(),
        project_id=uuid4(),
        product_url=page_file.as_uri(),
        product_description=None,
        customer=customer,
        task=None,
        mode=RunMode.autonomous,
    )

    storage = LocalStorageProvider(tmp_path / "storage")
    events, statuses, reports = [], [], []
    evaluator = StubEvaluator()

    from engines import registry

    registry.register(ScriptedEngine())
    try:
        report = await execute_run(
            brief=brief,
            model=ModelSpec(model="stub", engine="scripted", credential_ref="x"),
            budget=RunBudget(max_tool_calls=20, max_wall_seconds=90),
            storage=storage,
            evaluator=evaluator,
            persist_event=lambda e: _collect(events, e),
            update_status=lambda s, f: _collect(statuses, s),
            save_report=lambda r: _collect(reports, r),
            workdir=tmp_path / "work",
            headless=True,
            # file:// is refused by the navigation guard for good reason, so the
            # scripted customer starts on the page rather than opening it.
            session_factory=lambda **kw: _StartOnPage(page_file, **kw),
        )
    finally:
        registry._reset_for_tests()
        registry._bootstrap()

    # --- lifecycle -------------------------------------------------------
    assert statuses[0] is RunStatus.running
    assert RunStatus.evaluating in statuses
    # The customer gave up. That is a completed run, not a failed one.
    assert statuses[-1] is RunStatus.completed
    assert report.outcome is SessionOutcome.gave_up
    assert report.would_continue is False

    # --- the event stream is the evidence base ---------------------------
    kinds = [e.type for e in events]
    assert EventType.run_started in kinds
    assert EventType.note in kinds
    assert EventType.session_finished in kinds
    assert EventType.run_completed in kinds

    notes = [e for e in events if e.type is EventType.note]
    assert len(notes) == 2
    assert notes[0].meta["sentiment"] == "confused"
    # A reaction is only evidence if you can see what provoked it.
    assert notes[0].screenshot_key is not None

    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs) == list(range(1, len(seqs) + 1))

    # --- artifacts on the StorageProvider, keys only in the DB -----------
    root = storage.local_path(f"projects/{brief.project_id}/runs/{brief.run_id}")
    assert (root / "events.json").is_file()
    assert (root / "report.json").is_file()
    shots = list((root / "screenshots").glob("*.png"))
    assert shots, "no screenshots captured"

    persisted = json.loads((root / "events.json").read_text())
    assert len(persisted) == len(events)

    # --- the epistemic contract holds ------------------------------------
    finding = report.findings[0]
    assert finding.claim_type is ClaimType.observed_behavior
    assert finding.evidence[0].event_seq in set(seqs)

    # --- the evaluator saw a usable transcript ---------------------------
    assert "felt=confused" in evaluator.transcript
    assert "Workspace" in evaluator.transcript


async def _collect(sink: list, value):
    sink.append(value)


class _StartOnPage:
    """Real BrowserSession, pre-navigated to a local file.

    `file://` is refused by the navigation guard (an SSRF boundary we do not
    want to weaken for a test), so the fixture page is loaded directly.
    """

    def __init__(self, page_file: Path, **kwargs):
        from tools.browser.session import BrowserSession

        # The fixture is a local file, which the egress guard rightly refuses.
        # Only this fixture opts out; every production session enforces it.
        self._inner = BrowserSession(**{**kwargs, "enforce_egress": False})
        self._page_file = page_file

    async def start(self):
        await self._inner.start()
        await self._inner.page.goto(self._page_file.as_uri())

    def __getattr__(self, item):
        return getattr(self._inner, item)
