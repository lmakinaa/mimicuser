"""Study fan-out and completion detection against a real Postgres.

Needs the docker-compose stack. Skipped when the database isn't reachable.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from repositories.orm import Base
from domain.study import SegmentSpec
from orchestration.persona_synthesis import SYNTHESIS_PROMPT_VERSION
from personas import get_persona
from repositories.persona_cache_repo import PersonaCacheRepo
from repositories.run_repo import RunRepo
from repositories.study_repo import StudyRepo
from services.study_executor import create_study, note_run_finished

pytestmark = pytest.mark.integration


TEST_DB = "mimicuser_test"


def _test_database_url() -> str:
    """The app's URL with the database name swapped for a throwaway one."""
    base = get_settings().DATABASE_URL
    head, _, _ = base.rpartition("/")
    return f"{head}/{TEST_DB}"


@pytest.fixture
async def sessionmaker():
    """A dedicated database, created on demand and dropped afterwards.

    These tests commit — `create_study` and `note_run_finished` have to, since
    they are testing transactional claiming across concurrent callers. Pointed at
    the app's own database that leaves real rows behind: an earlier version of
    this fixture accumulated 50 studies and 151 runs there, which then showed up
    in the product's own UI alongside genuine research.

    So the schema is built fresh here from the ORM metadata and thrown away at
    the end. Nothing this suite writes can reach the app's data.
    """
    admin = create_async_engine(
        get_settings().DATABASE_URL, poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    except Exception:  # noqa: BLE001
        await admin.dispose()
        pytest.skip("postgres not reachable — run `docker compose up -d`")

    engine = create_async_engine(_test_database_url(), poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    try:
        yield maker
    finally:
        await engine.dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
        await admin.dispose()


AUDIENCES = {"Freelance designers": "freelancer", "Startup founders": "startup_founder"}


async def _seed_persona_cache(maker) -> None:
    """Pre-resolve the audiences so the test never calls the model.

    Resolution is an agent call now, so without this the suite depends on a live
    Anthropic account — and fails on a rate limit that says nothing about the
    code under test. Seeding also exercises the cache-first path these tests
    care about.
    """
    from orchestration.audience import normalise

    async with maker() as session:
        for audience, persona_id in AUDIENCES.items():
            await PersonaCacheRepo.put(
                session,
                key=normalise(audience),
                audience=audience,
                version=SYNTHESIS_PROMPT_VERSION,
                persona=get_persona(persona_id),
                source="catalogue",
                adopted_from=persona_id,
            )
        await session.commit()


async def _make_study(maker, counts=(2, 2)):
    settings = get_settings()
    await _seed_persona_cache(maker)
    async with maker() as session:
        study_id, run_ids = await create_study(
            session,
            settings=settings,
            product_url="https://example.com/study-test",
            segments=[
                SegmentSpec(audience="Freelance designers", count=counts[0]),
                SegmentSpec(audience="Startup founders", count=counts[1]),
            ],
            name="lifecycle test",
            sessionmaker=maker,
        )
        await session.commit()
    return study_id, run_ids


async def test_fan_out_creates_a_run_per_customer_with_one_persona_per_segment(sessionmaker):
    study_id, run_ids = await _make_study(sessionmaker, counts=(3, 2))
    assert len(run_ids) == 5

    async with sessionmaker() as session:
        runs = await StudyRepo.runs(session, study_id)
        assert len(runs) == 5
        assert all(r.status == "queued" for r in runs)

        by_segment: dict[str, set[str]] = {}
        for run in runs:
            by_segment.setdefault(run.segment_key, set()).add(
                run.customer["persona"]["id"]
            )
        # One persona per segment: "3 of 3 freelancers hit this" must describe
        # the product, not three differently-invented people.
        assert all(len(ids) == 1 for ids in by_segment.values())
        assert len(by_segment) == 2
        assert {len([r for r in runs if r.segment_key == k]) for k in by_segment} == {3, 2}

        # The customer agent runs on the cheaper tier.
        assert {r.model for r in runs} == {get_settings().CUSTOMER_MODEL}


async def test_aggregation_is_claimed_only_when_the_last_run_finishes(sessionmaker):
    study_id, run_ids = await _make_study(sessionmaker, counts=(1, 1))

    async with sessionmaker() as session:
        await RunRepo.set_status(session, run_ids[0], "completed", {})
        await session.commit()

    # One still pending — nobody aggregates yet.
    assert await note_run_finished(study_id, sessionmaker=sessionmaker) is False
    async with sessionmaker() as session:
        study = await StudyRepo.get(session, study_id)
        assert study.status == "running"

    async with sessionmaker() as session:
        await RunRepo.set_status(session, run_ids[1], "failed", {})
        await session.commit()

    # A failed run still counts as finished; the study must not wait forever.
    assert await note_run_finished(study_id, sessionmaker=sessionmaker) is True
    async with sessionmaker() as session:
        study = await StudyRepo.get(session, study_id)
        assert study.status == "aggregating"


async def test_only_one_caller_wins_a_simultaneous_finish(sessionmaker):
    """Two runs can finish in the same instant; the study must be analysed once."""
    study_id, run_ids = await _make_study(sessionmaker, counts=(1, 1))
    async with sessionmaker() as session:
        for run_id in run_ids:
            await RunRepo.set_status(session, run_id, "completed", {})
        await session.commit()

    results = await asyncio.gather(
        *(note_run_finished(study_id, sessionmaker=sessionmaker) for _ in range(4))
    )
    assert sum(results) == 1
