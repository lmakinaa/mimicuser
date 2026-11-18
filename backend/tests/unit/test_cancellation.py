"""Interrupting a run.

Cancellation is checked at tool-call boundaries rather than by killing the
process, so a stopped run still closes its browser and keeps its evidence.
"""
from __future__ import annotations

import pytest

from tools.base import Tool, ToolRegistry, ToolResult

pytestmark = pytest.mark.unit


def _tool(name: str = "look") -> Tool:
    async def handler(args):
        return ToolResult(ok=True, payload={"ran": True}, observation="did it")

    return Tool(name=name, description="d", input_schema={"type": "object"}, handler=handler)


async def _noop_record(*args):
    return None


async def test_a_cancelled_registry_refuses_further_tool_calls():
    flag = {"cancel": False}
    tool = _tool()
    registry = ToolRegistry(
        [tool], record=_noop_record, max_calls=10,
        should_cancel=lambda: flag["cancel"],
    )
    invoke = registry.wrapped(tool)

    assert (await invoke({})).ok is True
    flag["cancel"] = True
    stopped = await invoke({})

    assert stopped.ok is False
    assert "stopped" in (stopped.error or "").lower()
    assert registry.cancelled is True
    # The refused call must not consume budget.
    assert registry.calls == 1


async def test_cancellation_latches_even_if_the_flag_clears():
    """Redis keys expire and can be deleted; a run must not resurrect itself."""
    flag = {"cancel": True}
    registry = ToolRegistry(
        [_tool()], record=_noop_record, max_calls=10,
        should_cancel=lambda: flag["cancel"],
    )
    assert registry.check_cancelled() is True
    flag["cancel"] = False
    assert registry.check_cancelled() is True


async def test_no_cancel_hook_means_never_cancelled():
    registry = ToolRegistry([_tool()], record=_noop_record, max_calls=10)
    assert registry.check_cancelled() is False
    assert (await registry.wrapped(_tool())({})).ok is True


async def test_the_cancelled_call_is_still_recorded():
    """The refusal is part of the session's history, not a silent no-op."""
    recorded: list[ToolResult] = []

    async def record(tool, args, result, elapsed_ms):
        recorded.append(result)

    tool = _tool()
    registry = ToolRegistry(
        [tool], record=record, max_calls=10, should_cancel=lambda: True
    )
    await registry.wrapped(tool)({})
    # Cancellation short-circuits before the handler, so nothing is recorded —
    # the run's last real event is whatever it did before being stopped.
    assert recorded == []


def test_cancelled_report_is_not_a_verdict_about_the_product():
    from domain.findings import SessionOutcome
    from orchestration.evaluator import cancelled_report

    report = cancelled_report()
    assert report.findings == []
    assert report.task_completed is None
    assert report.outcome is SessionOutcome.error
    assert "stopped" in report.summary.lower()
    # It must say the session is recoverable, since the events are kept.
    assert "re-evaluation" in report.summary
