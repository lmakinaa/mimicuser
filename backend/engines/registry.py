"""Engine registry — `register`, `get`, `for_model`.

Resolution: explicit override → `ModelSpec.engine` → provider default → **raise**.

There is deliberately no universal fallback engine. The internal reference has
one (`_DEFAULT_ENGINE = "claude"`) and its own debt document explains why it is
wrong: an unrecognised provider silently routes to an engine that cannot serve
it, and nothing catches the mistake. An unroutable model is a configuration bug
and should say so.
"""
from __future__ import annotations

import logging

from domain.research import ModelSpec

from .base import AgentEngine

log = logging.getLogger(__name__)

_ENGINES: dict[str, AgentEngine] = {}

# Provider → engine, used only when ModelSpec.engine is unset.
_PROVIDER_DEFAULTS: dict[str, str] = {"anthropic": "claude_agent", "openai": "openai"}


class UnknownEngine(KeyError):
    """No engine registered under that name, or no default for the provider."""


class UnsupportedModel(ValueError):
    """The resolved engine exists but declines this model."""


def register(engine: AgentEngine) -> None:
    existing = _ENGINES.get(engine.name)
    if existing is not None and existing is not engine:
        raise RuntimeError(
            f"engine name {engine.name!r} already registered by {type(existing).__name__}"
        )
    _ENGINES[engine.name] = engine


def get(name: str) -> AgentEngine:
    try:
        return _ENGINES[name]
    except KeyError as exc:
        raise UnknownEngine(
            f"no engine registered as {name!r} (known: {sorted(_ENGINES)})"
        ) from exc


def registered_names() -> list[str]:
    return sorted(_ENGINES)


def for_model(model: ModelSpec, *, override: str | None = None) -> AgentEngine:
    if override:
        engine = get(override)
    elif model.engine:
        engine = get(model.engine)
    else:
        provider = (model.provider or "").lower()
        if provider not in _PROVIDER_DEFAULTS:
            raise UnknownEngine(
                f"provider {provider!r} has no default engine; set ModelSpec.engine "
                f"explicitly (known engines: {sorted(_ENGINES)})"
            )
        engine = get(_PROVIDER_DEFAULTS[provider])

    if not engine.supports(model):
        raise UnsupportedModel(
            f"engine {engine.name!r} does not support model {model.model!r} "
            f"(provider={model.provider!r})"
        )
    return engine


def _reset_for_tests() -> None:
    _ENGINES.clear()


def _bootstrap() -> None:
    """Register every adapter whose optional dependencies are present.

    Guarding per-adapter means a deployment missing one SDK still starts, and
    fails only when a run actually asks for that engine.
    """
    try:
        from engines.claude_agent.engine import ClaudeAgentEngine

        register(ClaudeAgentEngine())
    except ImportError as exc:  # pragma: no cover
        log.warning("claude_agent engine unavailable (%s); not registered", exc)
    from engines.openai.engine import OpenAIEngine
    register(OpenAIEngine())


_bootstrap()
