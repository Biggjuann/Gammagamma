"""Gamma exposure (GEX) aggregator.

Pipeline:

1. Pull chain definitions + NBBO snapshot + OI from the cache-backed
   Databento client.
2. Solve IV from mid-price; drop rows with no quote / near-expiry.
3. Compute delta, gamma, vanna, charm per contract.
4. Sum into per-strike, per-expiry, and per-ticker buckets under the
   dealer-short-calls / dealer-short-puts convention.

The convention here matches the Gamma Sonar marketing copy: positive GEX
means dealers are net long gamma and **sell into rallies / buy into
dips** (dampens volatility). Negative GEX means the opposite.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .config import get_settings
from .databento_client import get_client
from .greeks import greeks, implied_vol

log = logging.getLogger(__name__)


@dataclass
class ChainRow:
    instrument_id: int
    strike: float
    expiry: datetime
    dte: float
    option_type: str
    mid: float
    oi: int
    iv: float
    delta: float
    gamma: float
    vanna: float
    charm: float
    gex: float  # $ gamma per 1% spot move (dealer-negative convention)


@dataclass
class ChainSnapshot:
    underlying: str
    spot: float
    asof: datetime
    rows: List[ChainRow]
    total_gex: float
    total_dex: float
    total_vanna: float
    total_charm: float

    def to_records(self) -> List[dict]:
        return [
            {
                "instrument_id": r.instrument_id,
                "strike": r.strike,
                "expiry": r.expiry.isoformat(),
                "dte": r.dte,
                "type": r.option_type,
                "mid": r.mid,
                "oi": r.oi,
                "iv": r.iv,
                "delta": r.delta,
                "gamma": r.gamma,
                "vanna": r.vanna,
                "charm": r.charm,
                "gex": r.gex,
            }
            for r in self.rows
        ]


def _latest_close(df: pd.DataFrame) -> Optional[float]:
    if df is None or df.empty:
        return None
    col = "close" if "close" in df.columns else df.columns[-1]
    val = float(df[col].iloc[-1])
    return val if val > 0 else None


def _infer_spot_from_chain(defs, quotes_by_id) -> Optional[float]:
    """Put-call parity approximation of spot.

    C - P ≈ S - K for near-ATM, short-dated options (ignoring rates/dividends).
    Strategy:
      1. Pick the nearest future expiry with at least one C/P pair.
      2. Among its strikes, take the one with the smallest |C_mid - P_mid| —
         that's the ATM strike.
      3. Return K + (C_mid - P_mid).
    """
    from collections import defaultdict

    pair: dict = defaultdict(dict)
    for d in defs:
        q = quotes_by_id.get(d.instrument_id)
        if not q or q.mid <= 0:
            continue
        pair[(d.expiration.date(), d.strike)][d.option_type] = q.mid

    today = datetime.now(timezone.utc).date()
    expiries = sorted({e for e, _ in pair if e >= today})
    for exp in expiries:
        best_diff = float("inf")
        best: Optional[tuple] = None
        for (e, k), sides in pair.items():
            if e != exp or "C" not in sides or "P" not in sides:
                continue
            diff = abs(sides["C"] - sides["P"])
            if diff < best_diff:
                best_diff = diff
                best = (k, sides["C"], sides["P"])
        if best is not None:
            k, c, p = best
            return max(k + (c - p), 0.0)
    return None


def build_chain_snapshot(
    underlying: str,
    *,
    expiry_filter: Optional[str] = None,
) -> ChainSnapshot:
    settings = get_settings()
    client = get_client()

    defs = client.get_chain_definitions(underlying)
    quotes = client.snapshot_nbbo(underlying)
    oi_map = client.open_interest(underlying)

    now = datetime.now(timezone.utc)
    if not defs:
        log.warning("empty definitions for %s", underlying)
        return ChainSnapshot(underlying, 0.0, now, [], 0.0, 0.0, 0.0, 0.0)
    if not quotes:
        log.warning("empty quotes for %s", underlying)
        return ChainSnapshot(underlying, 0.0, now, [], 0.0, 0.0, 0.0, 0.0)

    quotes_by_id = {q.instrument_id: q for q in quotes}

    # Derive spot via put-call parity on the options we already have. Falls
    # back to a fixture spot if parity can't be solved (no matching C/P pair).
    spot = _infer_spot_from_chain(defs, quotes_by_id) or 0.0
    if spot <= 0:
        from .fixtures import _spot  # type: ignore

        spot = _spot(underlying)
        log.warning("spot fallback to fixture %.2f for %s", spot, underlying)
    else:
        log.info("spot (put-call parity) for %s = %.2f", underlying, spot)

    # vectorise
    keep_defs: List = []
    for d in defs:
        if d.instrument_id not in quotes_by_id:
            continue
        dte = (d.expiration - now).total_seconds() / 86400.0
        if dte < 0:
            continue
        if expiry_filter == "0dte" and dte > 1.0:
            continue
        if expiry_filter == "weekly" and not (0 <= dte <= 7):
            continue
        if expiry_filter == "monthly" and not (7 < dte <= 45):
            continue
        if expiry_filter == "leaps" and dte < 180:
            continue
        keep_defs.append(d)

    if not keep_defs:
        return ChainSnapshot(underlying, spot, now, [], 0.0, 0.0, 0.0, 0.0)

    K = np.array([d.strike for d in keep_defs])
    T = np.array(
        [max((d.expiration - now).total_seconds() / (365 * 86400), 1 / 365)
         for d in keep_defs]
    )
    mid = np.array([quotes_by_id[d.instrument_id].mid for d in keep_defs])
    is_call = np.array([d.option_type == "C" for d in keep_defs])
    oi = np.array([oi_map.get(d.instrument_id, 0) for d in keep_defs], dtype=float)
    mult = np.array([d.multiplier for d in keep_defs], dtype=float)

    S = np.full(K.shape, spot)
    r = settings.risk_free_rate

    iv = implied_vol(mid, S, K, T, r, is_call)
    valid = np.isfinite(iv)
    iv[~valid] = np.nan

    g = greeks(S, K, T, r, np.where(valid, iv, 0.2), is_call)

    # Dealer convention: public buys calls (dealer short calls → dealer short gamma on calls)
    # and buys puts (dealer short puts → dealer also short gamma on puts).
    # Aggregate per contract: GEX_$ = gamma * OI * multiplier * spot^2 * 0.01
    #   sign: calls contribute +, puts contribute - (Gamma Sonar convention).
    sign = np.where(is_call, 1.0, -1.0)
    gex_dollar = sign * g.gamma * oi * mult * (spot ** 2) * 0.01
    dex_dollar = g.delta * oi * mult * spot
    vanna_dollar = g.vanna * oi * mult * spot
    charm_dollar = g.charm * oi * mult * spot / 365.0

    rows: List[ChainRow] = []
    for i, d in enumerate(keep_defs):
        if not valid[i] or oi[i] <= 0:
            continue
        rows.append(
            ChainRow(
                instrument_id=d.instrument_id,
                strike=float(K[i]),
                expiry=d.expiration,
                dte=(d.expiration - now).total_seconds() / 86400.0,
                option_type=d.option_type,
                mid=float(mid[i]),
                oi=int(oi[i]),
                iv=float(iv[i]),
                delta=float(g.delta[i]),
                gamma=float(g.gamma[i]),
                vanna=float(g.vanna[i]),
                charm=float(g.charm[i]),
                gex=float(gex_dollar[i]),
            )
        )

    return ChainSnapshot(
        underlying=underlying,
        spot=spot,
        asof=now,
        rows=rows,
        total_gex=float(sum(r.gex for r in rows)),
        total_dex=float(np.nansum(dex_dollar[valid])),
        total_vanna=float(np.nansum(vanna_dollar[valid])),
        total_charm=float(np.nansum(charm_dollar[valid])),
    )


def gex_by_strike(snapshot: ChainSnapshot) -> Dict[float, Dict[str, float]]:
    """Collapse rows into a dict keyed by strike with call/put/net GEX."""
    out: Dict[float, Dict[str, float]] = {}
    for r in snapshot.rows:
        bucket = out.setdefault(
            r.strike, {"call_gex": 0.0, "put_gex": 0.0, "net_gex": 0.0, "oi": 0}
        )
        if r.option_type == "C":
            bucket["call_gex"] += r.gex
        else:
            bucket["put_gex"] += r.gex
        bucket["net_gex"] += r.gex
        bucket["oi"] += r.oi
    return out


def gex_by_expiry(snapshot: ChainSnapshot) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for r in snapshot.rows:
        key = r.expiry.date().isoformat()
        out[key] = out.get(key, 0.0) + r.gex
    return out
