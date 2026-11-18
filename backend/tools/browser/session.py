"""Playwright lifecycle for one research run.

One browser context per run, with video recording on and a fresh profile —
"what a first-time visitor sees" is only true if no state carries over.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError

from tools.browser.netguard import (
    BlockedDestination,
    check_destination,
    route_guard,
    websocket_guard,
)
from tools.browser.settle import is_transient, settle

log = logging.getLogger(__name__)

SNAPSHOT_JS = (Path(__file__).parent / "snapshot.js").read_text(encoding="utf-8")


@dataclass(frozen=True)
class Snapshot:
    url: str
    title: str
    tree: str
    refs: dict[str, dict]
    truncated: bool
    scroll: dict


class NavigationRefused(RuntimeError):
    """The agent tried to leave the allowed surface."""


class BrowserSession:
    def __init__(
        self,
        *,
        video_dir: Path,
        headless: bool = True,
        viewport: tuple[int, int] = (1280, 900),
        allowed_host: str | None = None,
        enforce_egress: bool = True,
    ) -> None:
        self._video_dir = video_dir
        self._headless = headless
        self._viewport = viewport
        self._allowed_host = allowed_host
        self._enforce_egress = enforce_egress

        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self._video_dir.mkdir(parents=True, exist_ok=True)
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self._headless)
        self._context = await self._browser.new_context(
            viewport={"width": self._viewport[0], "height": self._viewport[1]},
            record_video_dir=str(self._video_dir),
            record_video_size={"width": self._viewport[0], "height": self._viewport[1]},
            # A synthetic customer is a new visitor. No storage state, ever.
            locale="en-US",
            timezone_id="UTC",
            # Nothing here needs one, and their fetches have historically sat
            # outside request routing.
            service_workers="block",
        )
        self._context.set_default_timeout(15_000)
        # Every request the page makes — redirects, link clicks, page fetch() —
        # not only the URLs we navigate to, must go to a public address.
        if self._enforce_egress:
            await self._context.route("**/*", route_guard())
            # WebSockets are routed by a separate API; without this they would
            # reach any address the page names.
            await self._context.route_web_socket("**", websocket_guard())
        self._page = await self._context.new_page()

    async def close(self) -> Path | None:
        """Close everything and return the recorded video path, if any."""
        video_path: Path | None = None
        try:
            # Playwright only finalises the recording once the page is closed,
            # so grab the handle first and read its path afterwards.
            video = self._page.video if self._page and not self._page.is_closed() else None
            if self._context is not None:
                await self._context.close()
            if video is not None:
                raw = await video.path()
                video_path = Path(raw) if raw else None
        except PlaywrightError as exc:
            log.warning("closing browser context: %s", exc)
        finally:
            try:
                if self._browser:
                    await self._browser.close()
                if self._pw:
                    await self._pw.stop()
            except PlaywrightError as exc:
                log.warning("stopping playwright: %s", exc)
        return video_path

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("BrowserSession.start() has not been called")
        return self._page

    # -- actions -----------------------------------------------------------

    async def goto(self, url: str) -> str:
        """Navigate, refusing anything but http(s) to a public address.

        The agent is untrusted input driving a real browser inside our network,
        so this is an SSRF boundary, not a UX nicety. See tools/browser/netguard.py.
        """
        try:
            await check_destination(url)
        except BlockedDestination as exc:
            raise NavigationRefused(str(exc)) from exc
        await self.page.goto(url, wait_until="domcontentloaded")
        await settle(self.page, require_child_list=False)
        return self.page.url

    async def snapshot(self, *, retries: int = 3) -> Snapshot:
        """Settle, then extract the tree. Retries on the normal mid-read races."""
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                await settle(self.page, require_child_list=attempt > 0)
                raw = await self.page.evaluate(SNAPSHOT_JS)
                return Snapshot(
                    url=raw["url"],
                    title=raw.get("title") or "",
                    tree=raw.get("tree") or "",
                    refs=raw.get("refs") or {},
                    truncated=bool(raw.get("truncated")),
                    scroll=raw.get("scroll") or {},
                )
            except PlaywrightError as exc:
                last_error = exc
                if not is_transient(exc) or attempt == retries - 1:
                    break
                log.info("snapshot retry %d: %s", attempt + 1, exc)
        raise RuntimeError(f"could not read the page: {last_error}")

    async def screenshot_bytes(self) -> bytes:
        """Clean, unannotated screenshot.

        Harness's invariant: the marked-up view is for the model, the clean view
        is the artifact. Replay and reports must show what a real user saw, not
        our scaffolding.
        """
        return await self.page.screenshot(full_page=False, type="png")

    def locator(self, ref: str):
        return self.page.locator(f"[data-sc-ref='{ref}']")
