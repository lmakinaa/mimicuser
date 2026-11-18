"""OAuth account persistence + the refresh-concurrency guard.

The row lock in `get_for_update` is the load-bearing detail. Anthropic rotates
the refresh token on every use and invalidates the previous one, so a herd of
concurrent 401s must collapse into exactly one rotation — otherwise all but one
caller sends a now-dead token, gets `invalid_grant`, and the account is broken
permanently.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repositories.crypto import decrypt, encrypt
from repositories.orm import OAuthAccountRow


class OAuthAccountRepo:
    @staticmethod
    async def upsert(
        session: AsyncSession,
        *,
        provider: str,
        provider_account_id: str | None,
        email: str | None,
        label: str,
        access_token: str,
        refresh_token: str,
        token_expires_at: datetime,
        key: str,
    ) -> OAuthAccountRow:
        row: OAuthAccountRow | None = None
        if provider_account_id:
            row = (
                await session.execute(
                    select(OAuthAccountRow).where(
                        OAuthAccountRow.provider == provider,
                        OAuthAccountRow.provider_account_id == provider_account_id,
                    )
                )
            ).scalar_one_or_none()

        if row is None:
            row = OAuthAccountRow(provider=provider, provider_account_id=provider_account_id)
            session.add(row)

        row.email = email or row.email
        row.label = label or row.label
        row.access_token_encrypted = encrypt(access_token, key)
        row.refresh_token_encrypted = encrypt(refresh_token, key)
        row.token_expires_at = token_expires_at

        await session.flush()
        return row

    @staticmethod
    async def get(session: AsyncSession, account_id: UUID) -> OAuthAccountRow | None:
        return await session.get(OAuthAccountRow, account_id)

    @staticmethod
    async def get_for_update(
        session: AsyncSession, account_id: UUID
    ) -> OAuthAccountRow | None:
        """Row-locked read. Concurrent refreshers block here until we commit."""
        return (
            await session.execute(
                select(OAuthAccountRow)
                .where(OAuthAccountRow.id == account_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

    @staticmethod
    async def newest(session: AsyncSession) -> OAuthAccountRow | None:
        return (
            await session.execute(
                select(OAuthAccountRow)
                .where(OAuthAccountRow.provider == "anthropic")
                .order_by(OAuthAccountRow.updated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def list_all(session: AsyncSession) -> list[OAuthAccountRow]:
        return list(
            (
                await session.execute(
                    select(OAuthAccountRow).order_by(OAuthAccountRow.created_at.desc())
                )
            ).scalars()
        )

    @staticmethod
    async def api_key_account(
        session: AsyncSession, provider: str
    ) -> OAuthAccountRow | None:
        return (
            await session.execute(
                select(OAuthAccountRow)
                .where(
                    OAuthAccountRow.provider == provider,
                    OAuthAccountRow.provider_account_id.is_(None),
                )
                .order_by(OAuthAccountRow.updated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def save_api_key(
        session: AsyncSession, *, provider: str, secret: str, key: str
    ) -> OAuthAccountRow:
        row = await OAuthAccountRepo.api_key_account(session, provider)
        if row is None:
            row = OAuthAccountRow(
                provider=provider,
                provider_account_id=None,
                label=f"{provider.title()} API key",
            )
            session.add(row)
        row.access_token_encrypted = encrypt(secret, key)
        row.refresh_token_encrypted = encrypt("", key)
        row.token_expires_at = None
        await session.flush()
        return row

    @staticmethod
    async def delete_api_key(session: AsyncSession, provider: str) -> None:
        row = await OAuthAccountRepo.api_key_account(session, provider)
        if row is not None:
            await session.delete(row)

    @staticmethod
    async def update_tokens(
        session: AsyncSession,
        row: OAuthAccountRow,
        *,
        access_token: str,
        refresh_token: str,
        token_expires_at: datetime,
        key: str,
    ) -> OAuthAccountRow:
        row.access_token_encrypted = encrypt(access_token, key)
        row.refresh_token_encrypted = encrypt(refresh_token, key)
        row.token_expires_at = token_expires_at
        await session.flush()
        return row

    # Decryption happens only here, at the repository boundary.
    @staticmethod
    def access_token(row: OAuthAccountRow, key: str) -> str:
        return decrypt(row.access_token_encrypted, key)

    @staticmethod
    def refresh_token(row: OAuthAccountRow, key: str) -> str:
        return decrypt(row.refresh_token_encrypted, key)
