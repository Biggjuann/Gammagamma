"""Underlying spot price — anchored to today's cash market open.

Databento historical-only licenses lag real-time by ~4h so put-call parity
on the chain only gives yesterday's close. Users want their dashboard
pinned to today's session, so we use Yahoo Finance as a free spot source
and default to **today's OPEN price** — stable through the day, matches
the reference point every trader uses for daily structure.

Cached 30 min (the open doesn't change intraday; only the fallback live
price might, and we don't need to refresh it fast).
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
    """Return today's cash-session OPEN price (or ``None``).

    Cached 30 minutes. Returns ``None`` on failure so callers can fall
    through to put-call parity.
    """
    if yf is None:
        log.warning("yfinance not installed; no live spot for %s", underlying)
        return None

    yahoo = _YAHOO_SYMBOLS.get(underlying)
    if yahoo is None:
        return None

    return cached_call(
        namespace="spot_open",
        key=yahoo,
        ttl=timedelta(minutes=30),
        loader=lambda: _fetch(yahoo),
    )


def _fetch(yahoo: str) -> Optional[float]:
    """Return today's cash-market OPEN price for ``yahoo`` (or most recent open).

    We anchor the dashboard to the opening print so the session's levels
    remain stable through the day. Falls back to the last 1-minute close if
    the daily open isn't yet populated (pre-market).
    """
    try:
        t = yf.Ticker(yahoo)

        # Daily bar — Open is the regular-session opening print.
        hist = t.history(period="5d", interval="1d")
        if hist is not None and not hist.empty and "Open" in hist.columns:
            open_px = float(hist["Open"].iloc[-1])
            if open_px > 0:
                return open_px

        # Pre-market fallback: first 1-minute bar of the current session.
        intraday = t.history(period="1d", interval="1m")
        if intraday is not None and not intraday.empty and "Open" in intraday.columns:
            first_open = float(intraday["Open"].iloc[0])
            if first_open > 0:
                return first_open

        # Last-resort fallback: most recent trade price.
        fi = getattr(t, "fast_info", None)
        if fi is not None:
            for attr in ("last_price", "regular_market_price", "regularMarketPrice"):
                val = getattr(fi, attr, None) or (
                    fi.get(attr) if hasattr(fi, "get") else None
                )
                if val and val > 0:
                    return float(val)
    except Exception as exc:  # noqa: BLE001
        log.warning("yfinance fetch for %s failed: %s", yahoo, exc)
    return None


__all__ = ["get_live_spot"]
