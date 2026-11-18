"""FastAPI application."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints import artifacts, auth_anthropic, personas, projects, runs, studies
from app.api.v1.endpoints import settings as settings_api
from app.core.config import get_settings
from engines import registry
from personas import load_catalogue


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    log = logging.getLogger("mimicuser")

    # Fail the boot, not a run three hours later.
    catalogue = load_catalogue()
    settings.require_encryption_key()
    settings.STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    settings.WORKDIR.mkdir(parents=True, exist_ok=True)

    log.info(
        "ready · %d personas · engines=%s · model=%s · storage=%s",
        len(catalogue.personas),
        registry.registered_names(),
        settings.DEFAULT_MODEL,
        settings.STORAGE_ROOT,
    )
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Synthetic Customer Research",
        description=(
            "Put a product in front of AI customers drawn from a target audience "
            "and get back evidence-based product feedback."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in (
        projects.router, runs.router, studies.router, personas.router,
        auth_anthropic.router, artifacts.router, settings_api.router,
    ):
        app.include_router(router, prefix="/v1", tags=["v1"])

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {"status": "ok", "engines": registry.registered_names()}

    return app


app = create_app()
