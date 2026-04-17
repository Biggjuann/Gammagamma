"""Lightweight two-tier (memory + disk) TTL cache.

Databento is billed per byte, so every Gammagamma call goes through this
layer. Semantics:

* **Memory tier** — dict keyed by ``(namespace, key)`` with expiry timestamp.
  Zero-cost hits, lost on restart.
* **Disk tier** — pickled payloads under ``CACHE_DIR`` (default
  ``./.cache``). Survives restarts; reloaded lazily on miss.

Use :func:`cached_call` as the one-stop wrapper::

    chain = cached_call(
        namespace="definitions",
        key=f"{dataset}:{symbol}:{date}",
        ttl=timedelta(hours=24),
        loader=lambda: client.timeseries.get_range(...),
    )

Long TTLs are the default — definitions change daily, OI updates overnight,
so we only pay for truly fresh data.
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

    # ---- key hashing ---------------------------------------------------
    @staticmethod
    def _hash(namespace: str, key: str) -> str:
        h = hashlib.sha1(f"{namespace}::{key}".encode()).hexdigest()[:32]
        return f"{namespace}_{h}"

    def _disk_path(self, namespace: str, key: str) -> Path:
        return CACHE_DIR / f"{self._hash(namespace, key)}.pkl"

    # ---- core --------------------------------------------------------
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
    """Run ``loader`` only if the cache misses.

    If the loader raises and a stale value exists on disk, return the stale
    value (and log). This keeps the dashboard alive through transient
    Databento outages without burning extra quota on retries.
    """
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
                    "loader %s:%s failed (%s); serving stale value", namespace, key, exc
                )
                return stale
        raise
    _CACHE.set(namespace, key, value, ttl)
    _CACHE.set(namespace, f"stale::{key}", value, timedelta(days=7))
    return value
