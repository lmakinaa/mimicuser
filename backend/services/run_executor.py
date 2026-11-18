"""Wires the orchestration runner to persistence, storage and credentials.

This is the composition root for a run. `orchestration/` stays free of SQL and
FastAPI; everything that knows about both lives here.
"""
from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.runtime_settings import resolve
from app.db.session import get_sessionmaker
from domain.events import SessionEvent
from domain.findings import RunReport
from domain.persona import SyntheticCustomer
from domain.research import (
    ModelSpec,
    ResearchBrief,
    RunBudget,
    RunMode,
    RunPaths,
    RunStatus,
)
from orchestration.audience import AudienceResolver
from orchestration.completion import build_completion_client
from orchestration.evaluator import ResearchEvaluator
from orchestration.persona_synthesis import SYNTHESIS_PROMPT_VERSION, LLMPersonaResolver
from orchestration.prompts import EVALUATOR_PROMPT_VERSION
from orchestration.runner import execute_run
from personas.store import DbPersonaStore
from repositories.oauth_repo import OAuthAccountRepo
from repositories.persona_cache_repo import PersonaCacheRepo
from repositories.run_repo import EventRepo, ProjectRepo, ReportRepo, RunRepo
from services import anthropic_auth, cancellation
from services.byok import choose_credential
from storage.local import LocalStorageProvider

log = logging.getLogger(__name__)


async def reevaluate_run(run_id: UUID, *, sessionmaker: async_sessionmaker | None = None) -> RunReport:
    """Re-run evaluation over a stored session. No browser, no agent.

    This is the payoff of keeping the evaluator away from the browser: a session
    that was recorded but not analysed — a rate limit, a transient failure, or
    simply an older evaluator prompt — can be analysed again from the event
    stream alone, without paying for the browsing a second time.
    """
    maker = sessionmaker or get_sessionmaker()

    async with maker() as session:
        settings = await resolve(session)
        run = await RunRepo.get(session, run_id)
        if run is None:
            raise RunNotFound(f"run {run_id} does not exist")
        project = await ProjectRepo.get(session, run.project_id)
        brief = ResearchBrief(
            run_id=run.id,
            project_id=run.project_id,
            product_url=run.product_url,
            product_description=project.product_description if project else None,
            customer=SyntheticCustomer.model_validate(run.customer),
            task=run.task,
            mode=RunMode(run.mode),
        )
        stop_reason = run.stop_reason or "unknown"
        rows = await EventRepo.list_for_run(session, run_id, limit=5000)
        provider = "openai" if run.engine == "openai" else "anthropic"
        credential_kind, secret = await resolve_credential(session, settings, provider=provider)
        await session.commit()

    if not rows:
        raise RunNotFound(f"run {run_id} has no recorded events to analyse")

    events = [
        SessionEvent(
            run_id=r.run_id, seq=r.seq, ts=r.ts, type=r.type, url=r.url,
            action=r.action, target=r.target, input=r.input, result=r.result,
            observation=r.observation, screenshot_key=r.screenshot_key,
            error=r.error, meta=r.meta or {},
        )
        for r in rows
    ]
    finish = next(
        (e.result for e in reversed(events) if e.type == "session_finished" and e.result),
        None,
    )

    evaluator = ResearchEvaluator(
        build_completion_client(
            provider=provider,
            credential_kind=credential_kind, secret=secret,
            model=settings.model_for_provider(provider, "evaluator"), effort=settings.MODEL_EFFORT,
        )
    )
    report = await evaluator.evaluate(
        brief=brief,
        transcript="\n".join(e.to_transcript_line() for e in events),
        valid_seqs={e.seq for e in events},
        stop_reason=stop_reason,
        finish_payload=finish,
    )

    storage = LocalStorageProvider(settings.STORAGE_ROOT)
    await storage.write_json(
        RunPaths(project_id=brief.project_id, run_id=run_id).report,
        report.model_dump(mode="json"),
    )
    async with maker() as s:
        await ReportRepo.save(
            s, run_id=run_id, report=report, evaluator_version=EVALUATOR_PROMPT_VERSION
        )
        await s.commit()
    log.info("re-evaluated run %s", run_id)
    return report


class RunNotFound(LookupError):
    pass


async def build_audience_resolver(
    session: AsyncSession, settings: Settings, *, sessionmaker=None
) -> AudienceResolver:
    """An agent-backed resolver with a durable cache.

    Every audience is resolved by a model that can tell a recruiter from a
    buyer. Without a credential there is no resolver at all — and that is
    deliberate: the old behaviour silently fell back to the nearest
    token-overlap match, which is how "IT Recruiter" became an enterprise buyer.
    A run that cannot resolve its audience must fail loudly, not simulate the
    wrong human being.
    """
    cache = _DbPersonaCache(sessionmaker or get_sessionmaker())
    # Authored personas layered over the built-ins, so a persona created through
    # the API is selectable by id and offerable to the agent for adoption. With
    # the catalogue-only store they were invisible to every run.
    personas = DbPersonaStore(session)
    try:
        credential_kind, secret = await resolve_credential(session, settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("no credential for persona resolution (%s); cache only", exc)
        return AudienceResolver(cache=cache, personas=personas)

    return AudienceResolver(
        LLMPersonaResolver(
            build_completion_client(
                provider=settings.LLM_PROVIDER,
                credential_kind=credential_kind,
                secret=secret,
                model=settings.model_for("resolver"),
                effort=settings.MODEL_EFFORT,
            )
        ),
        cache=cache,
        personas=personas,
    )


class _DbPersonaCache:
    """Adapts `PersonaCacheRepo` to the `PersonaCache` protocol.

    Its own short sessions: resolution happens inside a request that has its own
    transaction, and a cache write must not be rolled back with it.
    """

    def __init__(self, maker) -> None:
        self._maker = maker

    async def get(self, key: str):
        async with self._maker() as s:
            return await PersonaCacheRepo.get(s, key, SYNTHESIS_PROMPT_VERSION)

    async def put(self, key, audience, persona, source, adopted_from) -> None:
        async with self._maker() as s:
            await PersonaCacheRepo.put(
                s, key=key, audience=audience, version=SYNTHESIS_PROMPT_VERSION,
                persona=persona, source=source, adopted_from=adopted_from,
            )
            await s.commit()


async def resolve_credential(
    session: AsyncSession, settings: Settings, *, provider: str | None = None
) -> tuple[str, str]:
    """Return `(credential_kind, secret)` for this run.

    OAuth (subscription auth) is the documented path. An API key is used only
    when it is the sole thing configured, so a stray environment variable can
    never silently redirect billing away from a linked account.
    """
    provider = provider or settings.LLM_PROVIDER
    if provider == "openai":
        account = await OAuthAccountRepo.api_key_account(session, "openai")
        if account is None:
            raise RuntimeError("No OpenAI API key is configured. Save one via PUT /v1/auth/openai/key.")
        return "api_key", OAuthAccountRepo.access_token(
            account, settings.require_encryption_key()
        )
    if settings.credential_kind == "api_key":
        return "api_key", settings.ANTHROPIC_API_KEY

    account = await anthropic_auth.newest_account(session)
    if account is None:
        raise RuntimeError(
            "No Anthropic account is linked and ANTHROPIC_API_KEY is unset. "
            "Link one via POST /v1/auth/anthropic/start."
        )
    token = await anthropic_auth.access_token_for(
        session,
        account.id,
        client_id=settings.CLAUDE_OAUTH_CLIENT_ID,
        key=settings.require_encryption_key(),
    )
    return "oauth", token


async def resolve_credential_for(
    session: AsyncSession,
    settings: Settings,
    *,
    run_ciphertext: bytes | None = None,
    study_ciphertext: bytes | None = None,
) -> tuple[str, str]:
    """The credential that pays for one piece of work.

    A visitor's sealed key always wins. In public mode, work without one raises
    instead of falling back: the operator's credential is never consulted, so a
    missing or unreadable visitor key cannot silently bill the operator.
    """
    visitor = choose_credential(
        run_ciphertext=run_ciphertext,
        study_ciphertext=study_ciphertext,
        encryption_key=settings.ENCRYPTION_KEY,
        public_mode=settings.PUBLIC_MODE,
    )
    if visitor is not None:
        return visitor
    return await resolve_credential(session, settings)


async def run_research(run_id: UUID, *, sessionmaker: async_sessionmaker | None = None) -> None:
    """Execute one queued run end to end.

    Each persistence callback opens its own short session. A single long-lived
    session would hold a connection for the whole browser run and would lose
    every recorded event if the final commit failed — the event stream is the
    product, so it is committed as it happens.
    """
    maker = sessionmaker or get_sessionmaker()
    async with maker() as session:
        settings = await resolve(session)
    storage = LocalStorageProvider(settings.STORAGE_ROOT)

    async def update_status(status: RunStatus, fields: dict) -> None:
        async with maker() as s:
            await RunRepo.set_status(s, run_id, status.value, fields)
            await s.commit()

    # Defined before anything can fail. Setup errors — a missing credential, a
    # bad persona, an unroutable engine — used to escape before the failure
    # handler existed, leaving an unrunnable run sitting in `queued` forever,
    # which is indistinguishable from "still waiting" in the UI.
    try:
        return await _execute(
            run_id,
            settings=settings,
            maker=maker,
            storage=storage,
            update_status=update_status,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("run %s failed", run_id)
        await update_status(
            RunStatus.failed, {"error": str(exc), "stop_reason": "setup_failed"}
        )
        raise
    finally:
        # A failed run is still a finished run: the study must not wait forever
        # for a sibling that will never report.
        await _notify_study(run_id, maker)


async def _execute(
    run_id: UUID,
    *,
    settings: Settings,
    maker: async_sessionmaker,
    storage: LocalStorageProvider,
    update_status,
) -> None:
    if cancellation.is_cancelled("run", str(run_id)):
        log.info("run %s was cancelled before it started", run_id)
        async with maker() as session:
            await RunRepo.set_status(
                session, run_id, RunStatus.cancelled.value, {"stop_reason": "cancelled"}
            )
            await session.commit()
        return

    async with maker() as session:
        run = await RunRepo.get(session, run_id)
        if run is None:
            log.error("run %s does not exist", run_id)
            return
        project = await ProjectRepo.get(session, run.project_id)
        if project is None:
            log.error("run %s has no project", run_id)
            return

        customer = SyntheticCustomer.model_validate(run.customer)
        brief = ResearchBrief(
            run_id=run.id,
            project_id=run.project_id,
            product_url=run.product_url,
            product_description=project.product_description,
            customer=customer,
            task=run.task,
            mode=RunMode(run.mode),
        )
        budget = RunBudget(
            max_tool_calls=int(run.budget.get("max_tool_calls", settings.DEFAULT_MAX_TOOL_CALLS)),
            max_wall_seconds=int(
                run.budget.get("max_wall_seconds", settings.DEFAULT_MAX_WALL_SECONDS)
            ),
            max_cost_usd=float(run.budget.get("max_cost_usd", settings.DEFAULT_MAX_COST_USD)),
        )
        provider = "openai" if run.engine == "openai" else "anthropic"
        credential_kind, secret = await resolve_credential(
            session, settings, provider=provider
        )
        model = ModelSpec(
            model=run.model,
            engine=run.engine,
            provider=provider,
            credential_kind=credential_kind,  # type: ignore[arg-type]
            credential_ref=secret,
            effort=settings.MODEL_EFFORT,
        )
        await session.commit()

    evaluator = ResearchEvaluator(
        build_completion_client(
            provider=provider,
            credential_kind=credential_kind,
            secret=secret,
            model=settings.model_for_provider(provider, "evaluator"),
            effort=settings.MODEL_EFFORT,
        )
    )

    async def persist_event(event: SessionEvent) -> None:
        async with maker() as s:
            await EventRepo.append(s, event)
            await s.commit()

    async def save_report(report: RunReport) -> None:
        async with maker() as s:
            await ReportRepo.save(
                s, run_id=run_id, report=report, evaluator_version=EVALUATOR_PROMPT_VERSION
            )
            await s.commit()

    # Failures here propagate to run_research, which marks the run failed.
    await execute_run(
        brief=brief,
        model=model,
        budget=budget,
        storage=storage,
        evaluator=evaluator,
        persist_event=persist_event,
        update_status=update_status,
        save_report=save_report,
        workdir=settings.WORKDIR,
        headless=settings.BROWSER_HEADLESS,
        viewport=(settings.VIEWPORT_WIDTH, settings.VIEWPORT_HEIGHT),
        should_cancel=lambda: cancellation.is_cancelled("run", str(run_id)),
    )


async def _notify_study(run_id: UUID, maker) -> None:
    """Tell the run's study it finished; the last one home triggers aggregation."""
    try:
        async with maker() as session:
            run = await RunRepo.get(session, run_id)
            study_id = run.study_id if run else None
            await session.commit()
        if study_id is None:
            return

        from services.study_executor import note_run_finished

        if await note_run_finished(study_id, sessionmaker=maker):
            from worker.tasks import enqueue_aggregation

            enqueue_aggregation(str(study_id))
    except Exception:  # noqa: BLE001 — never let bookkeeping fail a finished run
        log.exception("could not notify study for run %s", run_id)
