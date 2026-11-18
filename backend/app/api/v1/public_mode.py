"""Public-deployment gates.

A public demo runs studies on the visitor's own Anthropic key and exposes only
what a visitor needs to create a study and read its results. Everything that
reads or changes the operator's configuration or credentials is refused.

These are dependencies so they resolve before a handler's own — a refused
request never reaches the database.
"""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from app.core.config import Settings, get_settings
from services.byok import is_well_formed_api_key


async def forbid_in_public_mode(settings: Settings = Depends(get_settings)) -> None:
    if settings.PUBLIC_MODE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This isn't available on the public demo.")
    return None


async def visitor_api_key(
    x_anthropic_api_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> str | None:
    """The visitor's Anthropic key in public mode; None when the operator runs things."""
    if not settings.PUBLIC_MODE:
        return None
    key = (x_anthropic_api_key or "").strip()
    if not key:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "This demo runs on your own Anthropic API key — add it to start a study.",
        )
    if not is_well_formed_api_key(key):
        # Never echo the value back: a pasted key is a secret even when malformed.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That doesn't look like an Anthropic API key — it should start with sk-ant-.",
        )
    return key


OPERATOR_ONLY = [Depends(forbid_in_public_mode)]
VISITOR_KEY_REQUIRED = [Depends(visitor_api_key)]
