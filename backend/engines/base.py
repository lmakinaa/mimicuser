"""`AgentEngine` — the contract every agent runtime implements.

Duck-typed via `typing.Protocol` so engines aren't forced to inherit and tests
can pass plain objects.

**The engine owns its loop; the platform owns the context and the tools.** An
engine receives a system prompt, an opening message, a `ToolRegistry` and an
event sink, and drives whatever loop its runtime provides until the customer
finishes or a budget stops it.

**Output contract.** `run` may emit only the frozen event dataclasses in
`domain/events.py`, and must return exactly one `EngineOutcome`. Tool-call
events are *not* the engine's responsibility — `ToolRegistry` records those, so
the trace is identical across engines by construction.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable
from uuid import UUID

from domain.events import EngineEvent, EngineOutcome
from domain.research import ModelSpec, RunBudget, RuntimeConfig
from tools.base import ToolRegistry

EventSink = Callable[[EngineEvent], Awaitable[None]]


@runtime_checkable
class AgentEngine(Protocol):
    name: str

    def supports(self, model: ModelSpec) -> bool:
        """Honest provider/model compatibility.

        Never `return True` unconditionally. The internal reference documents
        that shortcut in `ENGINE_ABSTRACTION_DEBT.md` as a bug waiting for the
        second engine to register; we ship the protocol with two adapters in
        mind, so it is written honestly from the start.
        """
        ...

    async def run(
        self,
        *,
        run_id: UUID,
        system_prompt: str,
        opening_message: str,
        tools: ToolRegistry,
        model: ModelSpec,
        budget: RunBudget,
        config: RuntimeConfig,
        emit: EventSink,
    ) -> EngineOutcome:
        """Drive the agent to completion. Emits events; returns mechanics."""
        ...
