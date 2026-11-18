"""A study: one product, several audience segments, several customers each.

One synthetic customer is an anecdote. A study is the unit that turns anecdotes
into a research result — the same product experienced independently N times, with
findings clustered and ranked by how many customers actually hit them.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StudyStatus(StrEnum):
    queued = "queued"
    running = "running"
    aggregating = "aggregating"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})


class SegmentSpec(BaseModel):
    """One audience, sampled `count` times.

    The persona resolves **once** per segment and is shared by every run in it, so
    "3 of 3 freelancers hit this" is a statement about the product rather than
    about three differently-invented people. Varying personas within a segment is
    a separate axis, deliberately not built yet.
    """

    model_config = ConfigDict(frozen=True)

    audience: str = Field(min_length=2, max_length=300)
    count: int = Field(default=3, ge=1, le=15)
    persona_id: str | None = None

    @property
    def key(self) -> str:
        return self.audience.strip().lower()
