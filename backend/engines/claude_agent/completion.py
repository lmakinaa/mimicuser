"""Single-shot structured completions routed through the Claude Code CLI.

Why this exists: a subscription OAuth token is not spendable on the Messages
API. Sent as `Authorization: Bearer` with the `oauth-2025-04-20` beta header it
returns 429 on every call, regardless of model or concurrency — while the same
credential, handed to the CLI as `CLAUDE_CODE_OAUTH_TOKEN`, serves the browsing
agents all day. Evaluation was therefore the only stage of the pipeline that
could never run: sessions recorded fine and produced zero findings.

This adapter spends the credential on the path it is entitled to. There are no
tools and a single turn, so it is a completion in everything but transport, and
it satisfies the same call shape as `AnthropicCompletionClient.parse`.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import TypeVar

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from pydantic import BaseModel

from domain.completion import CompletionError, coerce_json, json_instruction
from domain.research import ModelSpec
from engines.claude_agent.credentials import build_env

log = logging.getLogger(__name__)

# Headroom for denied tool attempts before the model settles into answering.
_MAX_TURNS = 6

T = TypeVar("T", bound=BaseModel)

_MODEL_ALIASES: dict[str, str] = {
    "claude-opus-5": "opus",
    "claude-opus-4-8": "opus",
    "claude-sonnet-5": "sonnet",
    "claude-haiku-4-5": "haiku",
}

# The CLI has no structured-output surface, so conformance is a property of the
# prompt plus validation rather than of the request. Everything the customer
# agent is denied is denied here too — this call reasons over a transcript we
# supply and must not go looking for more.
_NO_TOOLS = [
    "Bash", "Read", "Write", "Edit", "Glob", "Grep",
    "WebSearch", "WebFetch", "NotebookEdit", "Task",
    # Built-ins the CLI offers even with an empty allow-list. Left enabled, the
    # model spends its turn on a tool call and the turn ends `stop_reason:
    # tool_use` with no text — an evaluation that silently produced nothing.
    "TodoWrite", "BashOutput", "KillShell", "ExitPlanMode", "SlashCommand",
]


class ClaudeAgentCompletionClient:
    """Structured completions over the Claude Code CLI, not the Messages API."""

    def __init__(
        self,
        *,
        credential_kind: str,
        secret: str,
        model: str = "claude-opus-5",
        effort: str = "high",
    ) -> None:
        if not secret:
            raise CompletionError("no Anthropic credential configured")
        self.model = model
        self.effort = effort
        self._spec = ModelSpec(
            model=model,
            engine="claude_agent",
            credential_kind=credential_kind,  # type: ignore[arg-type]
            credential_ref=secret,
            effort=effort,
        )

    async def parse(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = 16_000,
        backoff: tuple[int, ...] = (),
    ) -> T:
        """Return a validated `schema` instance.

        `max_tokens` and `backoff` are accepted for call-shape parity with the
        Messages-API client. The CLI manages its own token ceiling, and this
        path is not the one that rate-limits, so there is nothing to back off.
        """
        text = await self._complete(json_instruction(system, schema), user)
        if not text.strip():
            raise CompletionError("Claude Code CLI returned no text to parse")
        return coerce_json(text, schema)

    async def _complete(self, system: str, user: str) -> str:
        home = Path(tempfile.mkdtemp(prefix="mimic-completion-"))
        try:
            options = ClaudeAgentOptions(
                cwd=str(home),
                system_prompt=system,
                model=_MODEL_ALIASES.get(self.model, self.model),
                allowed_tools=[],
                disallowed_tools=_NO_TOOLS,
                permission_mode="bypassPermissions",
                # Never inherit the operator's CLAUDE.md, hooks or MCP servers:
                # an evaluation must depend only on the transcript it is given.
                setting_sources=[],
                env=build_env(self._spec, secret=self._spec.credential_ref or "",
                              claude_home=home / "claude"),
                effort=self.effort,
                # Not 1. A denied tool call consumes a turn on its own, and the
                # model needs at least one more to answer in text instead. One
                # turn made success depend on whether it happened to reach for a
                # tool first, which is exactly the kind of flake that looks like
                # a rate limit in the logs.
                max_turns=_MAX_TURNS,
            )
            chunks: list[str] = []
            failure: str | None = None
            async with ClaudeSDKClient(options=options) as client:
                await client.query(user)
                async for message in client.receive_response():
                    # Attribute-driven, matching the engine: SDK message classes
                    # move between versions, `.content` blocks with `.text` do not.
                    for block in getattr(message, "content", None) or []:
                        text = getattr(block, "text", None)
                        if isinstance(text, str):
                            chunks.append(text)
                    # The result message is the only place the CLI reports why a
                    # turn produced nothing; without it the caller just sees
                    # "no text" and cannot tell a rate limit from a bad prompt.
                    if getattr(message, "is_error", False) or getattr(message, "subtype", None) not in (None, "success", "init"):
                        failure = (
                            f"subtype={getattr(message, 'subtype', None)!r} "
                            f"api_error={getattr(message, 'api_error_status', None)!r} "
                            f"errors={getattr(message, 'errors', None)!r} "
                            f"stop={getattr(message, 'stop_reason', None)!r} "
                            f"result={str(getattr(message, 'result', ''))[:300]!r}"
                        )
            joined = "".join(chunks)
            if not joined.strip() and failure:
                raise CompletionError(f"Claude Code CLI returned no text — {failure}")
            return joined
        except CompletionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CompletionError(f"Claude Code completion failed: {exc}") from exc
        finally:
            shutil.rmtree(home, ignore_errors=True)
