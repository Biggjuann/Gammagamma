"""Live underlying spot prices (free, no API key).

Databento historical-only licenses lag by ~4 hours so the options chain
we pull is yesterday's close. Spot from put-call parity therefore shows
yesterday's price — users who cross-check against TradingView see values
drift 1–2% below live.

This module pulls *live* spot from Yahoo Finance via yfinance. It's:
* free (no API key)
* near-real-time (15-min delayed for some symbols, live for major indices)
* cached for 5 minutes so we don't hammer Yahoo

Options chain structural levels are still based on yesterday's NBBO, which
is fine — OI and dealer structure change slowly. Only the spot marker
jumps to live so the dashboard feels current.
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


# Map Gammagamma tickers → Yahoo symbols.
# SPX on Yahoo is "^GSPC"; indices use the caret prefix.
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
    """Return the last trade / quote price for ``underlying`` or ``None``.

    Cached for 5 minutes. Failures return ``None`` so callers can fall
    through to put-call parity.
    """
    if yf is None:
        log.warning("yfinance not installed; no live spot for %s", underlying)
        return None

    yahoo = _YAHOO_SYMBOLS.get(underlying)
    if yahoo is None:
        return None

    return cached_call(
        namespace="spot",
        key=yahoo,
        ttl=timedelta(minutes=5),
        loader=lambda: _fetch(yahoo),
    )


def _fetch(yahoo: str) -> Optional[float]:
    try:
        t = yf.Ticker(yahoo)
        fi = getattr(t, "fast_info", None)
        if fi is not None:
            for attr in ("last_price", "regular_market_price", "regularMarketPrice"):
                val = getattr(fi, attr, None) or (
                    fi.get(attr) if hasattr(fi, "get") else None
                )
                if val and val > 0:
                    return float(val)
        # fallback: latest 1m close
        hist = t.history(period="1d", interval="1m")
        if hist is not None and not hist.empty and "Close" in hist.columns:
            last = float(hist["Close"].iloc[-1])
            if last > 0:
                return last
    except Exception as exc:  # noqa: BLE001
        log.warning("yfinance fetch for %s failed: %s", yahoo, exc)
    return None


__all__ = ["get_live_spot"]
