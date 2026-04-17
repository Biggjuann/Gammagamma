"""Two-tier TTL cache with per-key locking.

Databento is billed per byte, so we route every call through this layer.

Features:
* Memory + disk tiers (disk survives restart).
* Per-key async lock: concurrent misses for the same key serialize into one
  loader call instead of N parallel fetches.
* Stale-on-error fallback: if the loader raises and a stale value exists on
  disk, return the stale value and log a warning.
"""
from __future__ import annotations

import hashlib
import logging
import os
import pickle
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional

log = logging.getLogger(__name__)

CACHE_DIR = Path(os.getenv("GAMMAGAMMA_CACHE_DIR", ".cache")).resolve()
CACHE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class _Entry:
    value: Any
    expires_at: float


class TTLCache:
    def __init__(self) -> None:
        self._mem: Dict[str, _Entry] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _hash(namespace: str, key: str) -> str:
        h = hashlib.sha1(f"{namespace}::{key}".encode()).hexdigest()[:32]
        return f"{namespace}_{h}"

    def _disk_path(self, namespace: str, key: str) -> Path:
        return CACHE_DIR / f"{self._hash(namespace, key)}.pkl"

    def get(self, namespace: str, key: str) -> Optional[Any]:
        k = self._hash(namespace, key)
        with self._lock:
            entry = self._mem.get(k)
            if entry and entry.expires_at > time.time():
                return entry.value

            path = self._disk_path(namespace, key)
            if path.exists():
                try:
                    with path.open("rb") as fh:
                        disk_entry: _Entry = pickle.load(fh)
                except (pickle.PickleError, EOFError, OSError) as exc:
                    log.warning("cache disk read failed for %s: %s", k, exc)
                    return None
                if disk_entry.expires_at > time.time():
                    self._mem[k] = disk_entry
                    return disk_entry.value
                try:
                    path.unlink()
                except OSError:
                    pass
        return None

    def set(self, namespace: str, key: str, value: Any, ttl: timedelta) -> None:
        expires_at = time.time() + ttl.total_seconds()
        entry = _Entry(value=value, expires_at=expires_at)
        k = self._hash(namespace, key)
        with self._lock:
            self._mem[k] = entry
            try:
                with self._disk_path(namespace, key).open("wb") as fh:
                    pickle.dump(entry, fh, protocol=pickle.HIGHEST_PROTOCOL)
            except OSError as exc:
                log.warning("cache disk write failed for %s: %s", k, exc)

    def invalidate(self, namespace: str, key: str) -> None:
        k = self._hash(namespace, key)
        with self._lock:
            self._mem.pop(k, None)
            p = self._disk_path(namespace, key)
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass


_CACHE = TTLCache()

# Per-key locks for cached_call. We keep an interning dict so the same key
# maps to the same Lock across callers; prevents N parallel fetches on a miss.
_KEY_LOCKS: Dict[str, threading.Lock] = {}
_KEY_LOCKS_GUARD = threading.Lock()


def _key_lock(namespace: str, key: str) -> threading.Lock:
    composite = f"{namespace}::{key}"
    with _KEY_LOCKS_GUARD:
        lock = _KEY_LOCKS.get(composite)
        if lock is None:
            lock = threading.Lock()
            _KEY_LOCKS[composite] = lock
    return lock


def cache() -> TTLCache:
    return _CACHE


def cached_call(
    namespace: str,
    key: str,
    ttl: timedelta,
    loader: Callable[[], Any],
    *,
    allow_stale_on_error: bool = True,
) -> Any:
    """Run ``loader`` only if the cache misses. Serialise concurrent misses.

    Concurrent requests for the same (namespace, key) wait on a per-key lock
    so the loader runs exactly once. This matters when a dashboard burst hits
    the API before the first fetch has populated the cache.
    """
    existing = _CACHE.get(namespace, key)
    if existing is not None:
        return existing

    with _key_lock(namespace, key):
        # recheck inside the lock — another thread may have populated the
        # cache while we were waiting.
        existing = _CACHE.get(namespace, key)
        if existing is not None:
            return existing

        try:
            value = loader()
        except Exception as exc:
            if allow_stale_on_error:
                stale = _CACHE.get(namespace, f"stale::{key}")
                if stale is not None:
                    log.warning(
                        "loader %s:%s failed (%s); serving stale value",
                        namespace, key, exc,
                    )
                    return stale
            raise
        _CACHE.set(namespace, key, value, ttl)
        _CACHE.set(namespace, f"stale::{key}", value, timedelta(days=7))
        return value
