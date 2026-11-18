"""OpenAI Chat Completions adapter with the platform's tool registry."""
from __future__ import annotations

import json
import logging
import time
from uuid import UUID

import httpx

from domain.events import AgentMessageEvent, EngineErrorEvent, EngineOutcome, EngineUsageEvent
from domain.research import ModelSpec, RunBudget, RuntimeConfig
from engines.base import EventSink
from tools.base import ToolRegistry

log = logging.getLogger(__name__)


class OpenAIEngine:
    name = "openai"

    def supports(self, model: ModelSpec) -> bool:
        return (model.provider or "").lower() == "openai"

    async def run(
        self, *, run_id: UUID, system_prompt: str, opening_message: str,
        tools: ToolRegistry, model: ModelSpec, budget: RunBudget,
        config: RuntimeConfig, emit: EventSink,
    ) -> EngineOutcome:
        started = time.perf_counter()
        outcome = EngineOutcome()
        secret = model.credential_ref or ""
        if not secret:
            outcome.is_error, outcome.stop_reason, outcome.error = True, "engine_error", "no OpenAI API key configured"
            await emit(EngineErrorEvent(message=outcome.error, fatal=True))
            return outcome

        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": opening_message},
        ]
        tool_specs = [
            {"type": "function", "function": {"name": spec.name, "description": spec.description, "parameters": spec.input_schema}}
            for spec in tools
        ]
        deadline = time.monotonic() + budget.max_wall_seconds
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(90.0)) as client:
                while outcome.turns < budget.max_tool_calls + 10:
                    if time.monotonic() > deadline:
                        outcome.stop_reason = "wall_clock_budget"
                        break
                    if tools.check_cancelled():
                        outcome.stop_reason = "cancelled"
                        break
                    if tools.finished:
                        outcome.stop_reason = "customer_finished"
                        break
                    response = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {secret}"},
                        json={"model": model.model, "messages": messages, "tools": tool_specs, "tool_choice": "auto", "temperature": 0.7},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    usage = payload.get("usage") or {}
                    tin, tout = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
                    outcome.tokens_in += tin
                    outcome.tokens_out += tout
                    if tin or tout:
                        await emit(EngineUsageEvent(tokens_in=tin, tokens_out=tout))
                    message = (payload.get("choices") or [{}])[0].get("message") or {}
                    content = message.get("content")
                    if content:
                        outcome.final_text = content
                        await emit(AgentMessageEvent(text=content))
                    calls = message.get("tool_calls") or []
                    outcome.turns += 1
                    if not calls:
                        outcome.stop_reason = "end_turn"
                        break
                    messages.append({"role": "assistant", "content": content, "tool_calls": calls})
                    for call in calls:
                        fn = call.get("function") or {}
                        name = fn.get("name", "")
                        try:
                            args = json.loads(fn.get("arguments") or "{}")
                            spec = tools.get(name)
                            result = await tools.wrapped(spec)(args)
                            text = _render_tool_result(result)
                        except Exception as exc:  # noqa: BLE001
                            log.exception("OpenAI tool %s failed", name)
                            text = f"Tool error: {exc}"
                        messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": text})
                else:
                    outcome.stop_reason = "tool_call_budget"
        except Exception as exc:  # noqa: BLE001
            log.exception("run %s: OpenAI engine failed", run_id)
            outcome.is_error, outcome.error, outcome.stop_reason = True, str(exc), "engine_error"
            await emit(EngineErrorEvent(message=str(exc), fatal=True))
        outcome.duration_ms = int((time.perf_counter() - started) * 1000)
        return outcome


def _render_tool_result(result) -> str:
    if not result.ok:
        return result.error or "That didn't work."
    parts = [result.observation] if result.observation else []
    page = result.payload.get("page")
    rest = {k: v for k, v in result.payload.items() if k != "page"}
    if rest:
        parts.append(json.dumps(rest, default=str))
    if page:
        parts.append(str(page))
    return "\n\n".join(parts) or "OK"
