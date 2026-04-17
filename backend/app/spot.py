"""Underlying spot price — live current price via Yahoo Finance.

Walls, flip, and GVWAP are strike points computed from yesterday's dealer
positioning (Databento historical-only). They're fixed numbers on the
price axis — they don't move with spot. So the most actionable dashboard
uses **live spot** to show "where price is *right now* relative to those
static dealer levels".

Cached 1 min (Yahoo's free tier isn't sub-second anyway).
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from .cache import cached_call

log = logging.getLogger(__name__)

try:
    import yfinance as yf  # type: ignore
except ImportError:  # pragma: no cover
    yf = None  # noqa: N816


_YAHOO_SYMBOLS = {
    "SPX": "^GSPC",
    "SPY": "SPY",
    "QQQ": "QQQ",
    "IWM": "IWM",
    "DIA": "DIA",
    "NDX": "^NDX",
    "RUT": "^RUT",
    "VIX": "^VIX",
}


def get_live_spot(underlying: str) -> Optional[float]:
    """Return the current live quote for ``underlying`` (or ``None``).

    Cached 1 minute. Returns ``None`` on failure so callers can fall
    through to put-call parity on the chain.
    """
    if yf is None:
        log.warning("yfinance not installed; no spot for %s", underlying)
        return None

    yahoo = _YAHOO_SYMBOLS.get(underlying)
    if yahoo is None:
        return None

    return cached_call(
        namespace="spot_live",
        key=yahoo,
        ttl=timedelta(minutes=1),
        loader=lambda: _fetch(yahoo),
    )


def _fetch(yahoo: str) -> Optional[float]:
    try:
        t = yf.Ticker(yahoo)

        # Primary: fast_info — near-real-time last trade / quote.
        fi = getattr(t, "fast_info", None)
        if fi is not None:
            for attr in ("last_price", "regular_market_price", "regularMarketPrice"):
                try:
                    val = getattr(fi, attr, None)
                    if val is None and hasattr(fi, "get"):
                        val = fi.get(attr)
                    if val and val > 0:
                        return float(val)
                except Exception:  # noqa: BLE001
                    continue

        # Fallback: last 1-minute Close from intraday history.
        hist = t.history(period="1d", interval="1m")
        if hist is not None and not hist.empty and "Close" in hist.columns:
            last = float(hist["Close"].iloc[-1])
            if last > 0:
                return last

        # Last-resort: daily Close.
        daily = t.history(period="2d", interval="1d")
        if daily is not None and not daily.empty and "Close" in daily.columns:
            last = float(daily["Close"].iloc[-1])
            if last > 0:
                return last
    except Exception as exc:  # noqa: BLE001
        log.warning("yfinance fetch for %s failed: %s", yahoo, exc)
    return None


__all__ = ["get_live_spot"]



__all__ = ["get_live_spot"]
