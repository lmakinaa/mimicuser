"""Compose ContextBlocks into the agent's system prompt.

Blocks are emitted in stable priority order specifically so the prompt prefix
is byte-identical across runs of the same persona. That is what makes Anthropic
prompt caching pay off when the same audience is fanned out across many
synthetic customers.
"""
from __future__ import annotations

from context.base import ContextBlock, ContextProvider
from domain.research import ResearchBrief


async def collect_blocks(
    brief: ResearchBrief, providers: list[ContextProvider]
) -> list[ContextBlock]:
    collected: list[ContextBlock] = []
    for provider in providers:
        collected.extend(await provider.blocks(brief))
    # Stable sort: equal priorities keep provider registration order.
    return sorted(collected, key=lambda b: b.priority)


def render(blocks: list[ContextBlock]) -> str:
    return "\n\n".join(b.body for b in blocks)
