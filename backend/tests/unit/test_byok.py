"""Bring-your-own-key credentials for public deployments.

A public visitor's study must run on the visitor's key and nothing else. The
operator's subscription is never a fallback in public mode — that would be
offering claude.ai rate limits to third parties, which Anthropic does not allow.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from services.byok import (
    CredentialUnavailable,
    InvalidApiKey,
    choose_credential,
    seal_api_key,
)

pytestmark = pytest.mark.unit

KEY = Fernet.generate_key().decode()
VISITOR_KEY = "sk-ant-api03-" + "A1b2_C3d4-" * 9
OTHER_KEY = "sk-ant-api03-" + "Z9y8_X7w6-" * 9


def test_a_sealed_key_round_trips_to_an_api_key_credential():
    sealed = seal_api_key(VISITOR_KEY, encryption_key=KEY)

    credential = choose_credential(
        run_ciphertext=sealed, study_ciphertext=None, encryption_key=KEY, public_mode=True
    )

    assert credential == ("api_key", VISITOR_KEY)


def test_the_sealed_value_does_not_contain_the_key():
    sealed = seal_api_key(VISITOR_KEY, encryption_key=KEY)

    assert VISITOR_KEY.encode() not in sealed


def test_a_run_key_wins_over_its_study_key():
    credential = choose_credential(
        run_ciphertext=seal_api_key(VISITOR_KEY, encryption_key=KEY),
        study_ciphertext=seal_api_key(OTHER_KEY, encryption_key=KEY),
        encryption_key=KEY,
        public_mode=True,
    )

    assert credential == ("api_key", VISITOR_KEY)


def test_a_study_run_uses_the_study_key():
    credential = choose_credential(
        run_ciphertext=None,
        study_ciphertext=seal_api_key(OTHER_KEY, encryption_key=KEY),
        encryption_key=KEY,
        public_mode=True,
    )

    assert credential == ("api_key", OTHER_KEY)


def test_public_mode_never_falls_back_to_the_operator():
    with pytest.raises(CredentialUnavailable):
        choose_credential(
            run_ciphertext=None, study_ciphertext=None, encryption_key=KEY, public_mode=True
        )


def test_private_mode_without_a_visitor_key_defers_to_the_operator():
    credential = choose_credential(
        run_ciphertext=None, study_ciphertext=None, encryption_key=KEY, public_mode=False
    )

    assert credential is None


def test_a_key_sealed_under_another_encryption_key_is_unavailable_not_a_crash():
    sealed = seal_api_key(VISITOR_KEY, encryption_key=Fernet.generate_key().decode())

    with pytest.raises(CredentialUnavailable):
        choose_credential(
            run_ciphertext=sealed, study_ciphertext=None, encryption_key=KEY, public_mode=True
        )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "sk-proj-abcdefghijklmnopqrstuvwxyz",
        "sk-ant-short",
        "sk-ant-api03-has space inside-abcdefghijklmnop",
        "sk-ant-api03-" + "a" * 400,
    ],
)
def test_malformed_keys_are_rejected_before_they_are_stored(value):
    with pytest.raises(InvalidApiKey):
        seal_api_key(value, encryption_key=KEY)
