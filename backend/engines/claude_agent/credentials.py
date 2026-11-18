"""Resolve a `ModelSpec` credential into the agent subprocess environment.

The Claude Agent SDK spawns the Claude Code CLI as a subprocess, so the
credential is handed over through the environment and nothing else.

Exactly one credential is ever set. Both together is a rejected configuration,
not a precedence question — silently preferring one over the other is how you
get a run billed to the wrong account.

Refresh never happens here: it happens in the API/worker process before the run
starts. The subprocess receives a short-lived access token and has no path to a
refresh token.
"""
from __future__ import annotations

from pathlib import Path

from domain.research import ModelSpec


class CredentialError(RuntimeError):
    """The run cannot be authenticated. Fails the run, never falls back."""


def build_env(model: ModelSpec, *, secret: str, claude_home: Path) -> dict[str, str]:
    if not secret:
        raise CredentialError(
            f"no {model.credential_kind} credential available — link an Anthropic "
            "account via /v1/auth/anthropic/start, or set ANTHROPIC_API_KEY"
        )

    env: dict[str, str] = {
        # Replaces ~/.claude so CLI state never leaks between runs or into the
        # operator's own profile.
        "CLAUDE_CONFIG_DIR": str(claude_home),
        # This is a customer simulation, not a coding session — the CLI's own
        # memory injection would contaminate the persona.
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_USE_FOUNDRY": "",
    }

    if model.credential_kind == "oauth":
        env["CLAUDE_CODE_OAUTH_TOKEN"] = secret
    elif model.credential_kind == "api_key":
        env["ANTHROPIC_API_KEY"] = secret
    else:  # pragma: no cover — enum-guarded upstream
        raise CredentialError(f"unknown credential kind {model.credential_kind!r}")

    return env
