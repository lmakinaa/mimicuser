"""The two tools that make this a research instrument, not a test runner.

`note` lets the customer say what they're feeling *while* it happens, attached
to the page it happened on. `finish` lets them stop for any reason a real person
stops — including losing interest.

Neither tool asks the agent to diagnose anything. Turning reactions into
findings is the evaluator's job, and keeping the roles apart is what stops the
customer from breaking character to write a UX report mid-session.
"""
from __future__ import annotations

from typing import Any

from tools.base import Tool, ToolResult

SENTIMENTS = ["confused", "frustrated", "hesitant", "surprised", "reassured", "pleased", "neutral"]

OUTCOMES = {
    "accomplished": "You did what you came to do.",
    "partially": "You got part of the way and stopped.",
    "gave_up": "You couldn't work it out, or it wasn't worth the effort.",
    "blocked": "Something stopped you that you couldn't get past.",
    "not_interested": "You worked out this isn't for you.",
    "satisfied_exploring": "You've seen enough to form an opinion.",
}


def build_research_tools() -> list[Tool]:
    async def note(args: dict[str, Any]) -> ToolResult:
        reaction = (args.get("reaction") or "").strip()
        if not reaction:
            return ToolResult.failure("Tell me what you're reacting to.")
        sentiment = (args.get("sentiment") or "neutral").lower()
        if sentiment not in SENTIMENTS:
            sentiment = "neutral"

        # `about` scopes the reaction (a label, a price, a whole page) so the
        # evaluator can attribute it without guessing.
        return ToolResult(
            ok=True,
            payload={
                "recorded": True,
                "sentiment": sentiment,
                "about": args.get("about"),
            },
            observation=reaction,
        )

    async def finish(args: dict[str, Any]) -> ToolResult:
        outcome = (args.get("outcome") or "").strip()
        if outcome not in OUTCOMES:
            return ToolResult.failure(
                f"outcome must be one of: {', '.join(OUTCOMES)}"
            )
        reason = (args.get("reason") or "").strip()
        if not reason:
            return ToolResult.failure("Say why you're stopping.")

        return ToolResult(
            ok=True,
            terminal=True,
            payload={
                "outcome": outcome,
                "reason": reason,
                "would_continue": bool(args.get("would_continue")),
                "would_recommend": args.get("would_recommend"),
            },
            observation=f"Finished — {outcome}: {reason}",
        )

    outcome_help = "\n".join(f"  - {k}: {v}" for k, v in OUTCOMES.items())

    return [
        Tool(
            name="note",
            group="research",
            description=(
                "Say what you're thinking or feeling right now, in your own words. "
                "Use it whenever something confuses you, annoys you, surprises you, "
                "reassures you, or makes you hesitate — and when something works "
                "well too. Short and first-person. These reactions are the most "
                "valuable thing you produce, so record them as they happen rather "
                "than saving them for the end."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "reaction": {
                        "type": "string",
                        "description": "First person, e.g. \"I don't know what a Workspace is\"",
                    },
                    "sentiment": {"type": "string", "enum": SENTIMENTS},
                    "about": {
                        "type": "string",
                        "description": "What you're reacting to — a button, a price, the page",
                    },
                },
                "required": ["reaction", "sentiment"],
                "additionalProperties": False,
            },
            handler=note,
        ),
        Tool(
            name="finish",
            group="research",
            description=(
                "Stop the session. Use this the moment you'd genuinely stop in real "
                "life — including if you've lost interest or decided this isn't for "
                f"you. Those are real answers, not failures.\n\n{outcome_help}"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "outcome": {"type": "string", "enum": list(OUTCOMES)},
                    "reason": {
                        "type": "string",
                        "description": "Why you're stopping, in your own words",
                    },
                    "would_continue": {
                        "type": "boolean",
                        "description": "Would you keep using this product after today?",
                    },
                    "would_recommend": {
                        "type": "boolean",
                        "description": "Would you mention it to someone like you?",
                    },
                },
                "required": ["outcome", "reason", "would_continue"],
                "additionalProperties": False,
            },
            handler=finish,
        ),
    ]
