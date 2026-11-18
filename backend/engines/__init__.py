"""Agent engine abstraction.

`registry` imports the adapter subpackages, so importing this package is enough
to have every available engine registered.
"""
from __future__ import annotations

from engines import registry  # noqa: F401
from engines.base import AgentEngine, EventSink  # noqa: F401

__all__ = ["AgentEngine", "EventSink", "registry"]
