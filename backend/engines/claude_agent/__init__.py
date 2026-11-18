"""Claude Agent SDK engine.

Registration is done by `engines.registry`, not here: a subpackage importing the
registry that imports the subpackage is a cycle, and cycles that happen to work
today break on the next import-order change.
"""
from __future__ import annotations

from engines.claude_agent.engine import ClaudeAgentEngine

__all__ = ["ClaudeAgentEngine"]
