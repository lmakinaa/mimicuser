"""Serve screenshots and recordings from the StorageProvider.

Path traversal is rejected by `LocalStorageProvider._resolve`, not sanitised
here — the storage layer owns that boundary and there must be exactly one.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.core.config import Settings, get_settings
from storage.base import UnsafeKey
from storage.local import LocalStorageProvider

router = APIRouter()

_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webm": "video/webm",
    ".json": "application/json",
}


@router.get("/artifacts/{key:path}")
async def get_artifact(key: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    storage = LocalStorageProvider(settings.STORAGE_ROOT)
    try:
        path = storage.local_path(key)
    except UnsafeKey as exc:
        raise HTTPException(400, str(exc)) from exc

    if not path.is_file():
        raise HTTPException(404, "Artifact not found")

    return FileResponse(
        path,
        media_type=_CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
