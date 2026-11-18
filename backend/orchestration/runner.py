"""Run lifecycle: queued → running → evaluating → completed.

Persistence is injected as callables so this module never imports SQLAlchemy or
FastAPI — the layering rule the import-lint test enforces.

A run that ends because the customer gave up is `completed`. `failed` means *we*
broke: engine error, browser crash, credential failure. Never confuse the two;
"the customer lost interest" is a research result and the most common one worth
paying for.
"""
from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from context.builder import collect_blocks, render
from context.providers import DEFAULT_PROVIDERS
from domain.events import EngineOutcome, SessionEvent
from domain.findings import RunReport, SessionOutcome
from domain.research import (
    ModelSpec,
    ResearchBrief,
    RunBudget,
    RunPaths,
    RunStatus,
    RuntimeConfig,
)
from engines import registry
from orchestration.evaluator import ResearchEvaluator, cancelled_report, fallback_report
from orchestration.prompts import (
    CUSTOMER_PROMPT_VERSION,
    EVALUATOR_PROMPT_VERSION,
    build_customer_prompt,
    build_opening_message,
)
from orchestration.recorder import SessionRecorder
from storage.local import LocalStorageProvider
from tools.base import Tool, ToolRegistry
from tools.browser.session import BrowserSession
from tools.browser.toolset import build_browser_tools
from tools.research.toolset import build_research_tools

log = logging.getLogger(__name__)

PersistEvent = Callable[[SessionEvent], Awaitable[None]]
UpdateStatus = Callable[[RunStatus, dict[str, Any]], Awaitable[None]]
SaveReport = Callable[[RunReport], Awaitable[None]]
SessionFactory = Callable[..., BrowserSession]


def _default_session(**kwargs: Any) -> BrowserSession:
    return BrowserSession(**kwargs)


class RunFailed(RuntimeError):
    """Our infrastructure broke. Distinct from any customer outcome."""


async def execute_run(
    *,
    brief: ResearchBrief,
    model: ModelSpec,
    budget: RunBudget,
    storage: LocalStorageProvider,
    evaluator: ResearchEvaluator,
    persist_event: PersistEvent,
    update_status: UpdateStatus,
    save_report: SaveReport,
    workdir: Path,
    headless: bool = True,
    viewport: tuple[int, int] = (1280, 900),
    should_cancel: Callable[[], bool] | None = None,
    # Injected so the orchestration path can be exercised without a real
    # browser; production always uses the default.
    session_factory: SessionFactory = _default_session,
) -> RunReport:
    paths = RunPaths(project_id=brief.project_id, run_id=brief.run_id)
    run_home = workdir / str(brief.run_id)
    run_home.mkdir(parents=True, exist_ok=True)

    recorder = SessionRecorder(
        run_id=brief.run_id, paths=paths, storage=storage, persist=persist_event
    )

    engine = registry.for_model(model)
    started = time.perf_counter()

    await update_status(RunStatus.running, {"started_at": None})
    await recorder.run_started(
        {
            "engine": engine.name,
            "model": model.model,
            "mode": brief.mode.value,
            "persona": brief.customer.persona.id,
            "persona_source": brief.customer.source,
            "audience": brief.customer.resolution.audience,
            "match_confidence": brief.customer.resolution.match_confidence,
            "customer_prompt_version": CUSTOMER_PROMPT_VERSION,
            "evaluator_prompt_version": EVALUATOR_PROMPT_VERSION,
            "budget": {
                "max_tool_calls": budget.max_tool_calls,
                "max_wall_seconds": budget.max_wall_seconds,
            },
        }
    )

    session = session_factory(
        video_dir=run_home / "video",
        headless=headless,
        viewport=viewport,
    )

    outcome = EngineOutcome()
    tool_calls = 0
    cancelled = False
    try:
        await session.start()

        async def save_screenshot(label: str) -> str:
            return await recorder.save_screenshot(label, await session.screenshot_bytes())

        tools: list[Tool] = [
            *build_browser_tools(session, save_screenshot),
            *build_research_tools(),
        ]
        registry_of_tools = ToolRegistry(
            tools,
            record=recorder.record_tool_use,
            max_calls=budget.max_tool_calls,
            should_cancel=should_cancel,
        )

        blocks = await collect_blocks(brief, DEFAULT_PROVIDERS)
        system_prompt = build_customer_prompt(render(blocks))

        outcome = await engine.run(
            run_id=brief.run_id,
            system_prompt=system_prompt,
            opening_message=build_opening_message(brief),
            tools=registry_of_tools,
            model=model,
            budget=budget,
            config=RuntimeConfig(
                run_home=run_home,
                storage_root=Path(storage.root),
                headless=headless,
                viewport_width=viewport[0],
                viewport_height=viewport[1],
            ),
            emit=recorder.record_engine_event,
        )

        tool_calls = registry_of_tools.calls
        cancelled = registry_of_tools.cancelled
        if cancelled:
            outcome.stop_reason = "cancelled"
        elif registry_of_tools.finished and outcome.stop_reason in {"unknown", "end_turn"}:
            outcome.stop_reason = "customer_finished"

    except Exception as exc:  # noqa: BLE001
        log.exception("run %s failed during the session", brief.run_id)
        outcome.is_error = True
        outcome.error = str(exc)
        outcome.stop_reason = "engine_error"
    finally:
        video_path = await session.close()
        if video_path and video_path.exists():
            try:
                await storage.copy_in(paths.recording, video_path)
            except Exception:  # noqa: BLE001 — a missing video is not a failed run
                log.warning("could not store the recording for run %s", brief.run_id)

    # -- evaluation --------------------------------------------------------

    await recorder.flush()

    if cancelled:
        # The session is still on disk and can be analysed later with
        # POST /v1/runs/{id}/evaluate — but nobody asked for a report on a run
        # they just stopped, and evaluation is the expensive half.
        report = cancelled_report()
        await storage.write_json(paths.report, report.model_dump(mode="json"))
        # Persisted too, so the run reads "stopped before the customer finished"
        # rather than the blank "no report was produced" of a broken run.
        await save_report(report)
        await recorder.run_completed(
            {"stop_reason": "cancelled", "tool_calls": tool_calls}
        )
        await recorder.flush()
        await update_status(
            RunStatus.cancelled,
            {
                "stop_reason": "cancelled",
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "tokens_in": outcome.tokens_in,
                "tokens_out": outcome.tokens_out,
                "engine": engine.name,
            },
        )
        shutil.rmtree(run_home, ignore_errors=True)
        return report

    await update_status(RunStatus.evaluating, {})

    try:
        report = await evaluator.evaluate(
            brief=brief,
            transcript=recorder.transcript(),
            valid_seqs=recorder.valid_seqs(),
            stop_reason=outcome.stop_reason,
            finish_payload=recorder.finish_payload,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("run %s: evaluation failed", brief.run_id)
        report = fallback_report(stop_reason=outcome.stop_reason, error=str(exc))

    await storage.write_json(paths.report, report.model_dump(mode="json"))
    await save_report(report)

    duration_ms = int((time.perf_counter() - started) * 1000)
    await recorder.run_completed(
        {
            "stop_reason": outcome.stop_reason,
            "outcome": report.outcome.value,
            "turns": outcome.turns,
            "tool_calls": tool_calls,
            "tokens_in": outcome.tokens_in,
            "tokens_out": outcome.tokens_out,
            "cost_usd": outcome.cost_usd,
            "duration_ms": duration_ms,
        }
    )
    await recorder.flush()

    # An engine crash is our failure; every customer outcome is a completed run.
    terminal = (
        RunStatus.failed
        if outcome.is_error and report.outcome is SessionOutcome.error
        else RunStatus.completed
    )
    await update_status(
        terminal,
        {
            "stop_reason": outcome.stop_reason,
            "duration_ms": duration_ms,
            "tokens_in": outcome.tokens_in,
            "tokens_out": outcome.tokens_out,
            "cost_usd": outcome.cost_usd,
            "engine": engine.name,
            "error": outcome.error,
        },
    )

    shutil.rmtree(run_home, ignore_errors=True)
    return report
