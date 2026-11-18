"""Tool contract + the recording registry.

The registry is where engine-agnosticism is actually enforced at runtime. Every
handler is wrapped with the event recorder here, so `tool_call` / `tool_result` /
`observation` events are byte-identical no matter which engine drove the loop.
An engine that forgot to report a tool call cannot produce a divergent trace,
because reporting is not the engine's job.
"""
from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArtifactRef:
    key: str
    kind: str  # "screenshot" | "video" | "trace"


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    payload: dict[str, Any] = field(default_factory=dict)
    observation: str | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    error: str | None = None
    # Set by `finish`; the runner reads it to stop the loop cleanly.
    terminal: bool = False

    @staticmethod
    def failure(message: str, **payload: Any) -> ToolResult:
        return ToolResult(ok=False, error=message, payload=payload)


ToolHandler = Callable[[dict[str, Any]], Awaitable[ToolResult] | ToolResult]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    group: str = "browser"


class BudgetExhausted(RuntimeError):
    """The run's tool-call budget is spent. A safety net, not a verdict."""


class ToolRegistry:
    """Holds tools, records their use, and enforces the call budget.

    `record` is injected by the runner (`SessionRecorder.record_tool_use`) so
    this module has no knowledge of persistence.
    """

    def __init__(
        self,
        tools: list[Tool],
        *,
        record: Callable[[Tool, dict[str, Any], ToolResult, int], Awaitable[None]],
        max_calls: int,
        # Checked before every tool call. A cancelled run stops at the next
        # boundary so the browser closes and the event stream flushes — killing
        # the process would lose both, and the partial session is still evidence.
        should_cancel: Callable[[], bool] | None = None,
    ) -> None:
        self._tools = {t.name: t for t in tools}
        self._record = record
        self._max_calls = max_calls
        self._should_cancel = should_cancel
        self.calls = 0
        self.cancelled = False
        self.terminal_result: ToolResult | None = None

    def __iter__(self):
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    @property
    def budget_exhausted(self) -> bool:
        return self.calls >= self._max_calls

    @property
    def finished(self) -> bool:
        return self.terminal_result is not None

    def check_cancelled(self) -> bool:
        """Latch on first observation — the flag may be cleared behind us."""
        if not self.cancelled and self._should_cancel is not None:
            self.cancelled = self._should_cancel()
        return self.cancelled

    def wrapped(self, tool: Tool) -> Callable[[dict[str, Any]], Awaitable[ToolResult]]:
        """Return the recorded, budget-checked form of `tool`'s handler.

        Engines invoke *this*, never `tool.handler` directly.
        """
        is_coro = inspect.iscoroutinefunction(tool.handler)

        async def invoke(args: dict[str, Any]) -> ToolResult:
            if self.check_cancelled():
                return ToolResult.failure("This session has been stopped.")
            if self.budget_exhausted:
                # Reported to the model in its own language so it wraps up
                # rather than thrashing against a wall it cannot see.
                return ToolResult.failure(
                    "You have run out of time for this session. Use `finish` now "
                    "to record how it went."
                )

            self.calls += 1
            started = time.perf_counter()
            try:
                result = await tool.handler(args) if is_coro else tool.handler(args)
            except Exception as exc:  # noqa: BLE001 — tool failures are data
                log.exception("tool %s raised", tool.name)
                result = ToolResult.failure(f"{tool.name} failed: {exc}")

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            if result.terminal:
                self.terminal_result = result

            await self._record(tool, args, result, elapsed_ms)
            return result

        return invoke
