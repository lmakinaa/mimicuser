"""Read and change the settings that can take effect without a restart."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.public_mode import OPERATOR_ONLY
from app.api.v1.schemas import SettingsResponse, UpdateSettingsRequest
from app.core.config import Settings, get_settings
from app.core.runtime_settings import (
    EDITABLE,
    READ_ONLY,
    apply,
    load_overrides,
    save_overrides,
)
from app.db.session import get_session

router = APIRouter(dependencies=OPERATOR_ONLY)

# Coerced before validation because JSON from a form arrives as strings, and
# `model_copy` does not validate — a string "5" pinned into STUDY_CONCURRENCY
# would survive here and fail much later, inside a worker.
_TYPES: dict[str, type] = {
    "STUDY_CONCURRENCY": int,
    "DEFAULT_MAX_TOOL_CALLS": int,
    "DEFAULT_MAX_WALL_SECONDS": int,
    "VIEWPORT_WIDTH": int,
    "VIEWPORT_HEIGHT": int,
    "DEFAULT_MAX_COST_USD": float,
    "BROWSER_HEADLESS": bool,
}

_BOUNDS: dict[str, tuple[float, float]] = {
    "STUDY_CONCURRENCY": (1, 20),
    "DEFAULT_MAX_TOOL_CALLS": (5, 300),
    "DEFAULT_MAX_WALL_SECONDS": (60, 3600),
    "DEFAULT_MAX_COST_USD": (0.01, 50),
    "VIEWPORT_WIDTH": (320, 3840),
    "VIEWPORT_HEIGHT": (480, 2160),
}


def _coerce(key: str, value: Any) -> Any:
    if key == "LLM_PROVIDER":
        if value not in {"anthropic", "openai"}:
            raise HTTPException(422, "LLM_PROVIDER must be anthropic or openai")
        return value
    want = _TYPES.get(key)
    if want is None:
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(422, f"{key} must be a non-empty string")
        return value.strip()
    try:
        coerced = (
            str(value).strip().lower() in {"1", "true", "yes", "on"}
            if want is bool
            else want(value)
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, f"{key} must be {want.__name__}") from exc
    if key in _BOUNDS:
        low, high = _BOUNDS[key]
        if not low <= coerced <= high:
            raise HTTPException(422, f"{key} must be between {int(low)} and {int(high)}")
    return coerced


def _payload(effective, overrides: dict[str, Any]) -> SettingsResponse:
    return SettingsResponse(
        editable={k: getattr(effective, k) for k in EDITABLE},
        descriptions=EDITABLE,
        overridden=sorted(k for k in overrides if k in EDITABLE),
        read_only={k: str(getattr(effective, k)) for k in READ_ONLY},
        # Honest about the one thing an edit cannot reach.
        restart_required=[
            "DATABASE_URL and REDIS_URL are bound to cached connections at import.",
            "The worker's Dramatiq time limit is fixed in the actor decorator; "
            "raising DEFAULT_MAX_WALL_SECONDS beyond it needs a worker restart.",
        ],
    )


@router.get("/settings", response_model=SettingsResponse)
async def read(session: AsyncSession = Depends(get_session)) -> SettingsResponse:
    overrides = await load_overrides(session)
    return _payload(apply(get_settings(), overrides), overrides)


@router.patch("/settings", response_model=SettingsResponse)
async def update(
    body: UpdateSettingsRequest, session: AsyncSession = Depends(get_session)
) -> SettingsResponse:
    unknown = sorted(set(body.values) - set(EDITABLE))
    if unknown:
        raise HTTPException(
            422,
            f"Not editable at runtime: {', '.join(unknown)}. "
            "These are environment settings and changing them here would not take effect.",
        )
    overrides = await load_overrides(session)
    patch = {k: _coerce(k, v) for k, v in body.values.items()}
    effective_provider = patch.get("LLM_PROVIDER", overrides.get("LLM_PROVIDER", get_settings().LLM_PROVIDER))
    invalid_models = [
        key for key in ("DEFAULT_MODEL", "CUSTOMER_MODEL", "EVALUATOR_MODEL", "AGGREGATOR_MODEL", "RESOLVER_MODEL")
        if key in patch and patch[key] not in Settings().valid_models(effective_provider)
    ]
    if invalid_models:
        raise HTTPException(422, f"Model names do not match {effective_provider}: {', '.join(invalid_models)}")
    overrides = await save_overrides(session, patch)
    await session.commit()
    return _payload(apply(get_settings(), overrides), overrides)


@router.delete("/settings/{key}", response_model=SettingsResponse)
async def reset(key: str, session: AsyncSession = Depends(get_session)) -> SettingsResponse:
    """Drop one override so the environment value applies again."""
    if key not in EDITABLE:
        raise HTTPException(404, f"{key} is not a runtime setting")
    overrides = await save_overrides(session, {key: None})
    await session.commit()
    return _payload(apply(get_settings(), overrides), overrides)
