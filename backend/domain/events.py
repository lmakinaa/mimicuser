"""Session event stream + the engine output contract.

Two layers live here, and the distinction matters.

`SessionEvent` is the **durable** record: ordered, append-only, persisted to
Postgres and mirrored to `events.json`. It is the evidence base — nothing
downstream may assert anything it cannot cite a `seq` for.

The frozen `*Event` dataclasses below are the **engine output contract**. Every
`AgentEngine.run` may emit only these types. They are dataclasses rather than a
dict schema on purpose: adding a field here breaks every adapter at type-check
time, which a JSON blob cannot do. (Device borrowed from the internal reference's
`runtime/engines/events.py`.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    run_started = "run_started"
    agent_message = "agent_message"
    agent_thought = "agent_thought"
    tool_call = "tool_call"
    tool_result = "tool_result"
    observation = "observation"
    navigation = "navigation"
    note = "note"
    task_status = "task_status"
    engine_error = "engine_error"
    session_finished = "session_finished"
    run_completed = "run_completed"


class SessionEvent(BaseModel):
    """One durable record of something that happened during a run."""

    model_config = ConfigDict(frozen=True)

    run_id: UUID
    seq: int
    ts: datetime
    type: EventType

    url: str | None = None
    action: str | None = None
    target: str | None = None
    input: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    observation: str | None = None
    screenshot_key: str | None = None
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    def to_transcript_line(self) -> str:
        """One-line rendering for the evaluator's transcript.

        The evaluator reads this, not the ORM rows — keeping the rendering here
        means the transcript format is versioned with the event schema.
        """
        head = f"[{self.seq}] {self.type.value}"
        parts: list[str] = []
        if self.url:
            parts.append(f"url={self.url}")
        if self.action and self.action != self.type.value:
            parts.append(f"action={self.action}")
        if self.target:
            parts.append(f"target={self.target}")
        if self.input:
            parts.append(f"input={self.input}")
        if self.type is EventType.note:
            # Sentiment is the difference between "they mentioned pricing" and
            # "pricing frustrated them"; the evaluator needs it.
            sentiment = self.meta.get("sentiment")
            about = self.meta.get("about")
            if sentiment:
                parts.append(f"felt={sentiment}")
            if about:
                parts.append(f"about={about}")
            if self.observation:
                parts.append(f'said: "{self.observation}"')
        elif self.observation:
            parts.append(self.observation)
        if self.error:
            parts.append(f"ERROR: {self.error}")
        return (f"{head} " + " · ".join(parts)) if parts else head


# ---------------------------------------------------------------------------
# Engine output contract — engines emit only these.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentMessageEvent:
    """Customer-facing narration from the agent."""

    text: str


@dataclass(frozen=True)
class AgentThoughtEvent:
    """Reasoning, where the engine exposes it. Never shown as customer speech."""

    text: str


@dataclass(frozen=True)
class EngineErrorEvent:
    message: str
    fatal: bool = False
    status: int | None = None


@dataclass(frozen=True)
class EngineUsageEvent:
    """Incremental token/cost accounting mid-run."""

    tokens_in: int = 0
    tokens_out: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float | None = None


EngineEvent = AgentMessageEvent | AgentThoughtEvent | EngineErrorEvent | EngineUsageEvent


@dataclass
class EngineOutcome:
    """Mechanics of a finished engine run. Never a verdict about the product."""

    stop_reason: str = "unknown"
    turns: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float | None = None
    duration_ms: int = 0
    is_error: bool = False
    error: str | None = None
    final_text: str = ""
    engine_session_handle: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


def utcnow() -> datetime:
    return datetime.now(UTC)
