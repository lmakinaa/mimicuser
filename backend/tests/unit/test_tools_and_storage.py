"""Tool recording/budget and storage-key safety."""
from __future__ import annotations

import pytest

from storage.base import UnsafeKey
from storage.local import LocalStorageProvider
from tools.base import Tool, ToolRegistry, ToolResult
from tools.research.toolset import build_research_tools

pytestmark = pytest.mark.unit


def _tool(name: str = "demo", *, result: ToolResult | None = None) -> Tool:
    async def handler(args):
        return result or ToolResult(ok=True, payload={"echo": args}, observation="did it")

    return Tool(name=name, description="d", input_schema={"type": "object"}, handler=handler)


async def test_registry_records_every_call_regardless_of_engine():
    recorded: list[tuple[str, dict, bool]] = []

    async def record(tool, args, result, elapsed_ms):
        recorded.append((tool.name, args, result.ok))

    registry = ToolRegistry([_tool()], record=record, max_calls=10)
    await registry.wrapped(next(iter(registry)))({"a": 1})

    assert recorded == [("demo", {"a": 1}, True)]
    assert registry.calls == 1


async def test_a_raising_handler_becomes_a_recorded_failure():
    """A tool exception is data about the customer's experience, not a crash."""

    async def boom(args):
        raise RuntimeError("kaboom")

    recorded = []

    async def record(tool, args, result, elapsed_ms):
        recorded.append(result)

    tool = Tool(name="boom", description="d", input_schema={}, handler=boom)
    registry = ToolRegistry([tool], record=record, max_calls=5)
    result = await registry.wrapped(tool)({})

    assert result.ok is False
    assert "kaboom" in (result.error or "")
    assert recorded and recorded[0].ok is False


async def test_budget_stops_calls_and_tells_the_model_in_its_own_language():
    async def record(*a):
        return None

    tool = _tool()
    registry = ToolRegistry([tool], record=record, max_calls=1)
    await registry.wrapped(tool)({})
    blocked = await registry.wrapped(tool)({})

    assert registry.budget_exhausted
    assert blocked.ok is False
    assert "finish" in (blocked.error or "")


async def test_finish_marks_the_registry_terminal():
    async def record(*a):
        return None

    tools = build_research_tools()
    registry = ToolRegistry(tools, record=record, max_calls=10)
    finish = next(t for t in tools if t.name == "finish")

    assert registry.finished is False
    result = await registry.wrapped(finish)(
        {"outcome": "gave_up", "reason": "Too confusing", "would_continue": False}
    )
    assert result.terminal is True
    assert registry.finished is True
    assert registry.terminal_result is not None


async def test_giving_up_is_a_valid_outcome_not_an_error():
    async def record(*a):
        return None

    tools = build_research_tools()
    registry = ToolRegistry(tools, record=record, max_calls=10)
    finish = next(t for t in tools if t.name == "finish")

    for outcome in ("gave_up", "not_interested", "blocked"):
        registry.terminal_result = None
        result = await registry.wrapped(finish)(
            {"outcome": outcome, "reason": "r", "would_continue": False}
        )
        assert result.ok is True, f"{outcome} must be a successful termination"


async def test_note_records_sentiment_and_subject():
    tools = build_research_tools()
    note = next(t for t in tools if t.name == "note")
    result = await note.handler(
        {"reaction": "I don't know what a Workspace is", "sentiment": "confused", "about": "label"}
    )
    assert result.ok is True
    assert result.observation == "I don't know what a Workspace is"
    assert result.payload["sentiment"] == "confused"
    assert result.payload["about"] == "label"


# -- storage ---------------------------------------------------------------


async def test_storage_roundtrip(tmp_path):
    storage = LocalStorageProvider(tmp_path)
    key = await storage.write_json("projects/p/runs/r/report.json", {"a": 1})
    assert await storage.read_json(key) == {"a": 1}
    assert await storage.exists(key)


@pytest.mark.parametrize("key", ["../escape.txt", "/etc/passwd", "a/../../b", ""])
async def test_traversal_is_refused_not_sanitised(tmp_path, key):
    storage = LocalStorageProvider(tmp_path)
    with pytest.raises(UnsafeKey):
        await storage.write_bytes(key, b"x", "text/plain")


async def test_screenshot_keys_follow_the_documented_layout(tmp_path):
    from uuid import uuid4

    from domain.research import RunPaths

    paths = RunPaths(project_id=uuid4(), run_id=uuid4())
    key = paths.screenshot(7, "look")
    assert key.endswith("/screenshots/0007-look.png")
    assert key.startswith(f"projects/{paths.project_id}/runs/{paths.run_id}")
