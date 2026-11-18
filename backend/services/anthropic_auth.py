"""Anthropic OAuth: PKCE state, code exchange, and concurrency-safe refresh.

Ported from `refs/vl-app-…/app/api/v2/endpoints/oauth_flow.py`, with org/user
scoping removed. See `docs/05-anthropic-oauth.md` for the reasoning behind each
choice; the two that matter most are the stateless signed state (no Redis on the
auth path) and the row-locked refresh (Anthropic rotates and invalidates the
refresh token on every use).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from repositories.oauth_repo import OAuthAccountRepo
from repositories.orm import OAuthAccountRow

log = logging.getLogger(__name__)

TOKEN_URL = "https://api.anthropic.com/v1/oauth/token"
PROFILE_URL = "https://api.anthropic.com/api/oauth/profile"
AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
SCOPES = "org:create_api_key user:profile user:inference"

# If the stored token still has more than this much life once we hold the lock,
# a concurrent caller already rotated — reuse rather than rotating again.
REFRESH_FRESHNESS_MARGIN_S = 60
# Refresh proactively at run start so a long session can't expire mid-run.
REFRESH_MARGIN_S = 300


class OAuthError(RuntimeError):
    pass


class ReLinkRequired(OAuthError):
    """The refresh token is dead. Retrying will never help."""


# -- signed state -----------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def encode_state(payload: dict, key: str) -> str:
    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64url(hmac.new(key.encode(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def decode_state(state: str, key: str) -> dict:
    """Verify signature and expiry.

    Every failure mode returns the same message so a probe cannot distinguish
    tampered from expired from malformed.
    """
    generic = OAuthError("Invalid or expired OAuth state")
    try:
        body, sig = state.split(".", 1)
    except ValueError as exc:
        raise generic from exc

    expected = _b64url(hmac.new(key.encode(), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        raise generic

    try:
        payload = json.loads(_b64url_decode(body))
    except (ValueError, json.JSONDecodeError) as exc:
        raise generic from exc

    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(time.time()):
        raise generic
    return payload


def generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def build_authorize_url(*, client_id: str, callback_url: str, challenge: str, state: str) -> str:
    from urllib.parse import quote

    return (
        f"{AUTHORIZE_URL}"
        f"?code=true"
        f"&client_id={client_id}"
        f"&response_type=code"
        f"&redirect_uri={quote(callback_url, safe='')}"
        f"&scope={quote(SCOPES)}"
        f"&state={state}"
        f"&code_challenge={challenge}"
        f"&code_challenge_method=S256"
    )


# -- token exchange ---------------------------------------------------------


async def exchange_code(
    *, code: str, state: str, verifier: str, client_id: str, callback_url: str
) -> dict:
    # Anthropic renders the code with a `#fragment` suffix on the callback page;
    # users paste the whole thing.
    raw_code = code.split("#")[0].strip()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "code": raw_code,
                "state": state,
                "redirect_uri": callback_url,
                "client_id": client_id,
                "code_verifier": verifier,
            },
            headers={"content-type": "application/json", "accept": "application/json"},
        )
    if response.status_code != 200:
        log.error("oauth token exchange failed: %s %s", response.status_code, response.text)
        raise OAuthError(f"Token exchange failed ({response.status_code}). Try linking again.")
    return response.json()


async def fetch_profile(access_token: str) -> dict:
    """Best effort — a missing profile must not fail the link."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                PROFILE_URL,
                headers={
                    "authorization": f"Bearer {access_token}",
                    "accept": "application/json",
                },
            )
        if response.status_code == 200:
            return response.json().get("account", {}) or {}
    except httpx.HTTPError as exc:
        log.warning("oauth profile fetch failed (non-fatal): %s", exc)
    return {}


async def _refresh(refresh_token: str, client_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
            headers={"content-type": "application/json", "accept": "application/json"},
        )
    if response.status_code == 200:
        return response.json()

    log.error("oauth refresh failed: %s %s", response.status_code, response.text)
    if "invalid_grant" in response.text or response.status_code in (400, 401):
        raise ReLinkRequired(
            "The stored Anthropic refresh token is no longer valid. Link the "
            "account again at /v1/auth/anthropic/start."
        )
    raise OAuthError(f"Token refresh failed: {response.status_code}")


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def access_token_for(
    session: AsyncSession,
    account_id: UUID,
    *,
    client_id: str,
    key: str,
) -> str:
    """Return a usable access token, refreshing under a row lock if needed."""
    row = await OAuthAccountRepo.get_for_update(session, account_id)
    if row is None:
        raise OAuthError("No Anthropic account is linked.")

    expires_at = _aware(row.token_expires_at)
    now = datetime.now(UTC)

    if expires_at and expires_at - now > timedelta(seconds=REFRESH_MARGIN_S):
        return OAuthAccountRepo.access_token(row, key)

    # We hold the lock. A concurrent caller may already have rotated while we
    # waited — re-check freshness before spending the (single-use) refresh token.
    if expires_at and expires_at - now > timedelta(seconds=REFRESH_FRESHNESS_MARGIN_S):
        log.info("token still fresh for account %s after lock; reusing", account_id)
        return OAuthAccountRepo.access_token(row, key)

    refresh_token = OAuthAccountRepo.refresh_token(row, key)
    if not refresh_token:
        raise ReLinkRequired("No refresh token is stored for this account.")

    data = await _refresh(refresh_token, client_id)
    new_access = data["access_token"]
    await OAuthAccountRepo.update_tokens(
        session,
        row,
        access_token=new_access,
        # Anthropic rotates this on every use; storing the old one guarantees
        # the next refresh fails.
        refresh_token=data.get("refresh_token", refresh_token),
        token_expires_at=now + timedelta(seconds=data.get("expires_in", 3600)),
        key=key,
    )
    log.info("refreshed Anthropic token for account %s", account_id)
    return new_access


async def newest_account(session: AsyncSession) -> OAuthAccountRow | None:
    return await OAuthAccountRepo.newest(session)
