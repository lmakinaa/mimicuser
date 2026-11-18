"""Cross-process cancellation flags.

A run executes inside a worker process; the request to stop it arrives in the
API process. Redis is already the broker, so it carries the signal — no new
dependency, and no polling the database from inside the agent loop.

The flag is advisory and checked at tool-call boundaries, which means a
cancelled run stops *cleanly*: the browser closes, the video is saved, and the
event stream is flushed. Killing the process would lose all three, and the
partial session is still evidence worth keeping.
"""
from __future__ import annotations

import logging

import redis

from app.core.config import get_settings

log = logging.getLogger(__name__)

# Long enough to outlive any run, short enough that a stale flag cannot haunt a
# recycled id.
_TTL_S = 24 * 60 * 60


def _client() -> redis.Redis:
    return redis.Redis.from_url(get_settings().REDIS_URL)


def _key(kind: str, ident: str) -> str:
    return f"mimicuser:cancel:{kind}:{ident}"


def request_cancel(kind: str, ident: str) -> None:
    try:
        _client().setex(_key(kind, ident), _TTL_S, "1")
    except redis.RedisError:
        # Cancellation is best-effort. The caller has already marked the row,
        # so a queued run still won't start; only an in-flight one runs on.
        log.exception("could not set cancel flag for %s %s", kind, ident)


def is_cancelled(kind: str, ident: str) -> bool:
    try:
        return bool(_client().exists(_key(kind, ident)))
    except redis.RedisError:
        # Fail open: a Redis blip must not abort a healthy run.
        log.warning("cancel check failed for %s %s; assuming not cancelled", kind, ident)
        return False


def clear(kind: str, ident: str) -> None:
    try:
        _client().delete(_key(kind, ident))
    except redis.RedisError:
        log.warning("could not clear cancel flag for %s %s", kind, ident)
