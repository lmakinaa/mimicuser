"""Dramatiq actors.

Thin shells around the service functions, so the same code path runs whether a
run is queued or invoked directly (tests, CLI, the re-evaluate endpoint).
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import contextmanager

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from app.core.config import get_settings

log = logging.getLogger(__name__)

_settings = get_settings()
broker = RedisBroker(url=_settings.REDIS_URL)
dramatiq.set_broker(broker)

_SEMAPHORE_KEY = "mimicuser:run_slots"
_SLOT_TTL_S = _settings.DEFAULT_MAX_WALL_SECONDS + 900


def _concurrency() -> int:
    """Current run-slot cap, including any runtime override.

    Defensive on purpose: this sits on the path of every task, and a database
    hiccup must not stop work — falling back to the environment value is
    strictly better than refusing to run.

    The driver here is async, so this opens its own short-lived loop. It runs
    *before* the task body's own `asyncio.run`, never inside it, so the two
    never nest.
    """
    from sqlalchemy import text

    from app.db.session import new_engine_and_sessionmaker

    async def _read() -> int | None:
        engine, _ = new_engine_and_sessionmaker()
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(text("SELECT values FROM app_settings LIMIT 1"))
                ).scalar_one_or_none()
            return int(row["STUDY_CONCURRENCY"]) if row and "STUDY_CONCURRENCY" in row else None
        finally:
            await engine.dispose()

    try:
        value = asyncio.run(_read())
        if value is not None:
            return value
    except Exception as exc:  # noqa: BLE001
        log.debug("could not read runtime concurrency (%s); using env value", exc)
    return _settings.STUDY_CONCURRENCY


@contextmanager
def _run_slot():
    """Cap concurrent browser sessions across every worker process.

    A Redis counter rather than `--threads`, so the cap holds no matter how the
    workers are deployed. Rate limit — not CPU — is what this protects: a study
    that fans out ten runs at once gets 429s for all ten.

    Slots carry a TTL so a killed worker cannot leak one forever.
    """
    client = broker.client
    # Read per task, not from the module-level settings captured at import, so
    # changing concurrency through the settings API takes effect on the next run
    # instead of at the next worker restart.
    limit = _concurrency()
    acquired = False
    deadline = time.monotonic() + _SLOT_TTL_S
    try:
        while time.monotonic() < deadline:
            count = client.incr(_SEMAPHORE_KEY)
            if count == 1:
                client.expire(_SEMAPHORE_KEY, _SLOT_TTL_S)
            if count <= limit:
                acquired = True
                break
            client.decr(_SEMAPHORE_KEY)
            time.sleep(5)
        if not acquired:
            log.warning("gave up waiting for a run slot; proceeding uncapped")
        yield
    finally:
        if acquired:
            try:
                if client.decr(_SEMAPHORE_KEY) < 0:
                    client.set(_SEMAPHORE_KEY, 0)
            except Exception:  # noqa: BLE001
                log.warning("could not release run slot", exc_info=True)


@dramatiq.actor(
    # A browser session is expensive and not idempotent, so a run that failed
    # mid-session must never be replayed. Setup failures are retried explicitly
    # inside the actor instead, where we can tell the two apart.
    max_retries=0,
    time_limit=int((_settings.DEFAULT_MAX_WALL_SECONDS + 900) * 1000),
    queue_name="research",
)
def run_research_task(run_id: str) -> None:
    from uuid import UUID

    from app.db.session import new_engine_and_sessionmaker
    from services.run_executor import run_research

    async def main() -> None:
        # A fresh, unpooled engine per task: this actor body runs under a new
        # event loop each time, and a pooled connection from the previous one
        # fails with "attached to a different loop".
        engine, sessionmaker = new_engine_and_sessionmaker()
        try:
            await run_research(UUID(run_id), sessionmaker=sessionmaker)
        finally:
            await engine.dispose()

    with _run_slot():
        asyncio.run(main())


@dramatiq.actor(max_retries=2, time_limit=1_800_000, queue_name="research")
def aggregate_study_task(study_id: str) -> None:
    """Cluster a finished study's findings.

    Retried, unlike a run: aggregation is idempotent (it replaces the report)
    and its usual failure is a rate limit that clears.
    """
    from uuid import UUID

    from app.db.session import new_engine_and_sessionmaker
    from services.study_executor import aggregate_study

    async def main() -> None:
        engine, sessionmaker = new_engine_and_sessionmaker()
        try:
            await aggregate_study(UUID(study_id), sessionmaker=sessionmaker)
        finally:
            await engine.dispose()

    asyncio.run(main())


def enqueue_run(run_id: str) -> None:
    """Failure to enqueue must not 500 the request — the run row already exists."""
    try:
        run_research_task.send(run_id)
    except Exception:  # noqa: BLE001
        log.exception("could not enqueue run %s; it stays queued for retry", run_id)


def enqueue_aggregation(study_id: str) -> None:
    try:
        aggregate_study_task.send(study_id)
    except Exception:  # noqa: BLE001
        log.exception("could not enqueue aggregation for study %s", study_id)
