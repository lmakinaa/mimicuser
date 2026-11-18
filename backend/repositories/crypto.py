"""Symmetric encryption for tokens at rest.

Plaintext exists only inside this module and the repository functions that call
it — never on an ORM attribute, where it could be swept into a log line or a
response model by accident.
"""
from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken


class CryptoError(RuntimeError):
    pass


@lru_cache(maxsize=4)
def _cipher(key: str) -> Fernet:
    if not key:
        # Fail loudly at use time rather than silently encrypting with b"".
        raise CryptoError("ENCRYPTION_KEY is not configured")
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise CryptoError(
            "ENCRYPTION_KEY is not a valid Fernet key. Generate one with:\n"
            "  python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        ) from exc


def encrypt(plaintext: str, key: str) -> bytes:
    return _cipher(key).encrypt(plaintext.encode("utf-8"))


def decrypt(ciphertext: bytes, key: str) -> str:
    try:
        return _cipher(key).decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise CryptoError(
            "stored token could not be decrypted — ENCRYPTION_KEY has probably "
            "changed since it was written; the account must be re-linked"
        ) from exc
