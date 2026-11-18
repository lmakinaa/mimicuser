"""Session recording — the evidence base.

Assigns `seq`, persists each event, mirrors screenshots and the whole stream to
the artifact store. Everything downstream (evaluator, dashboard, future
aggregation) reads what this writes; nothing reads the live browser.

Persistence is injected as a callable so orchestration never touches SQL.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from domain.events import (
    AgentMessageEvent,
    AgentThoughtEvent,
    EngineErrorEvent,
    EngineEvent,
    EngineUsageEvent,
    EventType,
    SessionEvent,
    utcnow,
)
from domain.research import RunPaths
from storage.local import LocalStorageProvider
from tools.base import Tool, ToolResult

log = logging.getLogger(__name__)

PersistEvent = Callable[[SessionEvent], Awaitable[None]]

# Tools whose result carries the page snapshot. The snapshot is huge and is
# already captured as a screenshot artifact, so we keep only a digest in the
# event row and let the evaluator work from observations + screenshots.
_SNAPSHOT_TOOLS = {"look", "open_url", "click", "type_text", "select_option", "scroll", "go_back", "wait"}


class SessionRecorder:
    def __init__(
        self,
        *,
        run_id: UUID,
        paths: RunPaths,
        storage: LocalStorageProvider,
        persist: PersistEvent,
    ) -> None:
        self.run_id = run_id
        self.paths = paths
        self.storage = storage
        self._persist = persist
        self._seq = 0
        self.events: list[SessionEvent] = []
        self.current_url: str | None = None
        self.last_screenshot_key: str | None = None
        self.finish_payload: dict[str, Any] | None = None

    # -- core --------------------------------------------------------------

    async def emit(self, **fields: Any) -> SessionEvent:
        self._seq += 1
        event = SessionEvent(
            run_id=self.run_id,
            seq=self._seq,
            ts=utcnow(),
            url=fields.pop("url", self.current_url),
            **fields,
        )
        self.events.append(event)
        try:
            await self._persist(event)
        except Exception:  # noqa: BLE001 — losing a row must not kill a run
            log.exception("failed to persist event %s for run %s", event.seq, self.run_id)
        return event

    @property
    def next_seq(self) -> int:
        return self._seq + 1

    # -- screenshots -------------------------------------------------------

    async def save_screenshot(self, label: str, png: bytes) -> str:
        key = self.paths.screenshot(self.next_seq, label)
        await self.storage.write_bytes(key, png, "image/png")
        return key

    # -- typed entry points ------------------------------------------------

    async def run_started(self, meta: dict[str, Any]) -> None:
        await self.emit(type=EventType.run_started, meta=meta)

    async def record_engine_event(self, event: EngineEvent) -> None:
        match event:
            case AgentMessageEvent(text=text):
                await self.emit(type=EventType.agent_message, observation=text)
            case AgentThoughtEvent(text=text):
                await self.emit(type=EventType.agent_thought, observation=text)
            case EngineErrorEvent(message=message, fatal=fatal, status=status):
                await self.emit(
                    type=EventType.engine_error,
                    error=message,
                    meta={"fatal": fatal, "status": status},
                )
            case EngineUsageEvent():
                # Accounting only — not part of the evidence base, and emitting
                # it as an event would bury the transcript in noise.
                pass

    async def record_tool_use(
        self, tool: Tool, args: dict[str, Any], result: ToolResult, elapsed_ms: int
    ) -> None:
        """Called by `ToolRegistry` for every tool invocation, every engine."""
        await self.emit(
            type=EventType.tool_call,
            action=tool.name,
            target=args.get("ref"),
            input=self._safe_args(args),
            meta={"group": tool.group},
        )

        url = result.payload.get("url")
        if url:
            if url != self.current_url:
                self.current_url = url
                await self.emit(type=EventType.navigation, url=url, action=tool.name)
            self.current_url = url

        screenshot_key = next(
            (a.key for a in result.artifacts if a.kind == "screenshot"), None
        )
        if screenshot_key:
            self.last_screenshot_key = screenshot_key

        if tool.name == "note":
            await self.emit(
                type=EventType.note,
                action="note",
                observation=result.observation,
                # A reaction is only evidence if you can see what provoked it.
                # `note` takes no screenshot of its own, so it inherits the
                # screen the customer was looking at when they said it.
                screenshot_key=self.last_screenshot_key,
                meta={
                    "sentiment": result.payload.get("sentiment"),
                    "about": result.payload.get("about"),
                },
            )
            return

        if tool.name == "finish":
            self.finish_payload = dict(result.payload)
            await self.emit(
                type=EventType.session_finished,
                action="finish",
                observation=result.observation,
                result=dict(result.payload),
            )
            return

        await self.emit(
            type=EventType.tool_result,
            action=tool.name,
            target=args.get("ref"),
            observation=result.observation,
            result=self._safe_result(tool, result),
            screenshot_key=screenshot_key,
            error=result.error,
            meta={"ok": result.ok, "elapsed_ms": elapsed_ms},
        )

    async def run_completed(self, meta: dict[str, Any]) -> None:
        await self.emit(type=EventType.run_completed, meta=meta)

    # -- output ------------------------------------------------------------

    async def flush(self) -> str:
        payload = [e.model_dump(mode="json") for e in self.events]
        return await self.storage.write_json(self.paths.events, payload)

    def transcript(self) -> str:
        return "\n".join(e.to_transcript_line() for e in self.events)

    def valid_seqs(self) -> set[int]:
        return {e.seq for e in self.events}

    # -- redaction ---------------------------------------------------------

    @staticmethod
    def _safe_args(args: dict[str, Any]) -> dict[str, Any]:
        """Never persist what the customer typed into a password field.

        The agent sometimes invents credentials during sign-up; storing them
        verbatim in an append-only table is a liability with no research value.
        """
        redacted = dict(args)
        text = redacted.get("text")
        if isinstance(text, str) and _looks_secret(text):
            redacted["text"] = "«redacted»"
        return redacted

    @staticmethod
    def _safe_result(tool: Tool, result: ToolResult) -> dict[str, Any] | None:
        if tool.name in _SNAPSHOT_TOOLS:
            page = result.payload.get("page")
            return {
                "url": result.payload.get("url"),
                "title": result.payload.get("title"),
                "page_chars": len(page) if isinstance(page, str) else 0,
            }
        return dict(result.payload) or None


_SECRET_HINTS = ("password", "passw0rd", "secret")


def _looks_secret(text: str) -> bool:
    lowered = text.lower()
    if any(h in lowered for h in _SECRET_HINTS):
        return True
    # Heuristic: medium-length strings mixing case, digits and symbols are far
    # more likely to be a generated password than something a customer typed.
    if 8 <= len(text) <= 64:
        classes = sum(
            [
                any(c.islower() for c in text),
                any(c.isupper() for c in text),
                any(c.isdigit() for c in text),
                any(not c.isalnum() for c in text),
            ]
        )
        return classes >= 3 and " " not in text
    return False
