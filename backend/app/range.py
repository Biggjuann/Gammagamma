"""Daily expected-range analysis.

Combines three inputs into a single view of "how wide should today go
and where will price actually turn":

  1. IV width  — spot × ATM_IV × √(DTE/365) gives the 1-SD expected
     move. ATM straddle price is the empirical counterpart (~1.25×
     the 1-SD in an efficient market).
  2. Skew shape — the 25-delta call and 25-delta put strikes bound
     the market-priced range. Index skew is put-heavy, so the put
     side sits farther from spot than the call side. Skew tilt
     quantifies that asymmetry.
  3. GEX regime — spot vs gamma_flip. Positive gamma = dealers hedge
     counter-trend, realized runs below implied, walls inside the
     skew range become the turning points. Negative gamma = dealers
     amplify moves, skew range is realistic or gets exceeded.

The generated read maps regime × walls × skew to a plain-language
"what to expect today."

Educational only — not trading advice. IV includes a variance risk
premium, so realized usually stays inside the skew range on quiet
days. The exceptions cluster in negative-gamma regimes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import sqrt
from typing import Iterable, List, Optional, Tuple


@dataclass
class DailyRange:
    underlying: str
    spot: float
    asof: datetime
    anchor_expiry: str          # ISO date of the expiry we anchored on
    dte_years: float            # time to expiry in years (floored at 1h)

    # IV width
    atm_iv: Optional[float]
    expected_move_1sd: Optional[float]  # $ move
    upper_1sd: Optional[float]
    lower_1sd: Optional[float]
    atm_straddle: Optional[float]

    # Skew shape
    call_25d_strike: Optional[float]
    put_25d_strike: Optional[float]
    call_25d_iv: Optional[float]
    put_25d_iv: Optional[float]
    skew_tilt: Optional[float]  # (put_dist - call_dist) / spot; >0 = bearish tilt

    # Structural context
    call_wall: Optional[float]
    put_wall: Optional[float]
    gamma_flip: Optional[float]
    regime: str                 # "positive_gamma" | "negative_gamma" | "unknown"

    # The read
    read_headline: str
    read_bullets: List[str]

    def to_json(self) -> dict:
        return {
            "underlying": self.underlying,
            "spot": self.spot,
            "asof": self.asof.isoformat() if self.asof else None,
            "anchor_expiry": self.anchor_expiry,
            "dte_years": self.dte_years,
            "atm_iv": self.atm_iv,
            "expected_move_1sd": self.expected_move_1sd,
            "upper_1sd": self.upper_1sd,
            "lower_1sd": self.lower_1sd,
            "atm_straddle": self.atm_straddle,
            "call_25d_strike": self.call_25d_strike,
            "put_25d_strike": self.put_25d_strike,
            "call_25d_iv": self.call_25d_iv,
            "put_25d_iv": self.put_25d_iv,
            "skew_tilt": self.skew_tilt,
            "call_wall": self.call_wall,
            "put_wall": self.put_wall,
            "gamma_flip": self.gamma_flip,
            "regime": self.regime,
            "read_headline": self.read_headline,
            "read_bullets": self.read_bullets,
        }


def build_daily_range_from_bundle(bundle) -> DailyRange:
    """Compute a daily range from a snapshot_service.Bundle.

    The bundle's `rows` are already-serialised dicts, so we work on
    those directly rather than requiring a live ChainSnapshot.
    """
    spot = bundle.spot or 0.0
    asof = bundle.asof
    underlying = bundle.underlying

    rows = list(bundle.rows or [])
    if not rows:
        return _empty(underlying, spot, asof)

    # Anchor on the earliest listed expiration in the filtered snapshot.
    # The endpoint requests expiry_filter="0dte", so this is the front
    # contract.
    parsed_rows = []
    for r in rows:
        try:
            exp = datetime.fromisoformat(r["expiry"])
        except (KeyError, TypeError, ValueError):
            continue
        parsed_rows.append((exp, r))
    if not parsed_rows:
        return _empty(underlying, spot, asof)

    earliest_exp = min(exp for exp, _ in parsed_rows)
    anchor_rows = [r for exp, r in parsed_rows if exp == earliest_exp]

    now = datetime.now(timezone.utc)
    dte_seconds = max((earliest_exp - now).total_seconds(), 3600.0)
    T_years = dte_seconds / (365 * 86400)

    # ATM: closest strike to spot in the anchor expiry.
    atm_strike = min(anchor_rows, key=lambda r: abs(r["strike"] - spot))["strike"]
    atm_call = _find_at_strike(anchor_rows, atm_strike, "C")
    atm_put = _find_at_strike(anchor_rows, atm_strike, "P")

    atm_iv = _avg_iv([atm_call, atm_put])
    atm_straddle = None
    if atm_call and atm_put:
        atm_straddle = float(atm_call["mid"]) + float(atm_put["mid"])

    expected_move_1sd = spot * atm_iv * sqrt(T_years) if (atm_iv and spot > 0) else None
    upper_1sd = spot + expected_move_1sd if expected_move_1sd else None
    lower_1sd = spot - expected_move_1sd if expected_move_1sd else None

    call_25 = _nearest_delta(anchor_rows, target=0.25, side="C")
    put_25 = _nearest_delta(anchor_rows, target=-0.25, side="P")

    skew_tilt: Optional[float] = None
    if call_25 and put_25 and spot > 0:
        put_dist = spot - float(put_25["strike"])
        call_dist = float(call_25["strike"]) - spot
        skew_tilt = (put_dist - call_dist) / spot

    lv = bundle.levels
    call_wall = getattr(lv, "call_wall", None)
    put_wall = getattr(lv, "put_wall", None)
    gamma_flip = getattr(lv, "gamma_flip", None)

    regime = _regime(spot, gamma_flip)

    headline, bullets = _generate_read(
        regime=regime,
        spot=spot,
        call_wall=call_wall,
        put_wall=put_wall,
        call_25d=float(call_25["strike"]) if call_25 else None,
        put_25d=float(put_25["strike"]) if put_25 else None,
        expected_move=expected_move_1sd,
        skew_tilt=skew_tilt,
    )

    return DailyRange(
        underlying=underlying,
        spot=spot,
        asof=asof,
        anchor_expiry=earliest_exp.date().isoformat(),
        dte_years=T_years,
        atm_iv=atm_iv,
        expected_move_1sd=expected_move_1sd,
        upper_1sd=upper_1sd,
        lower_1sd=lower_1sd,
        atm_straddle=atm_straddle,
        call_25d_strike=float(call_25["strike"]) if call_25 else None,
        put_25d_strike=float(put_25["strike"]) if put_25 else None,
        call_25d_iv=float(call_25["iv"]) if call_25 and call_25.get("iv") else None,
        put_25d_iv=float(put_25["iv"]) if put_25 and put_25.get("iv") else None,
        skew_tilt=skew_tilt,
        call_wall=call_wall,
        put_wall=put_wall,
        gamma_flip=gamma_flip,
        regime=regime,
        read_headline=headline,
        read_bullets=bullets,
    )


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------
def _find_at_strike(rows: Iterable[dict], strike: float, side: str) -> Optional[dict]:
    for r in rows:
        if r.get("type") == side and abs(float(r["strike"]) - strike) < 1e-6:
            return r
    return None


def _avg_iv(rows: Iterable[Optional[dict]]) -> Optional[float]:
    ivs = [
        float(r["iv"]) for r in rows
        if r is not None and r.get("iv") and float(r["iv"]) > 0
    ]
    return sum(ivs) / len(ivs) if ivs else None


def _nearest_delta(rows: Iterable[dict], *, target: float, side: str) -> Optional[dict]:
    """Find the row on `side` (C/P) whose delta is closest to `target`."""
    candidates = [
        r for r in rows
        if r.get("type") == side
        and isinstance(r.get("delta"), (int, float))
    ]
    if not candidates:
        return None
    # Filter to same-sign deltas (calls > 0, puts < 0)
    if side == "C":
        candidates = [r for r in candidates if r["delta"] > 0]
    else:
        candidates = [r for r in candidates if r["delta"] < 0]
    if not candidates:
        return None
    return min(candidates, key=lambda r: abs(float(r["delta"]) - target))


def _regime(spot: float, gamma_flip: Optional[float]) -> str:
    if gamma_flip is None or spot <= 0:
        return "unknown"
    return "positive_gamma" if spot >= gamma_flip else "negative_gamma"


def _generate_read(
    *,
    regime: str,
    spot: float,
    call_wall: Optional[float],
    put_wall: Optional[float],
    call_25d: Optional[float],
    put_25d: Optional[float],
    expected_move: Optional[float],
    skew_tilt: Optional[float],
) -> Tuple[str, List[str]]:
    bullets: List[str] = []

    if regime == "positive_gamma":
        headline = "Positive gamma — mean-reversion setup. Fade the extremes toward pin."
        bullets.append(
            "Dealers hedge counter-trend; realized vol tends to run below implied."
        )
        if call_wall and put_wall and call_25d and put_25d:
            walls_inside = put_wall > put_25d and call_wall < call_25d
            if walls_inside:
                bullets.append(
                    f"Walls ({put_wall:.2f} / {call_wall:.2f}) sit inside the 25Δ range "
                    f"({put_25d:.2f} / {call_25d:.2f}) — walls are the likely turning points."
                )
            else:
                bullets.append(
                    f"Walls at {put_wall:.2f} / {call_wall:.2f} vs 25Δ bounds "
                    f"{put_25d:.2f} / {call_25d:.2f}."
                )
        if expected_move:
            bullets.append(f"1-SD expected move ≈ ±${expected_move:.2f}.")
    elif regime == "negative_gamma":
        headline = (
            "Negative gamma — trend regime. Don't lean on the range; downside risk elevated."
        )
        bullets.append(
            "Dealers hedge with the move; the skew range is realistic or gets exceeded."
        )
        if put_25d:
            bullets.append(
                f"Downside 25Δ at {put_25d:.2f} is the realistic floor before acceleration."
            )
        if expected_move:
            bullets.append(
                f"1-SD expected move ≈ ±${expected_move:.2f}; expect realized ≥ implied."
            )
    else:
        headline = "Regime undetermined — insufficient chain data to place spot vs flip."
        if expected_move:
            bullets.append(f"1-SD expected move ≈ ±${expected_move:.2f}.")

    if skew_tilt is not None:
        pct = skew_tilt * 100
        if abs(pct) < 0.10:
            bullets.append("Skew nearly symmetric — market pricing a two-sided session.")
        elif pct > 0:
            bullets.append(
                f"Put-side wider by {pct:.2f}% of spot — market paying for downside tail."
            )
        else:
            bullets.append(
                f"Call-side wider by {abs(pct):.2f}% of spot — unusual upward tilt."
            )

    return headline, bullets


def _empty(underlying: str, spot: float, asof: datetime) -> DailyRange:
    return DailyRange(
        underlying=underlying,
        spot=spot,
        asof=asof,
        anchor_expiry="",
        dte_years=0.0,
        atm_iv=None,
        expected_move_1sd=None,
        upper_1sd=None,
        lower_1sd=None,
        atm_straddle=None,
        call_25d_strike=None,
        put_25d_strike=None,
        call_25d_iv=None,
        put_25d_iv=None,
        skew_tilt=None,
        call_wall=None,
        put_wall=None,
        gamma_flip=None,
        regime="unknown",
        read_headline="No chain data available for anchor expiry.",
        read_bullets=[],
    )


__all__ = ["DailyRange", "build_daily_range_from_bundle"]
