"""Shared vocabulary for single-shot structured completions.

Lives in `domain` because both sides of the completion boundary need it: the
Messages-API client in `orchestration`, and the Claude Code adapter in
`engines/claude_agent` — which may not import `orchestration`, and vice versa.
Nothing here talks to a provider; it is the contract, not a transport.
"""
from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class CompletionError(RuntimeError):
    pass


def json_instruction(system: str, schema: type[BaseModel]) -> str:
    """System prompt asking for one schema-conformant JSON object and nothing else."""
    return (
        f"{system}\n\n# Output format\n\nReply with a single JSON object and "
        f"nothing else — no prose, no markdown fence. It must validate "
        f"against this JSON Schema:\n\n"
        f"{json.dumps(schema.model_json_schema(), indent=2)}"
    )


def coerce_json(text: str, schema: type[T]) -> T:
    """Validate `text` against `schema`, tolerating a fence or surrounding prose."""
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1).strip()
    else:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            candidate = candidate[start : end + 1]
    try:
        return schema.model_validate_json(candidate)
    except ValidationError as exc:
        raise CompletionError(
            f"model output did not match {schema.__name__}: {exc}"
        ) from exc
