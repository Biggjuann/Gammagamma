"""Data-driven trading playbook.

Takes a :class:`~app.snapshot_service.Bundle` and produces a concise
summary of how to trade the session given the structural state. Pure
rule-based — no predictions, just the standard GEX playbook applied to
today's numbers.

Disclaimer in :mod:`.main` — nothing here is investment advice.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class KeyLevel:
    label: str
    price: Optional[float]
    note: str = ""


@dataclass
class Playbook:
    bias: str            # "MEAN REVERT" | "TREND" | "PIVOT WATCH" | "DIRECTIONAL"
    regime: str          # "COMPRESSED" | "NEUTRAL" | "TRANSITION" | "ELEVATED"
    headline: str        # one-sentence summary
    bullets: List[str]   # 3-6 actionable items
    key_levels: List[KeyLevel]
    risk_flags: List[str]

    def to_json(self) -> dict:
        return {
            "bias": self.bias,
            "regime": self.regime,
            "headline": self.headline,
            "bullets": self.bullets,
            "key_levels": [
                {"label": k.label, "price": k.price, "note": k.note}
                for k in self.key_levels
            ],
            "risk_flags": self.risk_flags,
        }


def _fmt(p: Optional[float]) -> str:
    return f"{p:,.2f}" if p else "—"


def build_playbook(
    *,
    underlying: str,
    spot: float,
    total_gex: float,
    call_wall: Optional[float],
    put_wall: Optional[float],
    gamma_flip: Optional[float],
    gvwap: Optional[float],
    regime_score: float,
    sss: float,
    fpi: float,
    regd: float,
    hv: float,
) -> Playbook:
    # -- regime label ------------------------------------------------------
    if regime_score >= 75:
        regime = "ELEVATED"
    elif regime_score >= 55:
        regime = "TRANSITION"
    elif regime_score >= 30:
        regime = "NEUTRAL"
    else:
        regime = "COMPRESSED"

    # -- bias --------------------------------------------------------------
    pos_gex = total_gex > 0
    in_range = (
        call_wall is not None
        and put_wall is not None
        and put_wall < spot < call_wall
    )
    near_flip = (
        gamma_flip is not None
        and spot > 0
        and abs(spot - gamma_flip) / spot <= 0.003  # within 0.3%
    )

    if near_flip:
        bias = "PIVOT WATCH"
    elif pos_gex and in_range:
        bias = "MEAN REVERT"
    elif not pos_gex:
        bias = "TREND"
    else:
        bias = "DIRECTIONAL"

    # -- headline ----------------------------------------------------------
    if bias == "MEAN REVERT":
        headline = (
            f"{underlying} pinned inside dealer-hedged range "
            f"{_fmt(put_wall)}–{_fmt(call_wall)}. Fade extremes, respect walls."
        )
    elif bias == "TREND":
        headline = (
            f"{underlying} in negative-gamma regime — dealers amplify moves. "
            f"Trade continuation; breaks of {_fmt(call_wall)} / {_fmt(put_wall)} "
            f"extend."
        )
    elif bias == "PIVOT WATCH":
        headline = (
            f"{underlying} sitting on gamma flip {_fmt(gamma_flip)}. "
            "Regime inflection risk — reduce size until direction resolves."
        )
    else:  # DIRECTIONAL
        side = "above call wall" if call_wall and spot > call_wall else "below put wall"
        headline = (
            f"{underlying} trading {side} — outside dealer-hedged range. "
            "Structural squeeze/cascade potential."
        )

    # -- bullets -----------------------------------------------------------
    bullets: List[str] = []
    if bias == "MEAN REVERT":
        bullets.append(
            f"Fade pushes toward {_fmt(call_wall)} (call wall) — dealers "
            "sell strength."
        )
        bullets.append(
            f"Buy dips toward {_fmt(put_wall)} (put wall) — dealers buy weakness."
        )
        if gvwap:
            bullets.append(f"GVWAP {_fmt(gvwap)} acts as magnet; mean reversion bias.")
        bullets.append("0DTE iron condors / short strangles favored; vol suppression.")
    elif bias == "TREND":
        bullets.append(
            "Dealers hedge pro-cyclically — momentum plays and breakouts valid."
        )
        if call_wall:
            bullets.append(f"Break above {_fmt(call_wall)} → chase upside; next resistance overnight.")
        if put_wall:
            bullets.append(f"Break below {_fmt(put_wall)} → cascade risk; downside continuation.")
        bullets.append("Avoid fading extremes. Directional options / debit spreads favored.")
    elif bias == "PIVOT WATCH":
        bullets.append(
            f"Spot within {abs(spot - (gamma_flip or spot)) / (spot or 1) * 10_000:.0f} bp "
            "of flip — regime can flip intraday."
        )
        bullets.append("Cut size. Wait for decisive close above/below flip before committing.")
        bullets.append("Volatility expansion likely — long gamma / straddle bias.")
    else:  # DIRECTIONAL
        if call_wall and spot > call_wall:
            bullets.append(
                f"Spot {_fmt(spot)} above call wall {_fmt(call_wall)} — "
                "dealer short-squeeze territory."
            )
            bullets.append("Upside can extend fast until new call OI rebuilds.")
        elif put_wall and spot < put_wall:
            bullets.append(
                f"Spot {_fmt(spot)} below put wall {_fmt(put_wall)} — "
                "dealer de-hedge cascade risk."
            )
            bullets.append("Downside continuation until OI rebuilds lower.")
        bullets.append("Directional bias strong; avoid counter-trend fades.")

    # Regime overlays
    if regime == "ELEVATED":
        bullets.append(
            f"Regime score {regime_score:.0f} (elevated) — volatility expansion "
            "likely; wider stops, smaller size."
        )
    elif regime == "COMPRESSED":
        bullets.append(
            f"Regime score {regime_score:.0f} (compressed) — coiling; "
            "breakout setup building, long-gamma bias."
        )

    if sss < 30:
        bullets.append(f"SSS {sss:.0f}: structural stability low — expect whipsaws.")
    if hv >= 70:
        bullets.append(f"HV {hv:.0f}: hidden vol elevated — wider than implied ranges.")

    # -- key levels --------------------------------------------------------
    key_levels = [
        KeyLevel("Spot", spot),
        KeyLevel("Call wall", call_wall, "dealer sell flow above"),
        KeyLevel("Put wall", put_wall, "dealer buy flow below"),
        KeyLevel("Gamma flip", gamma_flip, "regime boundary"),
        KeyLevel("GVWAP", gvwap, "gamma-weighted magnet"),
    ]

    # -- risk flags --------------------------------------------------------
    risk_flags: List[str] = []
    if regd >= 80:
        risk_flags.append("Regime distance high — flip is nearby.")
    if fpi >= 70:
        risk_flags.append(f"Flow pressure {fpi:.0f} — directional crowding.")
    if not call_wall or not put_wall:
        risk_flags.append("Wall(s) undefined — thin OI or data gap.")

    return Playbook(
        bias=bias,
        regime=regime,
        headline=headline,
        bullets=bullets,
        key_levels=key_levels,
        risk_flags=risk_flags,
    )
