"""ContextProvider — how the agent learns about its assignment.

No vector database in the MVP, per the brief. The protocol exists so semantic
retrieval is a new implementation rather than a new shape: a future
`VectorProvider` returns `ContextBlock`s like everything else and the composer
does not learn anything new.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from domain.research import ResearchBrief


@dataclass(frozen=True)
class ContextBlock:
    title: str
    body: str
    priority: int


@runtime_checkable
class ContextProvider(Protocol):
    name: str

    async def blocks(self, brief: ResearchBrief) -> list[ContextBlock]: ...
