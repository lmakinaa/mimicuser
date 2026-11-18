"""The four MVP context providers."""
from __future__ import annotations

from context.base import ContextBlock
from domain.persona import render_persona_block
from domain.research import ResearchBrief


class PersonaProvider:
    name = "persona"

    async def blocks(self, brief: ResearchBrief) -> list[ContextBlock]:
        return [
            ContextBlock(
                title="Who you are",
                body=render_persona_block(brief.customer),
                priority=10,
            )
        ]


class ProductProvider:
    name = "product"

    async def blocks(self, brief: ResearchBrief) -> list[ContextBlock]:
        # Deliberately minimal. If the operator gave no description we do NOT
        # scrape one: what the customer works out for themselves about the
        # product is one of the most valuable things a run produces.
        lines = [f"You are about to visit: {brief.product_url}"]
        if brief.product_description:
            lines.append(
                "Context you happen to have (e.g. from the link that brought you "
                f"here): {brief.product_description}"
            )
        else:
            lines.append(
                "You know nothing else about it yet. Work out what it is from the "
                "product itself."
            )
        return [
            ContextBlock(title="The product", body="\n\n".join(lines), priority=20)
        ]


class TaskProvider:
    name = "task"

    async def blocks(self, brief: ResearchBrief) -> list[ContextBlock]:
        if brief.is_directed and brief.task:
            body = (
                f"Someone has asked you to try to do this specific thing:\n\n"
                f"    {brief.task}\n\n"
                "Pursue it — but stay yourself while you do. Your reactions, "
                "confusions and doubts still matter, and if you would genuinely "
                "give up on this in real life, give up and say why."
            )
        else:
            body = (
                "Nobody has told you what to do here. Decide for yourself what "
                "someone like you would actually want from a product like this, "
                "then go after it. If the honest answer turns out to be 'nothing, "
                "this isn't for me', that is a real and useful outcome."
            )
        return [ContextBlock(title="Why you are here", body=body, priority=30)]


class WorkspaceProvider:
    """Operator-supplied notes attached to the project or run."""

    name = "workspace"

    async def blocks(self, brief: ResearchBrief) -> list[ContextBlock]:
        if not brief.workspace_notes:
            return []
        body = "\n".join(f"- {n}" for n in brief.workspace_notes)
        return [
            ContextBlock(title="Additional context", body=body, priority=40)
        ]


DEFAULT_PROVIDERS = [
    PersonaProvider(),
    ProductProvider(),
    TaskProvider(),
    WorkspaceProvider(),
]
