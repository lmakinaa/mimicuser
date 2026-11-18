"""Visitor-supplied Anthropic API keys (bring your own key).

In a public deployment a study runs on the visitor's key and nothing else. The
operator's credential is never a fallback there: serving third parties from a
subscription's rate limits is not permitted, and an operator API key would put
every anonymous visitor's study on the operator's bill.

A key is sealed with the deployment's ENCRYPTION_KEY as soon as it arrives, is
held only for the life of the work it pays for, and is never returned by the API.
"""
from __future__ import annotations

import re

from repositories.crypto import CryptoError, decrypt, encrypt

API_KEY_KIND = "api_key"
# Shape check only — whether the key actually works is asked of the API.
_KEY_SHAPE = re.compile(r"sk-ant-[A-Za-z0-9_-]{16,256}")


class InvalidApiKey(ValueError):
    """The value cannot be an Anthropic API key."""


class CredentialUnavailable(RuntimeError):
    """No usable visitor credential exists for this work."""


def is_well_formed_api_key(value: str) -> bool:
    """True if `value` has the shape of an Anthropic API key."""
    return bool(_KEY_SHAPE.fullmatch((value or "").strip()))


def seal_api_key(api_key: str, *, encryption_key: str) -> bytes:
    """Validate the key's shape and return it encrypted."""
    candidate = (api_key or "").strip()
    if not is_well_formed_api_key(candidate):
        raise InvalidApiKey(
            "That doesn't look like an Anthropic API key — it should start with sk-ant-."
        )
    return encrypt(candidate, encryption_key)


def _unseal(ciphertext: bytes, encryption_key: str) -> str:
    try:
        return decrypt(ciphertext, encryption_key)
    except CryptoError as exc:
        raise CredentialUnavailable(
            "The API key for this study can no longer be read. Start it again with your key."
        ) from exc


def choose_credential(
    *,
    run_ciphertext: bytes | None,
    study_ciphertext: bytes | None,
    encryption_key: str,
    public_mode: bool,
) -> tuple[str, str] | None:
    """The visitor credential for this work, or None to use the operator's.

    A run's own key wins over its study's. In public mode, having neither is an
    error rather than a silent switch to the operator.
    """
    sealed = run_ciphertext or study_ciphertext
    if sealed:
        return API_KEY_KIND, _unseal(sealed, encryption_key)
    if public_mode:
        raise CredentialUnavailable(
            "This deployment runs studies only on the visitor's own Anthropic API key."
        )
    return None
