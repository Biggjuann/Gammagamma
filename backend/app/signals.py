"""Structural signals: walls, flip, GVWAP, regime, SSS, FPI, REGD, HV.

All signals operate on a :class:`~app.gex.ChainSnapshot` so they can be
computed once and fanned out over REST / WebSocket without re-querying
Databento.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

from .gex import ChainSnapshot, gex_by_strike


@dataclass
class StructuralLevels:
    call_wall: Optional[float]
    put_wall: Optional[float]
    gamma_flip: Optional[float]
    gvwap: Optional[float]
    major_call_walls: List[float]
    major_put_walls: List[float]


@dataclass
class SignalBundle:
    # Original signals (0–100 normalised where applicable)
    sss: float        # Structural Stability Score
    fpi: float        # Flow Pressure Index
    regd: float       # Regime Distance
    hv: float         # Hidden Vol  (GEX-implied realised vol proxy)
    gvwap: float      # Gamma-weighted VWAP (price level)
    regime_score: float   # 0–100 composite


def structural_levels(snapshot: ChainSnapshot) -> StructuralLevels:
    per_strike = gex_by_strike(snapshot)
    if not per_strike:
        return StructuralLevels(None, None, None, None, [], [])

    spot = snapshot.spot
    strikes = np.array(sorted(per_strike.keys()))

    call_gex = np.array([per_strike[k]["call_gex"] for k in strikes])
    put_gex = np.array([per_strike[k]["put_gex"] for k in strikes])
    net_gex = np.array([per_strike[k]["net_gex"] for k in strikes])

    call_wall = float(strikes[int(np.argmax(call_gex))]) if call_gex.size else None
    put_wall = float(strikes[int(np.argmin(put_gex))]) if put_gex.size else None

    # Gamma flip.
    # Primary: cumulative GEX zero-crossing nearest spot. Works for
    # balanced chains (Monthly / LEAPS) where calls and puts roughly
    # offset.
    # Fallback: the strike where per-strike net GEX itself flips sign
    # nearest spot. Short-dated chains (0DTE / Weekly) are often lopsided
    # end-to-end (total GEX one-signed), so cumsum never crosses zero —
    # but per-strike still transitions from put-dominated (negative) to
    # call-dominated (positive) somewhere in the middle. That transition
    # *is* the call/put dominance boundary traders read as "gamma flip".
    cum = np.cumsum(net_gex)
    flip_idx = None
    for i in range(1, len(cum)):
        if cum[i - 1] * cum[i] < 0:
            if flip_idx is None or abs(strikes[i] - spot) < abs(strikes[flip_idx] - spot):
                flip_idx = i
    if flip_idx is None:
        for i in range(1, len(net_gex)):
            if net_gex[i - 1] * net_gex[i] < 0:
                if flip_idx is None or abs(strikes[i] - spot) < abs(strikes[flip_idx] - spot):
                    flip_idx = i
    gamma_flip = float(strikes[flip_idx]) if flip_idx is not None else None

    # GVWAP = Σ |gex| * K  /  Σ |gex|
    abs_gex = np.abs(net_gex)
    gvwap = float(np.sum(abs_gex * strikes) / np.sum(abs_gex)) if abs_gex.sum() else None

    # Major walls: top-3 by magnitude
    major_call = [
        float(strikes[i])
        for i in np.argsort(-call_gex)[:3]
        if call_gex[i] > 0
    ]
    major_put = [
        float(strikes[i])
        for i in np.argsort(put_gex)[:3]
        if put_gex[i] < 0
    ]

    return StructuralLevels(
        call_wall=call_wall,
        put_wall=put_wall,
        gamma_flip=gamma_flip,
        gvwap=gvwap,
        major_call_walls=major_call,
        major_put_walls=major_put,
    )


def _clip01(x: float) -> float:
    return float(max(0.0, min(100.0, x)))


def compute_signals(
    snapshot: ChainSnapshot,
    levels: StructuralLevels,
    *,
    history: Optional[Dict[str, float]] = None,
) -> SignalBundle:
    """Compute the five proprietary scalars + regime composite.

    The exact formulas in production Gamma Sonar aren't public. Ours are
    intentionally transparent, defensible approximations so the UI can
    render meaningful gauges instead of flat zeros:

    * **SSS** — Structural Stability Score. High when net GEX is large
      and positive (dealers long gamma → pinning).
    * **FPI** — Flow Pressure Index. Delta-weighted exposure vs gamma
      exposure; measures directional crowd.
    * **REGD** — Regime Distance. Normalised distance from spot to gamma
      flip; small REGD → near inflection.
    * **HV** — Hidden Vol. GEX-implied realised-vol proxy (inverse of
      absolute GEX).
    * **regime_score** — 15-feature composite (0–100) derived from the
      bundle above plus OI concentration / wall strength.
    """
    spot = snapshot.spot or 1.0
    total_abs_gex = abs(snapshot.total_gex) or 1.0

    # SSS: sigmoid on net GEX scaled by |total|.
    sss_raw = snapshot.total_gex / total_abs_gex  # -1 .. +1
    sss = _clip01(50.0 + 50.0 * sss_raw)

    # FPI: ratio of DEX to GEX magnitudes.
    fpi_raw = np.tanh(abs(snapshot.total_dex) / (total_abs_gex + 1e-9))
    fpi = _clip01(100.0 * fpi_raw)

    # REGD: distance from spot to gamma flip in ATR-like units.
    if levels.gamma_flip is None:
        regd = 50.0
    else:
        dist_bp = abs(levels.gamma_flip - spot) / spot * 10_000
        regd = _clip01(100.0 - min(dist_bp / 2.0, 100.0))

    # HV proxy: inverse gamma concentration. Higher = more potential vol.
    gex_range = max(r.gex for r in snapshot.rows) - min(r.gex for r in snapshot.rows) \
        if snapshot.rows else 0.0
    hv = _clip01(100.0 * np.tanh(gex_range / (total_abs_gex + 1e-9)))

    # Regime composite: 15 features rolled up. We use the bundle above
    # plus tail-risk and wall-sharpness proxies.
    wall_strength = 0.0
    if levels.call_wall is not None and levels.put_wall is not None:
        wall_gap = abs(levels.call_wall - levels.put_wall) / spot
        wall_strength = _clip01(100.0 - min(wall_gap * 1000.0, 100.0))

    charm_pressure = _clip01(
        100.0 * np.tanh(abs(snapshot.total_charm) / (total_abs_gex + 1e-9))
    )
    vanna_pressure = _clip01(
        100.0 * np.tanh(abs(snapshot.total_vanna) / (total_abs_gex + 1e-9))
    )

    features = np.array(
        [
            sss,
            fpi,
            regd,
            hv,
            wall_strength,
            charm_pressure,
            vanna_pressure,
            100.0 - sss,          # short-gamma stress
            _clip01(regd * 1.1),  # flip-proximity stress
            _clip01(100.0 - wall_strength),
            _clip01(fpi * 0.7),
            _clip01(hv * 0.8),
            _clip01(charm_pressure * 0.6),
            _clip01(vanna_pressure * 0.6),
            _clip01(50.0 + 50.0 * np.tanh((snapshot.total_vanna - snapshot.total_charm)
                                          / (total_abs_gex + 1e-9))),
        ]
    )
    regime_score = float(np.clip(features.mean(), 0.0, 100.0))

    return SignalBundle(
        sss=sss,
        fpi=fpi,
        regd=regd,
        hv=hv,
        gvwap=levels.gvwap or spot,
        regime_score=regime_score,
    )
