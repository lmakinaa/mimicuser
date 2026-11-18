"""Anthropic OAuth PKCE — copy-code flow.

No redirect URI to register and no callback endpoint to expose: the operator
pastes the code Anthropic shows them. See `docs/05-anthropic-oauth.md`.
"""
from __future__ import annotations

import logging
import secrets
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.public_mode import OPERATOR_ONLY
from app.api.v1.schemas import (
    ApiKeyRequest,
    AuthStatusResponse,
    OAuthAccountResponse,
    ProviderStatusResponse,
    StartOAuthRequest,
    StartOAuthResponse,
    SubmitCodeRequest,
)
from app.core.config import Settings, get_settings
from app.core.runtime_settings import apply, load_overrides, save_overrides
from app.db.session import get_session
from repositories.oauth_repo import OAuthAccountRepo
from services import anthropic_auth

log = logging.getLogger(__name__)
router = APIRouter(dependencies=OPERATOR_ONLY)


@router.post("/auth/anthropic/start", response_model=StartOAuthResponse)
async def start_oauth(
    body: StartOAuthRequest, settings: Settings = Depends(get_settings)
) -> StartOAuthResponse:
    key = settings.require_encryption_key()
    verifier, challenge = anthropic_auth.generate_pkce()

    # The payload rides inside the state parameter — signed, self-expiring, and
    # requiring no server-side store.
    state = anthropic_auth.encode_state(
        {
            "code_verifier": verifier,
            "label": body.label,
            "exp": int(time.time()) + settings.OAUTH_STATE_TTL_SECONDS,
            "jti": secrets.token_urlsafe(16),
        },
        key,
    )
    return StartOAuthResponse(
        auth_url=anthropic_auth.build_authorize_url(
            client_id=settings.CLAUDE_OAUTH_CLIENT_ID,
            callback_url=settings.OAUTH_CALLBACK_URL,
            challenge=challenge,
            state=state,
        ),
        state=state,
        expires_in=settings.OAUTH_STATE_TTL_SECONDS,
        instructions=(
            "Open auth_url, approve access, then copy the code shown on the "
            "callback page and POST it with this same state to "
            "/v1/auth/anthropic/submit-code."
        ),
    )


@router.post("/auth/anthropic/submit-code", response_model=OAuthAccountResponse)
async def submit_code(
    body: SubmitCodeRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> OAuthAccountResponse:
    key = settings.require_encryption_key()
    try:
        state_data = anthropic_auth.decode_state(body.state, key)
    except anthropic_auth.OAuthError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        tokens = await anthropic_auth.exchange_code(
            code=body.code,
            state=body.state,
            verifier=state_data["code_verifier"],
            client_id=settings.CLAUDE_OAUTH_CLIENT_ID,
            callback_url=settings.OAUTH_CALLBACK_URL,
        )
    except anthropic_auth.OAuthError as exc:
        raise HTTPException(400, str(exc)) from exc

    profile = await anthropic_auth.fetch_profile(tokens["access_token"])
    row = await OAuthAccountRepo.upsert(
        session,
        provider="anthropic",
        provider_account_id=profile.get("uuid"),
        email=profile.get("email"),
        label=state_data.get("label") or profile.get("email") or "Anthropic account",
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token", ""),
        token_expires_at=datetime.now(UTC)
        + timedelta(seconds=tokens.get("expires_in", 3600)),
        key=key,
    )
    await session.commit()
    log.info("linked Anthropic account %s", row.id)
    return OAuthAccountResponse.model_validate(row, from_attributes=True)


@router.get("/auth/status", response_model=AuthStatusResponse)
async def auth_status(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AuthStatusResponse:
    rows = [row for row in await OAuthAccountRepo.list_all(session) if row.provider == "anthropic"]
    return AuthStatusResponse(
        configured=bool(rows) or bool(settings.ANTHROPIC_API_KEY),
        credential_kind=settings.credential_kind,
        accounts=[OAuthAccountResponse.model_validate(r, from_attributes=True) for r in rows],
    )


@router.get("/auth/providers", response_model=ProviderStatusResponse)
async def provider_status(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ProviderStatusResponse:
    effective = apply(settings, await load_overrides(session))
    anthropic_configured = bool(settings.ANTHROPIC_API_KEY) or any(
        row.provider == "anthropic" for row in await OAuthAccountRepo.list_all(session)
    )
    return ProviderStatusResponse(
        active_provider=effective.LLM_PROVIDER,
        anthropic_configured=anthropic_configured,
        openai_configured=(await OAuthAccountRepo.api_key_account(session, "openai")) is not None,
    )


@router.put("/auth/providers/{provider}", response_model=ProviderStatusResponse)
async def switch_provider(
    provider: str = Path(..., pattern="^(anthropic|openai)$"),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ProviderStatusResponse:
    """Switch the provider used by runs created after this request."""
    configured = (
        bool(settings.ANTHROPIC_API_KEY)
        or any(row.provider == "anthropic" for row in await OAuthAccountRepo.list_all(session))
        if provider == "anthropic"
        else (await OAuthAccountRepo.api_key_account(session, "openai")) is not None
    )
    if not configured:
        raise HTTPException(409, f"{provider.title()} is not configured")
    saved = await save_overrides(session, {"LLM_PROVIDER": provider})
    await session.commit()
    effective = apply(settings, saved)
    return ProviderStatusResponse(
        active_provider=effective.LLM_PROVIDER,
        anthropic_configured=bool(settings.ANTHROPIC_API_KEY)
        or any(row.provider == "anthropic" for row in await OAuthAccountRepo.list_all(session)),
        openai_configured=(await OAuthAccountRepo.api_key_account(session, "openai")) is not None,
    )


@router.put("/auth/openai/key", response_model=ProviderStatusResponse)
async def save_openai_key(
    body: ApiKeyRequest,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ProviderStatusResponse:
    key = body.api_key.strip()
    if not key.startswith("sk-"):
        raise HTTPException(422, "OpenAI API keys must start with sk-")
    await OAuthAccountRepo.save_api_key(
        session, provider="openai", secret=key, key=settings.require_encryption_key()
    )
    await session.commit()
    effective = apply(settings, await load_overrides(session))
    anthropic_configured = bool(settings.ANTHROPIC_API_KEY) or any(
        row.provider == "anthropic" for row in await OAuthAccountRepo.list_all(session)
    )
    return ProviderStatusResponse(
        active_provider=effective.LLM_PROVIDER,
        anthropic_configured=anthropic_configured,
        openai_configured=True,
    )


@router.delete("/auth/openai/key", status_code=204)
async def delete_openai_key(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> None:
    effective = apply(settings, await load_overrides(session))
    if effective.LLM_PROVIDER == "openai":
        raise HTTPException(409, "Switch to a configured provider before removing the active OpenAI key")
    await OAuthAccountRepo.delete_api_key(session, "openai")
    await session.commit()
