"""Engine resolution and the OAuth signed-state codec."""
from __future__ import annotations

import time

import pytest
from cryptography.fernet import Fernet

from app.core.config import Settings
from domain.research import ModelSpec
from engines import registry
from services import anthropic_auth

pytestmark = pytest.mark.unit

KEY = Fernet.generate_key().decode()


# -- registry ---------------------------------------------------------------


def test_claude_agent_engine_is_registered():
    assert "claude_agent" in registry.registered_names()


def test_unknown_engine_name_raises():
    with pytest.raises(registry.UnknownEngine):
        registry.get("does_not_exist")


def test_unknown_provider_raises_instead_of_silently_falling_back():
    """The internal reference's `_DEFAULT_ENGINE` shortcut is the bug we're
    avoiding: an unroutable model must be a loud configuration error."""
    spec = ModelSpec(model="whatever", engine="", provider="unknown")
    with pytest.raises(registry.UnknownEngine, match="no default engine"):
        registry.for_model(spec)


def test_engine_declines_a_provider_it_cannot_serve():
    spec = ModelSpec(model="gpt-x", engine="claude_agent", provider="openai")
    with pytest.raises(registry.UnsupportedModel):
        registry.for_model(spec)


def test_anthropic_provider_resolves_to_the_default_engine():
    spec = ModelSpec(model="claude-opus-5", engine="", provider="anthropic")
    assert registry.for_model(spec).name == "claude_agent"


def test_openai_provider_resolves_to_the_openai_engine():
    spec = ModelSpec(model="gpt-4o", engine="", provider="openai")
    assert registry.for_model(spec).name == "openai"


def test_provider_model_catalogs_are_hardcoded_and_provider_specific():
    settings = Settings()
    assert settings.model_for_provider("anthropic", "customer") in settings.ANTHROPIC_MODELS
    assert settings.model_for_provider("openai", "customer") in settings.OPENAI_MODELS
    assert all(model.startswith("claude-") for model in settings.ANTHROPIC_MODELS)
    assert all(model.startswith(("gpt-", "o")) for model in settings.OPENAI_MODELS)


def test_explicit_override_wins():
    spec = ModelSpec(model="claude-opus-5", engine="", provider="anthropic")
    assert registry.for_model(spec, override="claude_agent").name == "claude_agent"


# -- oauth state ------------------------------------------------------------


def test_state_roundtrip():
    payload = {"code_verifier": "v", "exp": int(time.time()) + 60, "jti": "j"}
    decoded = anthropic_auth.decode_state(anthropic_auth.encode_state(payload, KEY), KEY)
    assert decoded["code_verifier"] == "v"


def test_tampered_state_is_rejected():
    state = anthropic_auth.encode_state({"exp": int(time.time()) + 60}, KEY)
    body, sig = state.split(".", 1)
    with pytest.raises(anthropic_auth.OAuthError):
        anthropic_auth.decode_state(f"{body}x.{sig}", KEY)


def test_state_signed_with_another_key_is_rejected():
    state = anthropic_auth.encode_state({"exp": int(time.time()) + 60}, KEY)
    with pytest.raises(anthropic_auth.OAuthError):
        anthropic_auth.decode_state(state, Fernet.generate_key().decode())


def test_expired_state_is_rejected():
    state = anthropic_auth.encode_state({"exp": int(time.time()) - 1}, KEY)
    with pytest.raises(anthropic_auth.OAuthError):
        anthropic_auth.decode_state(state, KEY)


@pytest.mark.parametrize("bad", ["", "no-dot", "a.b.c.d", "!!!.???"])
def test_malformed_state_is_rejected_uniformly(bad):
    """Every failure mode returns the same message so a probe learns nothing."""
    with pytest.raises(anthropic_auth.OAuthError) as exc:
        anthropic_auth.decode_state(bad, KEY)
    assert "Invalid or expired OAuth state" in str(exc.value)


def test_pkce_challenge_is_the_s256_of_the_verifier():
    import base64
    import hashlib

    verifier, challenge = anthropic_auth.generate_pkce()
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert challenge == expected


def test_authorize_url_carries_pkce_and_scopes():
    _, challenge = anthropic_auth.generate_pkce()
    url = anthropic_auth.build_authorize_url(
        client_id="cid", callback_url="https://cb", challenge=challenge, state="st"
    )
    assert "code_challenge_method=S256" in url
    assert f"code_challenge={challenge}" in url
    assert "user%3Ainference" in url


# -- token encryption -------------------------------------------------------


def test_tokens_roundtrip_through_encryption():
    from repositories.crypto import decrypt, encrypt

    assert decrypt(encrypt("secret-token", KEY), KEY) == "secret-token"


def test_decrypting_with_a_rotated_key_asks_for_a_relink():
    from repositories.crypto import CryptoError, decrypt, encrypt

    blob = encrypt("t", KEY)
    with pytest.raises(CryptoError, match="re-linked"):
        decrypt(blob, Fernet.generate_key().decode())
