"""The browser tools a synthetic customer gets.

Handle-based only — no coordinates, no selectors. Coordinate clicking produces
brittle, unreplayable traces and destroys the evidentiary value of a session,
which is the one thing this product sells.

A stale handle returns a friendly failure rather than an exception, because the
customer's experience of "the thing I was about to click disappeared" is a real
observation worth recording.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from playwright.async_api import Error as PlaywrightError

from tools.base import ArtifactRef, Tool, ToolResult
from tools.browser.session import BrowserSession, NavigationRefused

ScreenshotSaver = Callable[[str], Awaitable[str]]
"""Given a label, persist the current screenshot and return its storage key."""

_STALE = (
    "I can't find that on the page any more — it may have changed since you last "
    "looked. Use `look` to see what's there now."
)

MAX_WAIT_SECONDS = 10


def _obj(**props: Any) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": [], "additionalProperties": False}


def _required(schema: dict[str, Any], *names: str) -> dict[str, Any]:
    schema["required"] = list(names)
    return schema


def build_browser_tools(session: BrowserSession, save_screenshot: ScreenshotSaver) -> list[Tool]:
    async def _observe(label: str) -> ToolResult:
        try:
            snap = await session.snapshot()
        except RuntimeError as exc:
            return ToolResult.failure(str(exc))

        key = await save_screenshot(label)
        body = snap.tree or "(the page appears to be empty)"
        if snap.truncated:
            body += "\n\n… the page is longer than this. Scroll to see more."

        scroll = snap.scroll or {}
        position = ""
        if scroll.get("height") and scroll.get("viewport"):
            at_bottom = scroll["y"] + scroll["viewport"] >= scroll["height"] - 8
            position = " (at the bottom of the page)" if at_bottom else ""

        return ToolResult(
            ok=True,
            payload={"url": snap.url, "title": snap.title, "page": body},
            observation=f"Looked at {snap.title or 'the page'} — {snap.url}{position}",
            artifacts=(ArtifactRef(key=key, kind="screenshot"),),
        )

    async def look(_: dict[str, Any]) -> ToolResult:
        return await _observe("look")

    async def open_url(args: dict[str, Any]) -> ToolResult:
        url = (args.get("url") or "").strip()
        if not url:
            return ToolResult.failure("You need to give me a URL to open.")
        try:
            await session.goto(url)
        except NavigationRefused as exc:
            return ToolResult.failure(str(exc))
        except PlaywrightError as exc:
            return ToolResult.failure(f"That page didn't load: {exc}")
        return await _observe("open")

    async def click(args: dict[str, Any]) -> ToolResult:
        ref = args.get("ref")
        if not ref:
            return ToolResult.failure("Which element? Give me a ref like `e12`.")
        try:
            await session.locator(ref).first.click(timeout=8_000)
        except PlaywrightError as exc:
            return ToolResult.failure(_STALE if "strict mode" not in str(exc) else str(exc))
        return await _observe(f"click-{ref}")

    async def type_text(args: dict[str, Any]) -> ToolResult:
        ref, text = args.get("ref"), args.get("text", "")
        if not ref:
            return ToolResult.failure("Which field? Give me a ref like `e12`.")
        try:
            locator = session.locator(ref).first
            await locator.fill(str(text), timeout=8_000)
            if args.get("submit"):
                await locator.press("Enter")
        except PlaywrightError:
            return ToolResult.failure(_STALE)
        return await _observe(f"type-{ref}")

    async def select_option(args: dict[str, Any]) -> ToolResult:
        ref, value = args.get("ref"), args.get("value")
        if not ref or value is None:
            return ToolResult.failure("I need both a ref and the option to choose.")
        try:
            await session.locator(ref).first.select_option(label=str(value), timeout=8_000)
        except PlaywrightError:
            try:
                await session.locator(ref).first.select_option(value=str(value), timeout=8_000)
            except PlaywrightError:
                return ToolResult.failure(
                    f"I couldn't pick {value!r} — it may not be one of the options."
                )
        return await _observe(f"select-{ref}")

    async def scroll(args: dict[str, Any]) -> ToolResult:
        direction = (args.get("direction") or "down").lower()
        amount = int(args.get("amount") or 600)
        delta = amount if direction == "down" else -amount
        try:
            await session.page.mouse.wheel(0, delta)
        except PlaywrightError as exc:
            return ToolResult.failure(f"Couldn't scroll: {exc}")
        return await _observe(f"scroll-{direction}")

    async def go_back(_: dict[str, Any]) -> ToolResult:
        try:
            await session.page.go_back(wait_until="domcontentloaded")
        except PlaywrightError:
            return ToolResult.failure("There's nowhere to go back to.")
        return await _observe("back")

    async def wait(args: dict[str, Any]) -> ToolResult:
        seconds = min(float(args.get("seconds") or 2), MAX_WAIT_SECONDS)
        await asyncio.sleep(seconds)
        result = await _observe("wait")
        if result.ok:
            return ToolResult(
                ok=True,
                payload=result.payload,
                observation=f"Waited {seconds:g}s to see if it would finish loading. "
                + (result.observation or ""),
                artifacts=result.artifacts,
            )
        return result

    return [
        Tool(
            name="look",
            description=(
                "Look at the page you're on. Returns what's visible, with a handle "
                "like [e12] for everything you can interact with. Use this whenever "
                "you need to see where you are — it's the only way to see the page."
            ),
            input_schema=_obj(),
            handler=look,
        ),
        Tool(
            name="open_url",
            description="Go to a web address.",
            input_schema=_required(
                _obj(url={"type": "string", "description": "Full http(s) address"}), "url"
            ),
            handler=open_url,
        ),
        Tool(
            name="click",
            description="Click something. Use the handle from your last look, e.g. e12.",
            input_schema=_required(
                _obj(ref={"type": "string", "description": "Element handle, e.g. e12"}), "ref"
            ),
            handler=click,
        ),
        Tool(
            name="type_text",
            description=(
                "Type into a field. Set submit=true to press Enter afterwards, which "
                "is usually what you want for a search box."
            ),
            input_schema=_required(
                _obj(
                    ref={"type": "string", "description": "Field handle, e.g. e12"},
                    text={"type": "string"},
                    submit={"type": "boolean", "description": "Press Enter after typing"},
                ),
                "ref",
                "text",
            ),
            handler=type_text,
        ),
        Tool(
            name="select_option",
            description="Choose an option from a dropdown.",
            input_schema=_required(
                _obj(
                    ref={"type": "string"},
                    value={"type": "string", "description": "The option's visible label"},
                ),
                "ref",
                "value",
            ),
            handler=select_option,
        ),
        Tool(
            name="scroll",
            description="Scroll the page up or down.",
            input_schema=_required(
                _obj(
                    direction={"type": "string", "enum": ["up", "down"]},
                    amount={"type": "integer", "description": "Pixels, default 600"},
                ),
                "direction",
            ),
            handler=scroll,
        ),
        Tool(
            name="go_back",
            description="Go back to the previous page.",
            input_schema=_obj(),
            handler=go_back,
        ),
        Tool(
            name="wait",
            description=(
                "Wait a few seconds and look again — for when something seems to be "
                f"loading. Maximum {MAX_WAIT_SECONDS} seconds."
            ),
            input_schema=_required(_obj(seconds={"type": "number"}), "seconds"),
            handler=wait,
        ),
    ]
