"""In-process snapshot cache.

Builds a full ``ChainSnapshot + levels + signals + flow`` bundle per
ticker on demand and keeps it in memory until the refresh tick expires.
Every downstream consumer (REST handlers, WebSocket broadcaster,
scheduler) calls :func:`get_bundle` — Databento is only touched when the
underlying cache misses, so ten simultaneous dashboard tabs share one
fetch.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from dataclasses import replace

from .config import TICKER_ALIASES, get_settings
from .flow import FlowRow, build_flow
from .gex import ChainRow, ChainSnapshot, build_chain_snapshot, gex_by_expiry, gex_by_strike
from .playbook import Playbook, build_playbook
from .signals import SignalBundle, StructuralLevels, compute_signals, structural_levels

log = logging.getLogger(__name__)


@dataclass
class Bundle:
    underlying: str
    asof: datetime
    spot: float
    total_gex: float
    total_dex: float
    total_vanna: float
    total_charm: float
    levels: StructuralLevels
    signals: SignalBundle
    rows: List[dict]
    per_strike: Dict[str, Dict[str, float]]
    per_expiry: Dict[str, float]
    flow: List[FlowRow]
    playbook: Playbook

    def to_json(self) -> dict:
        return {
            "underlying": self.underlying,
            "asof": self.asof.isoformat(),
            "spot": self.spot,
            "totals": {
                "gex": self.total_gex,
                "dex": self.total_dex,
                "vanna": self.total_vanna,
                "charm": self.total_charm,
            },
            "levels": {
                "call_wall": self.levels.call_wall,
                "put_wall": self.levels.put_wall,
                "gamma_flip": self.levels.gamma_flip,
                "gvwap": self.levels.gvwap,
                "major_call_walls": self.levels.major_call_walls,
                "major_put_walls": self.levels.major_put_walls,
            },
            "signals": asdict(self.signals),
            "playbook": self.playbook.to_json(),
            "rows": self.rows,
            "per_strike": {str(k): v for k, v in self.per_strike.items()},
            "per_expiry": self.per_expiry,
            "flow": [
                {
                    "instrument_id": f.instrument_id,
                    "strike": f.strike,
                    "expiry": f.expiry,
                    "type": f.option_type,
                    "premium": f.premium,
                    "size": f.size,
                    "side": f.side,
                    "sweep": f.sweep,
                    "block": f.block,
                    "oi_exceedance": f.oi_exceedance,
                    "dealer_side": f.dealer_side,
                    "ts": f.ts.isoformat(),
                }
                for f in self.flow
            ],
        }


_BUNDLES: Dict[str, Bundle] = {}
_BUNDLE_EXPIRY: Dict[str, float] = {}
_LOCK = threading.RLock()


def _scale_snapshot(snap: ChainSnapshot, scale: float, as_underlying: str) -> ChainSnapshot:
    """Rescale strikes / spot for alias tickers (e.g. ES from SPY × 10)."""
    new_rows = [replace(r, strike=r.strike * scale) for r in snap.rows]
    return ChainSnapshot(
        underlying=as_underlying,
        spot=snap.spot * scale,
        asof=snap.asof,
        rows=new_rows,
        total_gex=snap.total_gex,
        total_dex=snap.total_dex,
        total_vanna=snap.total_vanna,
        total_charm=snap.total_charm,
    )


def _resolve_scale(underlying: str, source: str, fallback: float) -> float:
    """Compute the live source→alias price ratio.

    A hardcoded SPY × 10 gives ES ≈ SPY × 10.0 which is off by ~0.8% vs the
    real ES futures price (SPX ≠ 10×SPY exactly, plus ES carries a small
    basis premium). Using the live ratio makes ES track actual /ES.
    """
    if underlying == source:
        return 1.0
    try:
        from .spot import get_live_spot

        src_spot = get_live_spot(source)
        alias_spot = get_live_spot(underlying)
        if src_spot and alias_spot and src_spot > 0:
            ratio = alias_spot / src_spot
            if 0.05 < ratio < 100:  # sanity
                log.info(
                    "alias scale %s/%s = %.4f (live ratio)",
                    underlying, source, ratio,
                )
                return ratio
    except Exception as exc:  # noqa: BLE001
        log.warning("live scale for %s/%s failed: %s", underlying, source, exc)
    return fallback


def _build(underlying: str, expiry_filter: Optional[str]) -> Bundle:
    # resolve aliases (e.g. ES → fetch SPY, display at ES scale)
    alias = TICKER_ALIASES.get(underlying)
    source = alias["source"] if alias else underlying
    fallback_scale = alias["scale"] if alias else 1.0
    scale = _resolve_scale(underlying, source, fallback_scale)

    snap: ChainSnapshot = build_chain_snapshot(source, expiry_filter=expiry_filter)
    if scale != 1.0:
        snap = _scale_snapshot(snap, scale, underlying)
    levels = structural_levels(snap)
    sigs = compute_signals(snap, levels)
    flow = build_flow(source, snap)
    playbook = build_playbook(
        underlying=underlying,
        spot=snap.spot,
        total_gex=snap.total_gex,
        call_wall=levels.call_wall,
        put_wall=levels.put_wall,
        gamma_flip=levels.gamma_flip,
        gvwap=levels.gvwap,
        regime_score=sigs.regime_score,
        sss=sigs.sss,
        fpi=sigs.fpi,
        regd=sigs.regd,
        hv=sigs.hv,
    )
    return Bundle(
        underlying=underlying,
        asof=snap.asof,
        spot=snap.spot,
        total_gex=snap.total_gex,
        total_dex=snap.total_dex,
        total_vanna=snap.total_vanna,
        total_charm=snap.total_charm,
        levels=levels,
        signals=sigs,
        rows=snap.to_records(),
        per_strike={f"{k:.2f}": v for k, v in gex_by_strike(snap).items()},
        per_expiry=gex_by_expiry(snap),
        flow=flow,
        playbook=playbook,
    )


def get_bundle(underlying: str, *, expiry_filter: Optional[str] = None) -> Bundle:
    key = f"{underlying}|{expiry_filter or 'all'}"
    settings = get_settings()
    ttl = settings.refresh_interval_seconds
    with _LOCK:
        existing = _BUNDLES.get(key)
        if existing and _BUNDLE_EXPIRY.get(key, 0) > time.time():
            return existing
    # build outside the lock so parallel tickers don't block each other
    bundle = _build(underlying, expiry_filter)
    with _LOCK:
        _BUNDLES[key] = bundle
        _BUNDLE_EXPIRY[key] = time.time() + max(ttl - 5, 10)
    return bundle


def summary_row(underlying: str) -> dict:
    """Light-weight payload for the ticker table."""
    try:
        b = get_bundle(underlying)
    except Exception as exc:  # noqa: BLE001
        log.warning("summary for %s failed: %s", underlying, exc)
        return {"underlying": underlying, "error": str(exc)}
    return {
        "underlying": underlying,
        "asof": b.asof.isoformat(),
        "spot": b.spot,
        "total_gex": b.total_gex,
        "total_dex": b.total_dex,
        "call_wall": b.levels.call_wall,
        "put_wall": b.levels.put_wall,
        "gamma_flip": b.levels.gamma_flip,
        "regime_score": b.signals.regime_score,
        "sss": b.signals.sss,
        "fpi": b.signals.fpi,
    }


def prime_universe() -> None:
    """Warm the cache for the full universe. Safe to call on every tick."""
    settings = get_settings()
    for sym in settings.universe:
        try:
            get_bundle(sym)
        except Exception as exc:  # noqa: BLE001
            log.warning("prime failed for %s: %s", sym, exc)


def reset_cache() -> None:
    with _LOCK:
        _BUNDLES.clear()
        _BUNDLE_EXPIRY.clear()


__all__ = [
    "Bundle",
    "get_bundle",
    "summary_row",
    "prime_universe",
    "reset_cache",
]
