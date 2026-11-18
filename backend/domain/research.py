"""Run-level value objects shared by orchestration, engines, and tools.

No engine-specific types appear here — that is the whole point of the module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal
from uuid import UUID

from domain.persona import SyntheticCustomer


class RunMode(StrEnum):
    autonomous = "autonomous"
    directed = "directed"


class RunStatus(StrEnum):
    queued = "queued"
    running = "running"
    evaluating = "evaluating"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


CredentialKind = Literal["oauth", "api_key"]


@dataclass(frozen=True)
class ModelSpec:
    """Which model, on which engine, with which credential.

    `credential_ref` is an indirection rather than a raw secret so that a later
    credential broker (or the reference implementation's LLM proxy) can slot in
    without touching the engines.
    """

    model: str
    engine: str
    provider: str = "anthropic"
    credential_kind: CredentialKind = "oauth"
    credential_ref: str | None = None
    effort: str = "high"


@dataclass(frozen=True)
class RunBudget:
    """Safety nets, not semantics.

    A run that stops because of one of these did not "fail" and did not reveal
    that the customer gave up — the evaluator is told which limit fired so it
    does not misread our cut-off as the customer's judgement.
    """

    max_tool_calls: int = 60
    max_wall_seconds: int = 600
    max_cost_usd: float = 3.0


@dataclass(frozen=True)
class ResearchBrief:
    """Everything the customer agent is given about the assignment."""

    run_id: UUID
    project_id: UUID
    product_url: str
    product_description: str | None
    customer: SyntheticCustomer
    task: str | None
    mode: RunMode
    workspace_notes: list[str] = field(default_factory=list)

    @property
    def is_directed(self) -> bool:
        return self.mode is RunMode.directed


@dataclass(frozen=True)
class RunPaths:
    """Storage keys for one run. Relative and provider-agnostic."""

    project_id: UUID
    run_id: UUID

    @property
    def prefix(self) -> str:
        return f"projects/{self.project_id}/runs/{self.run_id}"

    @property
    def screenshots(self) -> str:
        return f"{self.prefix}/screenshots"

    @property
    def events(self) -> str:
        return f"{self.prefix}/events.json"

    @property
    def report(self) -> str:
        return f"{self.prefix}/report.json"

    @property
    def recording(self) -> str:
        return f"{self.prefix}/recording.webm"

    def screenshot(self, seq: int, label: str) -> str:
        safe = "".join(c for c in label if c.isalnum() or c in "-_")[:32] or "shot"
        return f"{self.screenshots}/{seq:04d}-{safe}.png"


@dataclass(frozen=True)
class RuntimeConfig:
    """Filesystem knobs handed to engines. Snapshot per run."""

    run_home: Path
    storage_root: Path
    headless: bool = True
    viewport_width: int = 1280
    viewport_height: int = 900
