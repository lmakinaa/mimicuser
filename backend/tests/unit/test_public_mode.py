"""Public mode: what an anonymous visitor may and may not do.

A public deployment runs studies on the visitor's own Anthropic key and exposes
only what a visitor needs to create a study and read its results. Everything
that reads or changes the operator's configuration or credentials is refused —
and refused before the request touches the database, so a locked endpoint
cannot leak through a slow query or a connection error.
"""
from __future__ import annotations

import httpx
import pytest

from app.api.v1.public_mode import forbid_in_public_mode, visitor_api_key
from app.core.config import Settings, get_settings
from app.main import create_app

pytestmark = pytest.mark.unit

RUN_ID = "7d2c1e7a-2b7c-4a55-9d3e-1f0f5d0a9b11"
VISITOR_KEY = "sk-ant-api03-" + "A1b2_C3d4-" * 9
RUN_BODY = {"product_url": "https://valsea.ai/", "audience": "Backend engineers"}
STUDY_BODY = {"product_url": "https://valsea.ai/", "segments": [{"audience": "CTOs", "count": 1}]}


def client_for(*, public: bool) -> httpx.AsyncClient:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(PUBLIC_MODE=public)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


OPERATOR_SURFACES = [
    ("POST", "/v1/auth/anthropic/start"),
    ("POST", "/v1/auth/anthropic/submit-code"),
    ("GET", "/v1/auth/status"),
    ("GET", "/v1/settings"),
    ("PATCH", "/v1/settings"),
    ("DELETE", "/v1/settings/CUSTOMER_MODEL"),
    ("POST", "/v1/personas"),
    ("PATCH", "/v1/personas/software_developer"),
    ("DELETE", "/v1/personas/software_developer"),
    ("POST", "/v1/personas/resolve"),
    ("GET", "/v1/projects"),
    ("POST", "/v1/projects"),
    ("GET", f"/v1/projects/{RUN_ID}"),
    ("PATCH", f"/v1/projects/{RUN_ID}"),
    ("DELETE", f"/v1/projects/{RUN_ID}"),
    ("POST", f"/v1/runs/{RUN_ID}/evaluate"),
    ("POST", f"/v1/studies/{RUN_ID}/aggregate"),
]


@pytest.mark.parametrize(("method", "path"), OPERATOR_SURFACES)
async def test_operator_surfaces_are_refused_in_public_mode(method: str, path: str) -> None:
    async with client_for(public=True) as client:
        response = await client.request(method, path, json={})

    assert response.status_code == 403


@pytest.mark.parametrize(("path", "body"), [("/v1/runs", RUN_BODY), ("/v1/studies", STUDY_BODY)])
async def test_new_work_without_a_visitor_key_is_refused(path: str, body: dict) -> None:
    async with client_for(public=True) as client:
        response = await client.post(path, json=body)

    assert response.status_code == 401


@pytest.mark.parametrize(("path", "body"), [("/v1/runs", RUN_BODY), ("/v1/studies", STUDY_BODY)])
async def test_a_malformed_visitor_key_is_rejected_before_anything_runs(path: str, body: dict) -> None:
    async with client_for(public=True) as client:
        response = await client.post(path, json=body, headers={"X-Anthropic-Api-Key": "sk-proj-nope"})

    assert response.status_code == 400
    assert "sk-proj-nope" not in response.text


async def test_private_mode_leaves_operator_surfaces_open() -> None:
    assert await forbid_in_public_mode(settings=Settings(PUBLIC_MODE=False)) is None


async def test_private_mode_does_not_require_a_visitor_key() -> None:
    assert await visitor_api_key(x_anthropic_api_key=None, settings=Settings(PUBLIC_MODE=False)) is None


async def test_public_mode_accepts_a_well_formed_key_with_surrounding_whitespace() -> None:
    key = await visitor_api_key(
        x_anthropic_api_key=f"  {VISITOR_KEY}\n", settings=Settings(PUBLIC_MODE=True)
    )

    assert key == VISITOR_KEY
