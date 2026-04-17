"""Deterministic offline fixtures.

Used when ``DATABENTO_API_KEY`` is absent so the frontend is usable in
local dev without burning quota. The synthetic chain is seeded off the
ticker string, so the same ticker always returns the same shape —
useful for screenshots and regression tests.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import numpy as np
import pandas as pd

from .databento_client import OptionDefinition, Quote, TradeTick


def _seed(underlying: str) -> int:
    return int(hashlib.sha1(underlying.encode()).hexdigest()[:8], 16)


_SPOT_OVERRIDES = {
    "SPX": 5250.0, "SPY": 525.0, "QQQ": 460.0, "IWM": 210.0, "DIA": 400.0,
    "VIX": 14.5, "NDX": 18500.0, "RUT": 2050.0,
    "AAPL": 215.0, "MSFT": 420.0, "NVDA": 135.0, "AMZN": 185.0, "META": 500.0,
    "GOOGL": 175.0, "GOOG": 176.0, "TSLA": 240.0, "AMD": 165.0, "AVGO": 1500.0,
}


def _spot(underlying: str) -> float:
    if underlying in _SPOT_OVERRIDES:
        return _SPOT_OVERRIDES[underlying]
    rng = np.random.default_rng(_seed(underlying))
    return float(rng.uniform(30, 400))


def build_fixture_chain(underlying: str) -> List[OptionDefinition]:
    spot = _spot(underlying)
    rng = np.random.default_rng(_seed(underlying))
    now = datetime.now(timezone.utc)
    expiries = [
        now + timedelta(days=d)
        for d in (0, 1, 2, 7, 14, 30, 60, 90, 180, 365)
    ]
    # strike ladder ±20% around spot, ~1% spacing
    step = max(round(spot * 0.01, 2), 0.5)
    strikes = [round(spot + i * step, 2) for i in range(-20, 21)]
    defs: List[OptionDefinition] = []
    iid = 1_000_000 + (_seed(underlying) % 1_000)
    for exp in expiries:
        for k in strikes:
            for cp in ("C", "P"):
                defs.append(
                    OptionDefinition(
                        instrument_id=iid,
                        raw_symbol=f"{underlying} {exp:%y%m%d}{cp}{int(k*1000):08d}",
                        underlying=underlying,
                        expiration=exp,
                        strike=float(k),
                        option_type=cp,
                        multiplier=100,
                    )
                )
                iid += 1
    _ = rng  # reserved for future jitter
    return defs


def build_fixture_quotes(underlying: str) -> List[Quote]:
    chain = build_fixture_chain(underlying)
    spot = _spot(underlying)
    rng = np.random.default_rng(_seed(underlying) ^ 0x5EED)
    now = datetime.now(timezone.utc)
    quotes: List[Quote] = []
    for d in chain:
        T = max((d.expiration - now).total_seconds() / (365 * 86400), 1 / 365)
        intrinsic = max(
            (spot - d.strike) if d.option_type == "C" else (d.strike - spot),
            0.0,
        )
        tv = 0.18 * spot * (T ** 0.5) * float(rng.uniform(0.6, 1.4))
        mid = max(intrinsic + tv * 0.4, 0.05)
        half = max(mid * 0.02, 0.01)
        quotes.append(
            Quote(
                instrument_id=d.instrument_id,
                bid=max(mid - half, 0.01),
                ask=mid + half,
                bid_size=int(rng.integers(1, 500)),
                ask_size=int(rng.integers(1, 500)),
                ts=now,
            )
        )
    return quotes


def build_fixture_oi(underlying: str) -> Dict[int, int]:
    chain = build_fixture_chain(underlying)
    spot = _spot(underlying)
    rng = np.random.default_rng(_seed(underlying) ^ 0xDEAD)
    oi: Dict[int, int] = {}
    for d in chain:
        atm_dist = abs(d.strike - spot) / spot
        base = int(max(5000 * np.exp(-15 * atm_dist), 5))
        oi[d.instrument_id] = base + int(rng.integers(0, base // 2 + 1))
    return oi


def build_fixture_trades(underlying: str) -> List[TradeTick]:
    chain = build_fixture_chain(underlying)
    rng = np.random.default_rng(_seed(underlying) ^ 0xBEEF)
    now = datetime.now(timezone.utc)
    n = 60
    pick = rng.choice(len(chain), size=min(n, len(chain)), replace=False)
    ticks: List[TradeTick] = []
    for idx in pick:
        d = chain[int(idx)]
        ticks.append(
            TradeTick(
                instrument_id=d.instrument_id,
                price=float(rng.uniform(0.2, 15)),
                size=int(rng.integers(1, 400)),
                side=str(rng.choice(["A", "B", "N"])),
                ts=now - timedelta(seconds=int(rng.integers(0, 300))),
            )
        )
    return ticks


def build_fixture_ohlcv(underlying: str) -> pd.DataFrame:
    spot = _spot(underlying)
    rng = np.random.default_rng(_seed(underlying) ^ 0xCAFE)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    idx = pd.date_range(end=now, periods=390, freq="1min", tz="UTC")
    rets = rng.normal(0, 0.0008, size=len(idx))
    prices = spot * np.exp(np.cumsum(rets) - 0.004)
    high = prices * (1 + np.abs(rng.normal(0, 0.0005, size=len(idx))))
    low = prices * (1 - np.abs(rng.normal(0, 0.0005, size=len(idx))))
    open_ = np.roll(prices, 1)
    open_[0] = prices[0]
    return pd.DataFrame(
        {
            "ts_event": idx,
            "open": open_,
            "high": high,
            "low": low,
            "close": prices,
            "volume": rng.integers(1_000, 200_000, size=len(idx)),
        }
    )
