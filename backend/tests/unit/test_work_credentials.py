"""Which credential pays for a piece of work.

A visitor's sealed key always wins. In public mode, work without one fails
loudly — the operator's credential is never even consulted, so a missing or
unreadable visitor key can never silently bill the operator.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

import services.run_executor as run_executor
from app.core.config import Settings
from services.byok import CredentialUnavailable, seal_api_key

pytestmark = pytest.mark.unit

ENCRYPTION_KEY = Fernet.generate_key().decode()
VISITOR_KEY = "sk-ant-api03-" + "A1b2_C3d4-" * 9


@pytest.fixture
def operator_lookups(monkeypatch):
    """Records every time the operator's credential is resolved."""
    calls: list[Settings] = []

    async def fake_resolve_credential(session, settings):
        calls.append(settings)
        return "oauth", "operator-token"

    monkeypatch.setattr(run_executor, "resolve_credential", fake_resolve_credential)
    return calls


def settings_for(*, public: bool) -> Settings:
    return Settings(PUBLIC_MODE=public, ENCRYPTION_KEY=ENCRYPTION_KEY)


def sealed(key: str = VISITOR_KEY) -> bytes:
    return seal_api_key(key, encryption_key=ENCRYPTION_KEY)


async def test_public_study_work_runs_on_the_visitor_key(operator_lookups):
    credential = await run_executor.resolve_credential_for(
        None, settings_for(public=True), study_ciphertext=sealed()
    )

    assert credential == ("api_key", VISITOR_KEY)
    assert operator_lookups == []


async def test_public_work_without_a_visitor_key_never_reaches_the_operator(operator_lookups):
    with pytest.raises(CredentialUnavailable):
        await run_executor.resolve_credential_for(None, settings_for(public=True))

    assert operator_lookups == []


async def test_private_work_without_a_visitor_key_uses_the_operator(operator_lookups):
    credential = await run_executor.resolve_credential_for(None, settings_for(public=False))

    assert credential == ("oauth", "operator-token")
    assert len(operator_lookups) == 1


async def test_a_visitor_key_wins_even_in_private_mode(operator_lookups):
    credential = await run_executor.resolve_credential_for(
        None, settings_for(public=False), run_ciphertext=sealed()
    )

    assert credential == ("api_key", VISITOR_KEY)
    assert operator_lookups == []
