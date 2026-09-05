"""FastAPI entry point.

All expensive work flows through :mod:`snapshot_service`, which in turn
flows through the cache-backed Databento client. The routes here are
deliberately thin: they shape JSON and push WebSocket diffs on the 60 s
refresh tick.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import List, Optional

print(f"gammagamma: importing main (PORT={os.getenv('PORT')})", flush=True)

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import SCHEDULE_TIMES, SCHEDULE_TZ, get_settings
from .snapshot_service import (
    get_bundle,
    prime_universe,
    reset_cache,
    summary_row,
)

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
except ImportError:  # pragma: no cover
    AsyncIOScheduler = None
    CronTrigger = None

logging.basicConfig(
    level=get_settings().log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("gammagamma")
print("gammagamma: imports complete, defining app", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    log.info(
        "Gammagamma boot — dataset=%s universe=%d scheduler=%s tz=%s",
        settings.opra_dataset,
        len(settings.universe),
        settings.schedule_enabled,
        SCHEDULE_TZ,
    )
    scheduler = None
    if settings.schedule_enabled and AsyncIOScheduler is not None:
        scheduler = AsyncIOScheduler(timezone=SCHEDULE_TZ)
        for hour, minute in SCHEDULE_TIMES:
            scheduler.add_job(
                _scheduled_refresh,
                CronTrigger(hour=hour, minute=minute, day_of_week="mon-fri"),
                id=f"snapshot-{hour:02d}{minute:02d}",
                replace_existing=True,
            )
        scheduler.start()
        log.info(
            "Scheduled snapshots at %s (%s)",
            ", ".join(f"{h:02d}:{m:02d}" for h, m in SCHEDULE_TIMES),
            SCHEDULE_TZ,
        )
        # prime once on boot so the dashboard has data immediately
        asyncio.create_task(asyncio.to_thread(prime_universe))
    else:
        log.info("Scheduler disabled — bundles built on-demand")
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


async def _scheduled_refresh() -> None:
    log.info("Scheduled refresh starting")
    reset_cache()
    await asyncio.to_thread(prime_universe)
    # push to any live WS subscribers
    for sym in get_settings().universe:
        try:
            bundle = get_bundle(sym)
            await _WS.broadcast(sym, bundle.to_json())
        except Exception as exc:  # noqa: BLE001
            log.warning("broadcast %s failed: %s", sym, exc)
    log.info("Scheduled refresh complete")


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
    try:
        return get_bundle(sym, expiry_filter=flt).to_json()
    except Exception as exc:  # noqa: BLE001
        log.exception("ticker %s build failed: %s", sym, exc)
        raise HTTPException(
            status_code=502,
            detail={
                "error": type(exc).__name__,
                "message": str(exc),
                "symbol": sym,
                "hint": "Check backend logs. Common causes: Databento auth, "
                "symbology (SPX options may need a different parent symbol), "
                "or OPRA historical lag during market hours.",
            },
        )


@app.get("/api/levels/{symbol}")
def levels(
    symbol: str,
    expiry: Optional[str] = Query(None, pattern="^(0dte|weekly|monthly|leaps|all)$"),
) -> dict:
    """Lightweight levels-only payload for downstream consumers
    (MotiveWave studies, other agents). Returns just spot + structural
    levels, no rows / flow / playbook. ~500 bytes vs the full bundle.
    """
    sym = symbol.upper()
    if sym not in get_settings().universe:
        raise HTTPException(404, f"{sym} not in universe")
    flt = None if expiry in (None, "all") else expiry
    try:
        b = get_bundle(sym, expiry_filter=flt)
    except Exception as exc:  # noqa: BLE001
        log.exception("levels %s build failed: %s", sym, exc)
        raise HTTPException(502, f"{type(exc).__name__}: {exc}")
    return {
        "underlying": b.underlying,
        "asof": b.asof.isoformat(),
        "expiry_filter": flt or "all",
        "spot": b.spot,
        "call_wall": b.levels.call_wall,
        "put_wall": b.levels.put_wall,
        "gamma_flip": b.levels.gamma_flip,
        "gvwap": b.levels.gvwap,
        "major_call_walls": b.levels.major_call_walls,
        "major_put_walls": b.levels.major_put_walls,
        "total_gex": b.total_gex,
    }


@app.get("/api/range/{symbol}")
def range_single(symbol: str) -> dict:
    """Daily expected-range analysis for one ticker.

    Combines IV width (1-SD move from ATM IV × √T), skew shape
    (25-delta strikes), and GEX regime (spot vs gamma_flip) into a
    single view of where the day is likely to range and where it will
    turn. Anchors on the 0DTE / nearest expiry.
    """
    sym = symbol.upper()
    if sym not in get_settings().universe:
        raise HTTPException(404, f"{sym} not in universe")
    from .range import build_daily_range_from_bundle
    try:
        b = get_bundle(sym, expiry_filter="0dte")
    except Exception as exc:  # noqa: BLE001
        log.exception("range %s build failed: %s", sym, exc)
        raise HTTPException(502, f"{type(exc).__name__}: {exc}")
    return build_daily_range_from_bundle(b).to_json()


@app.get("/api/range")
def range_all() -> dict:
    """Daily range summaries for every ticker in the universe."""
    from .range import build_daily_range_from_bundle
    out = []
    for sym in get_settings().universe:
        try:
            b = get_bundle(sym, expiry_filter="0dte")
            out.append(build_daily_range_from_bundle(b).to_json())
        except Exception as exc:  # noqa: BLE001
            log.warning("range for %s failed: %s", sym, exc)
            out.append({"underlying": sym, "error": str(exc)})
    return {"rows": out}


@app.post("/api/cache/reset")
def cache_reset() -> dict:
    reset_cache()
    return {"ok": True}


@app.post("/api/refresh")
async def refresh_now() -> dict:
    """Manually trigger a full-universe refresh (same as a scheduled tick)."""
    await _scheduled_refresh()
    return {"ok": True, "universe": get_settings().universe}


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


