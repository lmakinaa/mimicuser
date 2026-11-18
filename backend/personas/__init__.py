"""Persona catalogue loader.

The catalogue is a data file, not code. Adding an audience is a JSON change.
It is validated against the Pydantic model at import time, so a malformed
catalogue fails the process boot rather than a run three hours later.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ValidationError

from domain.persona import Persona

CATALOGUE_PATH = Path(__file__).parent / "catalogue.json"


class Catalogue(BaseModel):
    version: int
    personas: list[Persona]


class CatalogueError(RuntimeError):
    """The shipped catalogue is invalid. Always a build-time bug."""


@lru_cache(maxsize=1)
def load_catalogue() -> Catalogue:
    raw = json.loads(CATALOGUE_PATH.read_text(encoding="utf-8"))
    try:
        catalogue = Catalogue.model_validate(raw)
    except ValidationError as exc:
        raise CatalogueError(f"{CATALOGUE_PATH} is invalid:\n{exc}") from exc

    ids = [p.id for p in catalogue.personas]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise CatalogueError(f"duplicate persona ids in catalogue: {sorted(duplicates)}")
    return catalogue


def all_personas() -> list[Persona]:
    return list(load_catalogue().personas)


def get_persona(persona_id: str) -> Persona | None:
    return next((p for p in load_catalogue().personas if p.id == persona_id), None)
