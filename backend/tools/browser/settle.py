"""Wait for the page to stop moving before observing it.

Never `sleep()`. A fixed delay captures half-rendered pages, and the agent then
honestly reports the half-rendered page as broken UI — a synthetic finding with
no real-world referent, which is the worst thing this system can produce.

Two gates, borrowed from Harness (DOM-quietness with a childList requirement for
SPA route transitions) and BrowserGym (retry on detached frames, because real
pages mutate mid-read and `Execution context was destroyed` is normal, not
exceptional).
"""
from __future__ import annotations

import logging

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

log = logging.getLogger(__name__)

# Errors that mean "the page moved under us" rather than "something is broken".
_TRANSIENT = (
    "Frame was detached",
    "Execution context was destroyed",
    "Frame with the given frameId is not found",
    "Target closed",
    "Navigation failed because page was closed",
)

_DOM_QUIET_JS = """
([idleMs, timeoutMs, requireChildList]) => new Promise((resolve) => {
  let timer = null;
  const started = Date.now();
  const done = (reason) => { try { obs.disconnect(); } catch (e) {} clearTimeout(timer); resolve(reason); };

  const arm = () => {
    clearTimeout(timer);
    timer = setTimeout(() => done('quiet'), idleMs);
  };

  const obs = new MutationObserver((records) => {
    if (Date.now() - started > timeoutMs) return done('timeout');
    if (requireChildList && !records.some((r) => r.type === 'childList')) return;
    arm();
  });

  obs.observe(document.documentElement, {
    childList: true, subtree: true, attributes: true, characterData: true,
  });
  arm();
  setTimeout(() => done('timeout'), timeoutMs);
})
"""


def is_transient(exc: BaseException) -> bool:
    message = str(exc)
    return any(marker in message for marker in _TRANSIENT)


async def settle(
    page: Page,
    *,
    idle_ms: int = 250,
    timeout_ms: int = 5_000,
    require_child_list: bool = False,
) -> str:
    """Return once the page is quiet. Returns the reason it stopped waiting.

    Best-effort throughout: a page that never goes quiet (a spinner, a polling
    widget, an animation) must not wedge the run. We take the snapshot anyway
    and let the customer report what they saw.
    """
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except PlaywrightError as exc:
        if not is_transient(exc):
            log.debug("domcontentloaded wait: %s", exc)

    try:
        # Handles full page loads well; frequently times out on SPAs, which is
        # expected and why the DOM gate below is the real signal.
        await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 3_000))
    except PlaywrightError:
        pass

    try:
        return await page.evaluate(
            _DOM_QUIET_JS, [idle_ms, timeout_ms, require_child_list]
        )
    except PlaywrightError as exc:
        if is_transient(exc):
            return "navigated"
        log.debug("dom quiet gate failed: %s", exc)
        return "error"
