"""The one engine-specific seam: generic `Tool` → Claude Agent SDK MCP tool.

Adding a tool group touches no engine. Adding an engine means writing the
analogous file under `engines/<name>/tools_adapter.py` and touching no tool.

Response envelope: the SDK's `@tool` expects
`{"content": [{"type": "text", "text": …}], "is_error": bool}`. We JSON-encode
the handler's payload into the text frame so the model gets structured data, and
prepend the human-readable observation so it reads naturally.

Ported from `refs/vl-app-…/runtime/engines/claude/tools_adapter.py`.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from tools.base import Tool, ToolRegistry, ToolResult

log = logging.getLogger(__name__)

MCP_SERVER_NAME = "research"


def _frame(text: str, *, is_error: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        payload["is_error"] = True
    return payload


def _render(result: ToolResult) -> dict[str, Any]:
    if not result.ok:
        return _frame(result.error or "That didn't work.", is_error=True)

    parts: list[str] = []
    if result.observation:
        parts.append(result.observation)
    # `page` is the snapshot tree; it is the bulk of the payload and reads far
    # better raw than JSON-escaped.
    page = result.payload.get("page")
    rest = {k: v for k, v in result.payload.items() if k != "page"}
    if rest:
        parts.append(json.dumps(rest, default=str))
    if page:
        parts.append(str(page))
    return _frame("\n\n".join(p for p in parts if p) or "OK")


def _to_sdk_tool(spec: Tool, registry: ToolRegistry):
    """Wrap a `Tool` in the SDK's `@tool` decorator.

    The handler invoked is `registry.wrapped(spec)`, never `spec.handler` — that
    is what guarantees every call is recorded and budget-checked regardless of
    which engine is driving.
    """
    invoke = registry.wrapped(spec)

    @tool(spec.name, spec.description, spec.input_schema)
    async def _wrapped(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _render(await invoke(args))
        except Exception as exc:  # noqa: BLE001 — never let a tool kill the run
            log.exception("tool %s failed inside the adapter", spec.name)
            return _frame(f"Error in {spec.name}: {exc}", is_error=True)

    return _wrapped


def build_mcp_server(registry: ToolRegistry):
    """In-process MCP server exposing every registered tool."""
    return create_sdk_mcp_server(
        name=MCP_SERVER_NAME,
        version="1.0.0",
        tools=[_to_sdk_tool(spec, registry) for spec in registry],
    )


def allowed_tool_names(registry: ToolRegistry) -> list[str]:
    """Fully-qualified names the SDK must permit without prompting.

    In-process MCP tools are addressed as `mcp__<server>__<tool>`.
    """
    return [f"mcp__{MCP_SERVER_NAME}__{spec.name}" for spec in registry]
