"""Claude Agent SDK adapter.

The SDK owns the agent loop. We give it a system prompt, an in-process MCP
server carrying our tools, and a credential; it drives the conversation and
calls tools until the customer finishes.

This module is the *only* place in the codebase that may import
`claude_agent_sdk` (proved by `tests/unit/test_layering.py`).
"""
from __future__ import annotations

import asyncio
import logging
import time
from uuid import UUID

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from domain.events import (
    AgentMessageEvent,
    AgentThoughtEvent,
    EngineErrorEvent,
    EngineOutcome,
    EngineUsageEvent,
)
from domain.research import ModelSpec, RunBudget, RuntimeConfig
from engines.base import EventSink
from engines.claude_agent.credentials import build_env
from engines.claude_agent.tools_adapter import (
    MCP_SERVER_NAME,
    allowed_tool_names,
    build_mcp_server,
)
from tools.base import ToolRegistry

log = logging.getLogger(__name__)

# Upstream model id → CLI alias where one exists. Unknown ids pass through, so
# a newly released model works without a code change.
_MODEL_ALIASES: dict[str, str] = {
    "claude-opus-5": "opus",
    "claude-opus-4-8": "opus",
    "claude-sonnet-5": "sonnet",
    "claude-haiku-4-5": "haiku",
}

_SUPPORTED_PROVIDERS = {"anthropic"}
_POLL_TICK_S = 0.5


class ClaudeAgentEngine:
    name = "claude_agent"

    def supports(self, model: ModelSpec) -> bool:
        # Honest, not universal: this adapter speaks to the Claude Code CLI,
        # which is Anthropic-only. A second engine can claim other providers
        # without this one having to be corrected first.
        return (model.provider or "").lower() in _SUPPORTED_PROVIDERS

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
        started = time.perf_counter()
        outcome = EngineOutcome()

        claude_home = config.run_home / "claude"
        claude_home.mkdir(parents=True, exist_ok=True)

        env = build_env(model, secret=model.credential_ref or "", claude_home=claude_home)
        mcp_server = build_mcp_server(tools)
        allowed = allowed_tool_names(tools)

        options = ClaudeAgentOptions(
            cwd=str(config.run_home),
            system_prompt=system_prompt,
            model=_MODEL_ALIASES.get(model.model, model.model),
            mcp_servers={MCP_SERVER_NAME: mcp_server},
            allowed_tools=allowed,
            # The customer has a browser, not a computer. No file access, no
            # shell, no web search — the product under study is the only source
            # of information, which is what makes the findings mean anything.
            disallowed_tools=[
                "Bash", "Read", "Write", "Edit", "Glob", "Grep",
                "WebSearch", "WebFetch", "NotebookEdit", "Task",
            ],
            permission_mode="bypassPermissions",
            # No project/user settings: a run must not inherit the operator's
            # CLAUDE.md, hooks, or MCP servers, or the simulation is polluted.
            setting_sources=[],
            env=env,
            effort=model.effort,
            max_turns=budget.max_tool_calls + 10,
            # SDK-side cost ceiling, belt-and-braces with our own wall clock.
            max_budget_usd=budget.max_cost_usd,
        )

        deadline = time.monotonic() + budget.max_wall_seconds

        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(opening_message)

                stream = client.receive_response().__aiter__()
                task = asyncio.ensure_future(stream.__anext__())

                while True:
                    if time.monotonic() > deadline:
                        outcome.stop_reason = "wall_clock_budget"
                        task.cancel()
                        break
                    if tools.check_cancelled():
                        outcome.stop_reason = "cancelled"
                        task.cancel()
                        break
                    if tools.finished:
                        outcome.stop_reason = "customer_finished"
                        task.cancel()
                        break
                    if tools.budget_exhausted and outcome.stop_reason == "unknown":
                        # Don't cut the model off mid-thought — it has been told
                        # to wrap up, and its closing words are worth having.
                        outcome.stop_reason = "tool_call_budget"

                    done, _ = await asyncio.wait({task}, timeout=_POLL_TICK_S)
                    if not done:
                        continue

                    try:
                        message = done.pop().result()
                    except StopAsyncIteration:
                        if outcome.stop_reason == "unknown":
                            outcome.stop_reason = "end_turn"
                        break
                    except asyncio.CancelledError:
                        break
                    except Exception as exc:  # noqa: BLE001
                        log.exception("run %s: SDK stream error", run_id)
                        outcome.is_error = True
                        outcome.error = str(exc)
                        outcome.stop_reason = "engine_error"
                        await emit(EngineErrorEvent(message=str(exc), fatal=True))
                        break

                    await self._translate(message, outcome, emit)
                    task = asyncio.ensure_future(stream.__anext__())

        except Exception as exc:  # noqa: BLE001 — engine faults are run failures
            log.exception("run %s: claude_agent engine failed", run_id)
            outcome.is_error = True
            outcome.error = str(exc)
            outcome.stop_reason = "engine_error"
            await emit(EngineErrorEvent(message=str(exc), fatal=True))

        outcome.duration_ms = int((time.perf_counter() - started) * 1000)
        return outcome

    # -- SDK message → engine events --------------------------------------

    async def _translate(self, message: object, outcome: EngineOutcome, emit: EventSink) -> None:
        """Map one SDK message onto the frozen engine event contract.

        Deliberately attribute-driven rather than isinstance-driven: the SDK's
        message classes move between versions, but `content` blocks carrying
        `.text`/`.thinking` and a result carrying `.usage` have been stable.
        """
        handle = getattr(message, "session_id", None)
        if handle and not outcome.engine_session_handle:
            outcome.engine_session_handle = handle

        usage = getattr(message, "usage", None)
        if usage is not None:
            read = usage.get if isinstance(usage, dict) else lambda k, d=None: getattr(usage, k, d)
            tin = int(read("input_tokens", 0) or 0)
            tout = int(read("output_tokens", 0) or 0)
            outcome.tokens_in += tin
            outcome.tokens_out += tout
            outcome.cache_read_tokens += int(read("cache_read_input_tokens", 0) or 0)
            outcome.cache_creation_tokens += int(read("cache_creation_input_tokens", 0) or 0)
            if tin or tout:
                await emit(EngineUsageEvent(tokens_in=tin, tokens_out=tout))

        # ResultMessage carries the authoritative end-of-run accounting.
        cost = getattr(message, "total_cost_usd", None)
        if isinstance(cost, (int, float)):
            outcome.cost_usd = float(cost)
        num_turns = getattr(message, "num_turns", None)
        if isinstance(num_turns, int):
            outcome.turns = num_turns
        sdk_stop = getattr(message, "stop_reason", None) or getattr(
            message, "terminal_reason", None
        )
        if sdk_stop:
            outcome.meta["sdk_stop_reason"] = str(sdk_stop)
        if getattr(message, "is_error", False):
            outcome.is_error = True
            errors = getattr(message, "errors", None)
            outcome.error = str(errors or getattr(message, "result", "") or "SDK reported an error")

        content = getattr(message, "content", None)
        if not content:
            return

        outcome.turns += 1
        for block in content:
            text = getattr(block, "text", None)
            thinking = getattr(block, "thinking", None)
            if thinking:
                await emit(AgentThoughtEvent(text=str(thinking)))
            elif text:
                outcome.final_text = str(text)
                await emit(AgentMessageEvent(text=str(text)))
