"""Async engine + session factories.

Two factories, because the API and the worker have different lifetimes.

The API is one long-lived process on one event loop, so a cached, pooled engine
is right. A Dramatiq task runs its own `asyncio.run()` per message: a pooled
connection opened under one loop and reused under the next raises
``got Future attached to a different loop``. The worker therefore builds a
`NullPool` engine per task and disposes it.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_engine():
    settings = get_settings()
    return create_async_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        echo=False,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)


def new_engine_and_sessionmaker() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """A throwaway engine bound to the caller's event loop.

    `NullPool` means connections are never held across loops. The caller must
    `await engine.dispose()` when finished.
    """
    engine = create_async_engine(
        get_settings().DATABASE_URL, poolclass=NullPool, echo=False
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Commits on success, rolls back on error."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
