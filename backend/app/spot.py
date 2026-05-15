"""Live underlying spot — Schwab first (when configured), then free fallbacks.

When DATA_SOURCE=schwab and credentials are present, we hit Schwab's
/marketdata/v1/quotes endpoint for true real-time. That removes the
two failure modes that hurt accuracy with the free vendors:

1. Stooq's nq.f returns NQ continuous front but is sometimes off by
   100+ points from the actual front-month NQ futures.
2. Spot×scale drift: chain spot and alias scale are fetched from
   separate Stooq calls at slightly different freshness, so the
   displayed alias spot doesn't equal either input cleanly.

Schwab gives us one authoritative source for all five tickers.

Fallback order if Schwab fails or isn't configured:
1. **Stooq** — CSV, no key, generally works from data centers.
2. **Yahoo v8 chart** — JSON, no key, sometimes IP-blocked.
3. Caller falls through to put-call parity on the chain.
"""
from __future__ import annotations

import csv
import io
import logging
import os
from datetime import timedelta
from typing import Optional

import httpx

from .cache import cached_call

log = logging.getLogger(__name__)


# (stooq symbol, yahoo symbol). Either can be None to skip that source.
_SYMBOL_MAP = {
    "SPX":  ("^spx",    "^GSPC"),
    "SPY":  ("spy.us",  "SPY"),
    "QQQ":  ("qqq.us",  "QQQ"),
    "IWM":  ("iwm.us",  "IWM"),
    "DIA":  ("dia.us",  "DIA"),
    "NDX":  ("^ndx",    "^NDX"),
    "RUT":  ("^rut",    "^RUT"),
    "VIX":  ("^vix",    "^VIX"),
    "ES":   ("es.f",    "ES=F"),
    "NQ":   ("nq.f",    "NQ=F"),
}

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


def get_live_spot(underlying: str) -> Optional[float]:
    """Return the current live price for ``underlying`` (or ``None``).

    Cached 1 minute. Tries Stooq first, Yahoo as backup.
    """
    if underlying not in _SYMBOL_MAP:
        return None

    return cached_call(
        namespace="spot_live",
        key=underlying,
        ttl=timedelta(minutes=1),
        loader=lambda: _fetch(underlying),
    )


def _fetch(underlying: str) -> Optional[float]:
    # Schwab first (real-time) when active. Avoids the off-by-100pt
    # drift we see on Stooq's nq.f and the spot×scale staleness issue.
    if os.getenv("DATA_SOURCE", "").lower() == "schwab":
        try:
            from .schwab_client import fetch_schwab_spot

            px = fetch_schwab_spot(underlying)
            if px is not None:
                log.info("live spot %s = %.2f (schwab)", underlying, px)
                return px
        except Exception as exc:  # noqa: BLE001
            log.warning("schwab spot %s failed, falling back: %s", underlying, exc)

    stooq_sym, yahoo_sym = _SYMBOL_MAP[underlying]

    if stooq_sym:
        px = _fetch_stooq(stooq_sym)
        if px is not None:
            log.info("live spot %s = %.2f (stooq)", underlying, px)
            return px

    if yahoo_sym:
        px = _fetch_yahoo(yahoo_sym)
        if px is not None:
            log.info("live spot %s = %.2f (yahoo)", underlying, px)
            return px

    log.warning("all live-spot sources failed for %s", underlying)
    return None


def _fetch_stooq(symbol: str) -> Optional[float]:
    url = "https://stooq.com/q/l/"
    try:
        r = httpx.get(
            url,
            params={"s": symbol, "f": "sd2t2ohlcv", "h": "", "e": "csv"},
            headers={"User-Agent": _BROWSER_UA},
            timeout=5.0,
        )
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        log.warning("stooq fetch %s failed: %s", symbol, exc)
        return None

    try:
        reader = csv.DictReader(io.StringIO(r.text))
        row = next(reader, None)
        if not row:
            return None
        close = row.get("Close") or row.get("close")
        if close and close not in ("N/D", ""):
            return float(close)
    except (csv.Error, ValueError, StopIteration):
        return None
    return None


def _fetch_yahoo(symbol: str) -> Optional[float]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    try:
        r = httpx.get(
            url,
            params={"interval": "1m", "range": "1d"},
            headers={"User-Agent": _BROWSER_UA, "Accept": "application/json"},
            timeout=5.0,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("yahoo fetch %s failed: %s", symbol, exc)
        return None

    try:
        meta = data["chart"]["result"][0]["meta"]
        for k in ("regularMarketPrice", "previousClose", "chartPreviousClose"):
            val = meta.get(k)
            if val and val > 0:
                return float(val)
    except (KeyError, IndexError, TypeError):
        return None
    return None


__all__ = ["get_live_spot"]
