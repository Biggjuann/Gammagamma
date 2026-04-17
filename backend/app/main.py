"""FastAPI entry point.

All expensive work flows through :mod:`snapshot_service`, which in turn
flows through the cache-backed Databento client. The routes here are
deliberately thin: they shape JSON and push WebSocket diffs on the 60 s
refresh tick.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .snapshot_service import (
    get_bundle,
    prime_universe,
    reset_cache,
    summary_row,
)

logging.basicConfig(level=get_settings().log_level)
log = logging.getLogger("gammagamma")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info(
        "Gammagamma boot — dataset=%s refresh=%ss universe=%d",
        settings.opra_dataset,
        settings.refresh_interval_seconds,
        len(settings.universe),
    )
    task = asyncio.create_task(_refresh_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Gammagamma API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------
# REST
# ---------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "universe": len(get_settings().universe)}


@app.get("/api/universe")
def universe() -> dict:
    return {"symbols": get_settings().universe}


@app.get("/api/summary")
def summary() -> dict:
    rows = [summary_row(sym) for sym in get_settings().universe]
    return {"rows": rows}


@app.get("/api/ticker/{symbol}")
def ticker(
    symbol: str,
    expiry: Optional[str] = Query(None, pattern="^(0dte|weekly|monthly|leaps|all)$"),
) -> dict:
    sym = symbol.upper()
    if sym not in get_settings().universe:
        raise HTTPException(404, f"{sym} not in universe")
    flt = None if expiry in (None, "all") else expiry
    return get_bundle(sym, expiry_filter=flt).to_json()


@app.post("/api/cache/reset")
def cache_reset() -> dict:
    reset_cache()
    return {"ok": True}


# ---------------------------------------------------------------------
# WebSocket — per-ticker live feed
# ---------------------------------------------------------------------
class _WSRegistry:
    def __init__(self) -> None:
        self._subs: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def add(self, sym: str, ws: WebSocket) -> None:
        async with self._lock:
            self._subs.setdefault(sym, set()).add(ws)

    async def drop(self, sym: str, ws: WebSocket) -> None:
        async with self._lock:
            if sym in self._subs:
                self._subs[sym].discard(ws)

    async def broadcast(self, sym: str, payload: dict) -> None:
        dead: List[WebSocket] = []
        async with self._lock:
            subs = list(self._subs.get(sym, ()))
        for ws in subs:
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        if dead:
            async with self._lock:
                for d in dead:
                    self._subs.get(sym, set()).discard(d)


_WS = _WSRegistry()


@app.websocket("/ws/ticker/{symbol}")
async def ws_ticker(ws: WebSocket, symbol: str) -> None:
    sym = symbol.upper()
    if sym not in get_settings().universe:
        await ws.close(code=4404)
        return
    await ws.accept()
    await _WS.add(sym, ws)
    try:
        # initial push
        await ws.send_json(get_bundle(sym).to_json())
        while True:
            # drain any inbound pings; the refresh loop is the producer
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        await _WS.drop(sym, ws)


# ---------------------------------------------------------------------
# Background refresh
# ---------------------------------------------------------------------
async def _refresh_loop() -> None:
    settings = get_settings()
    while True:
        try:
            await asyncio.to_thread(prime_universe)
            # push each bundle to subscribers
            for sym in settings.universe:
                try:
                    bundle = get_bundle(sym)
                except Exception as exc:  # noqa: BLE001
                    log.warning("refresh %s failed: %s", sym, exc)
                    continue
                await _WS.broadcast(sym, bundle.to_json())
        except Exception as exc:  # noqa: BLE001
            log.exception("refresh loop error: %s", exc)
        await asyncio.sleep(settings.refresh_interval_seconds)
