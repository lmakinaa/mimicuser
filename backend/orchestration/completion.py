"""Non-agentic model calls: the evaluator and persona synthesis.

Deliberately separate from `AgentEngine`. These are single structured requests
with no tools and no loop — modelling them as "engines" would force every future
adapter to implement a shape it doesn't need, and would put the evaluator's
methodology behind the runtime abstraction, which is exactly the coupling the
brief asks us to avoid.

Credentials are shared with the engines: an OAuth access token rides
`Authorization: Bearer` with the `oauth-2025-04-20` beta header — never
`x-api-key`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Protocol, TypeVar

import anthropic
from pydantic import BaseModel

from domain.completion import CompletionError, coerce_json, json_instruction

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class StructuredCompletion(Protocol):
    async def parse(
        self, *, system: str, user: str, schema: type[T], max_tokens: int = ...,
        backoff: tuple[int, ...] = ...,
    ) -> T: ...

OAUTH_BETA_HEADER = "oauth-2025-04-20"
DEFAULT_MAX_TOKENS = 16_000

# Evaluation runs immediately after a long, token-heavy browsing session, which
# is exactly when a subscription is most likely to be rate-limited. The SDK's
# own retries are sub-second and don't clear that; these waits do. Losing the
# evaluation means throwing away a browser session that already cost real money,
# so it is worth waiting minutes to avoid.
_RATE_LIMIT_BACKOFF_S = (20, 60, 120)

# Persona synthesis happens inside an HTTP request while someone waits. Blocking
# a form submit for minutes to maybe avoid a fallback is a bad trade — fail fast
# and let the resolver record an honest weak match instead.
INTERACTIVE_BACKOFF_S = (4, 12)

# Adaptive thinking and `output_config.effort` are not universal: Haiku 4.5 and
# Sonnet 4.5 reject both with a 400. Sending them unconditionally made the
# cheap resolver model unusable, which is worse than the rate limit it was
# meant to dodge. Anything not listed here is called without them.
_THINKING_MODELS = frozenset({
    "claude-fable-5", "claude-mythos-5",
    "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
    "claude-sonnet-5", "claude-sonnet-4-6",
})


__all__ = [
    "AnthropicCompletionClient",
    "StructuredCompletion",
    "CompletionError",
    "build_completion_client",
    "INTERACTIVE_BACKOFF_S",
]


class AnthropicCompletionClient:
    """Structured single-shot completions against the Messages API."""

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
        self.supports_thinking = model in _THINKING_MODELS

        if credential_kind == "oauth":
            self._client = anthropic.AsyncAnthropic(
                auth_token=secret,
                default_headers={"anthropic-beta": OAUTH_BETA_HEADER},
            )
        else:
            self._client = anthropic.AsyncAnthropic(api_key=secret)

    async def parse(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = DEFAULT_MAX_TOKENS,
        backoff: tuple[int, ...] = _RATE_LIMIT_BACKOFF_S,
    ) -> T:
        """Return a validated `schema` instance.

        Primary path is structured outputs, so conformance is a property of the
        request rather than of parsing luck. The JSON-schema-in-prompt fallback
        exists because structured outputs is a comparatively new surface and we
        would rather degrade than fail a run that already cost a browser session.
        """
        try:
            response = await self._with_rate_limit_retry(
                backoff,
                lambda: self._client.messages.parse(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    output_format=schema,
                    **self._reasoning_params(),
                )
            )
            parsed = getattr(response, "parsed_output", None)
            if isinstance(parsed, schema):
                return parsed
            raise CompletionError("structured output returned nothing parseable")
        except anthropic.RateLimitError:
            # The prompted-JSON fallback would hit the same limit. Let the
            # caller record an honest "not analysed" report instead of burning
            # more quota on a request that cannot succeed.
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("structured output failed (%s); falling back to prompted JSON", exc)
            return await self._parse_via_prompt(
                system=system, user=user, schema=schema, max_tokens=max_tokens
            )

    def _reasoning_params(self) -> dict:
        if not self.supports_thinking:
            return {}
        return {"thinking": {"type": "adaptive"}, "output_config": {"effort": self.effort}}

    @staticmethod
    async def _with_rate_limit_retry(backoff: tuple[int, ...], call):
        """Retry 429s on a schedule long enough to outlast a subscription limit.

        Honours `retry-after` when the API supplies it.
        """
        last: Exception | None = None
        for attempt, wait in enumerate((*backoff, None)):
            try:
                return await call()
            except anthropic.RateLimitError as exc:
                last = exc
                if wait is None:
                    break
                retry_after = None
                response = getattr(exc, "response", None)
                if response is not None:
                    try:
                        retry_after = float(response.headers.get("retry-after", ""))
                    except (TypeError, ValueError):
                        retry_after = None
                delay = max(wait, retry_after or 0)
                log.warning(
                    "rate limited by Anthropic; retrying evaluation in %ss (attempt %d)",
                    delay,
                    attempt + 1,
                )
                await asyncio.sleep(delay)
        assert last is not None
        raise last

    async def _parse_via_prompt(
        self, *, system: str, user: str, schema: type[T], max_tokens: int
    ) -> T:
        instruction = json_instruction(system, schema)
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=instruction,
            messages=[{"role": "user", "content": user}],
            **self._reasoning_params(),
        )
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        return self._coerce(text, schema)

    @staticmethod
    def _coerce(text: str, schema: type[T]) -> T:
        return coerce_json(text, schema)


def build_completion_client(
    *,
    provider: str = "anthropic",
    credential_kind: str,
    secret: str,
    model: str,
    effort: str = "high",
):
    """Pick the transport the credential can actually pay for.

    A subscription OAuth token is rejected by the Messages API with a 429 on
    every request — not a concurrency limit, an entitlement one — so routing
    evaluation there guarantees zero findings. The Claude Code CLI accepts the
    same token and is what the browsing engines already run on. An explicit API
    key keeps the direct path, which is faster and supports structured outputs.
    """
    if provider == "openai":
        from engines.openai.completion import OpenAICompletionClient

        return OpenAICompletionClient(secret=secret, model=model, effort=effort)
    if credential_kind == "oauth":
        # Imported lazily and by module path: `claude_agent_sdk` itself must not
        # appear outside its adapter (tests/unit/test_layering.py enforces it).
        from engines.claude_agent.completion import ClaudeAgentCompletionClient

        return ClaudeAgentCompletionClient(
            credential_kind=credential_kind, secret=secret, model=model, effort=effort
        )
    return AnthropicCompletionClient(
        credential_kind=credential_kind, secret=secret, model=model, effort=effort
    )
