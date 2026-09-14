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

    def invalidate_namespace(self, namespace: str) -> int:
        """Drop every entry in ``namespace`` from both tiers.

        Used by the scheduled refresh: clearing only the bundle cache left
        the chain fetch sitting on its own (much longer) TTL, so every
        "refresh" rebuilt the same bundle from the same cached chain.

        The matching ``stale__<namespace>`` entries are left alone — they
        are the last-known-good fallback for :func:`cached_call` when a
        loader raises.
        """
        prefix = f"{namespace}_"
        dropped = 0
        with self._lock:
            for k in [k for k in self._mem if k.startswith(prefix)]:
                self._mem.pop(k, None)
                dropped += 1
            try:
                paths = list(CACHE_DIR.glob(f"{prefix}*.pkl"))
            except OSError:
                paths = []
            for path in paths:
                try:
                    path.unlink()
                except OSError:
                    pass
        return dropped


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


def _stale_namespace(namespace: str) -> str:
    """Namespace holding the last-known-good value for ``namespace``.

    Kept as a *prefix* rather than a key suffix so that
    :meth:`TTLCache.invalidate_namespace` — which matches on the
    ``"<namespace>_"`` filename prefix — cannot sweep the stale copies away
    along with the live entries. They are the fallback when a loader raises.
    """
    return f"stale__{namespace}"


_CONTAINER_TYPES = (list, tuple, set, frozenset, dict, str, bytes)


def _is_empty(value: Any) -> bool:
    """Treat empty collections / empty DataFrames as failed fetches.

    Swallowed errors inside loaders typically return empty — caching those
    for the full TTL poisons the cache. We skip caching empties so the
    next request retries.

    Tuples of containers count as empty only when *every* element is empty.
    This is the shape every chain client returns — ``(defs, quotes, oi)``.
    A plain ``len() == 0`` check misses it: a failed Schwab/Yahoo/marketdata
    fetch hands back ``([], [], {})``, whose length is 3, so the failure used
    to be cached (memory *and* disk) for the full TTL and the dashboard
    stayed empty until the entry expired — across restarts, since the disk
    tier is mounted.
    """
    if value is None:
        return True
    if hasattr(value, "empty"):  # pandas DataFrame / Series
        try:
            return bool(value.empty)
        except Exception:  # noqa: BLE001
            return False
    if isinstance(value, (list, tuple, set, frozenset)):
        if len(value) == 0:
            return True
        # Only recurse into a container-of-containers (the (defs, quotes, oi)
        # bundle). A populated list of quotes/rows is never "empty".
        if all(
            v is None or isinstance(v, _CONTAINER_TYPES) or hasattr(v, "empty")
            for v in value
        ):
            return all(_is_empty(v) for v in value)
        return False
    if isinstance(value, (dict, str, bytes)):
        return len(value) == 0
    return False


def cached_call(
    namespace: str,
    key: str,
    ttl: timedelta,
    loader: Callable[[], Any],
    *,
    allow_stale_on_error: bool = True,
    cache_empty: bool = False,
) -> Any:
    """Run ``loader`` only if the cache misses. Serialise concurrent misses.

    Concurrent requests for the same (namespace, key) wait on a per-key lock
    so the loader runs exactly once. This matters when a dashboard burst hits
    the API before the first fetch has populated the cache.

    Empty results are NOT cached by default (``cache_empty=False``) because
    loaders commonly return an empty collection on transient failure — we
    want the next call to retry rather than serve stale empties for hours.
    """
    existing = _CACHE.get(namespace, key)
    if existing is not None and not _is_empty(existing):
        return existing

    with _key_lock(namespace, key):
        existing = _CACHE.get(namespace, key)
        if existing is not None and not _is_empty(existing):
            return existing

        try:
            value = loader()
        except Exception as exc:
            if allow_stale_on_error:
                stale = _CACHE.get(_stale_namespace(namespace), key)
                if stale is not None and not _is_empty(stale):
                    log.warning(
                        "loader %s:%s failed (%s); serving stale value",
                        namespace, key, exc,
                    )
                    return stale
            raise

        if _is_empty(value) and not cache_empty:
            log.info(
                "loader %s:%s returned empty; skipping cache so next call retries",
                namespace, key,
            )
            return value

        _CACHE.set(namespace, key, value, ttl)
        _CACHE.set(_stale_namespace(namespace), key, value, timedelta(days=7))
        return value


# Namespaces that hold raw option-chain payloads. The scheduled refresh
# clears these so each tick re-fetches from the vendor instead of
# replaying whatever was cached at boot.
CHAIN_NAMESPACES = (
    "schwab_chain",
    "yahoo_chain",
    "md_chain",
    "definitions",
    "nbbo",
    "oi",
    "trades",
    "spot_live",
)


def invalidate_chain_caches() -> int:
    """Clear every cached chain payload. Returns the entry count dropped."""
    total = 0
    for ns in CHAIN_NAMESPACES:
        total += _CACHE.invalidate_namespace(ns)
    log.info("chain caches invalidated (%d in-memory entries dropped)", total)
    return total


__all__ = [
    "CHAIN_NAMESPACES",
    "TTLCache",
    "cache",
    "cached_call",
    "invalidate_chain_caches",
]
