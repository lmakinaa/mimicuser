"""Structured, key-authenticated OpenAI completions."""
from __future__ import annotations

from typing import TypeVar

import httpx
from pydantic import BaseModel

from domain.completion import CompletionError, coerce_json, json_instruction

T = TypeVar("T", bound=BaseModel)


class OpenAICompletionClient:
    def __init__(self, *, secret: str, model: str, effort: str = "high") -> None:
        if not secret:
            raise CompletionError("no OpenAI API key configured")
        self.secret, self.model = secret, model

    async def parse(
        self, *, system: str, user: str, schema: type[T], max_tokens: int = 16_000,
        backoff: tuple[int, ...] = (),
    ) -> T:
        async with httpx.AsyncClient(timeout=httpx.Timeout(90.0)) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.secret}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": json_instruction(system, schema)},
                        {"role": "user", "content": user},
                    ],
                    "response_format": {"type": "json_object"},
                    "max_tokens": max_tokens,
                },
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise CompletionError(f"OpenAI completion failed: {response.text[:300]}") from exc
        text = ((response.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return coerce_json(text, schema)
