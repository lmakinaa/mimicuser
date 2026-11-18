"""Settings that can change without a restart.

`get_settings()` is `@lru_cache`d and reads the environment once, which is right
for infrastructure and wrong for the handful of knobs an operator actually wants
to turn — above all the model tiers, which were only changeable by editing `.env`
and restarting.

The resolution is deliberately boring: read one row, overlay it on the env-backed
`Settings`, return a copy. Consumers keep taking a plain `Settings`, so nothing
downstream needs to know a database was involved.

What is *not* here matters as much as what is. `DATABASE_URL` and `REDIS_URL` are
captured by `@lru_cache`d engines and the Dramatiq broker at import; `CORS_ORIGINS`
is fixed into middleware at `create_app()`; Dramatiq's `time_limit` is evaluated
inside the actor decorator. Making those editable would produce edits that appear
to save and then do nothing, which is worse than not offering them.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session
from repositories.orm import AppSettingsRow

log = logging.getLogger(__name__)

#: Field name -> human description. The keys are exactly the `Settings` fields
#: that take effect on the next run without restarting anything.
EDITABLE: dict[str, str] = {
    "LLM_PROVIDER": "Provider used for new customer runs and evaluations.",
    "CUSTOMER_MODEL": "Model the synthetic customer runs on — the bulk of the work.",
    "EVALUATOR_MODEL": "Model that turns one session into findings. Runs once per customer.",
    "AGGREGATOR_MODEL": "Model that clusters findings across customers. Once per study.",
    "RESOLVER_MODEL": "Model that turns an audience into a persona.",
    "DEFAULT_MODEL": "Fallback model where no tier is specified.",
    "MODEL_EFFORT": "Reasoning effort for models that support it.",
    "STUDY_CONCURRENCY": "How many runs may execute at once across all studies.",
    "DEFAULT_MAX_TOOL_CALLS": "Default per-run tool-call budget.",
    "DEFAULT_MAX_WALL_SECONDS": "Default per-run wall-clock budget.",
    "DEFAULT_MAX_COST_USD": "Default per-run spend cap. Inert under subscription auth.",
    "BROWSER_HEADLESS": "Run the browser headless.",
    "VIEWPORT_WIDTH": "Browser viewport width.",
    "VIEWPORT_HEIGHT": "Browser viewport height.",
}

#: Shown so the operator can see them, never writable here.
READ_ONLY = (
    "DATABASE_URL",
    "REDIS_URL",
    "STORAGE_ROOT",
    "WORKDIR",
    "CORS_ORIGINS",
    "LOG_LEVEL",
)


async def load_overrides(session: AsyncSession) -> dict[str, Any]:
    row = (await session.execute(select(AppSettingsRow).limit(1))).scalar_one_or_none()
    return dict(row.values) if row and row.values else {}


async def save_overrides(session: AsyncSession, patch: dict[str, Any]) -> dict[str, Any]:
    """Merge `patch` into the stored overrides and return the result."""
    row = (await session.execute(select(AppSettingsRow).limit(1))).scalar_one_or_none()
    if row is None:
        row = AppSettingsRow(values={})
        session.add(row)
    merged = {**(row.values or {}), **patch}
    # Dropping a key restores the environment default rather than pinning it.
    row.values = {k: v for k, v in merged.items() if v is not None}
    await session.flush()
    return row.values


def apply(base: Settings, overrides: dict[str, Any]) -> Settings:
    """Overlay stored overrides on the env-backed settings.

    Unknown or non-editable keys are ignored rather than trusted: the row is
    data, and a stale key left behind by an older build must not resurrect a
    setting the code no longer honours.
    """
    clean = {k: v for k, v in overrides.items() if k in EDITABLE}
    if not clean:
        return base
    try:
        return base.model_copy(update=clean)
    except Exception:  # noqa: BLE001
        log.exception("stored settings could not be applied; using environment")
        return base


async def resolve(session: AsyncSession) -> Settings:
    """The settings a run should actually use."""
    return apply(get_settings(), await load_overrides(session))


async def get_runtime_settings(session: AsyncSession = Depends(get_session)) -> Settings:
    """FastAPI dependency — drop-in replacement for `Depends(get_settings)`."""
    return await resolve(session)
