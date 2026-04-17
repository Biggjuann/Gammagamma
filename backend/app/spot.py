"""Underlying spot price — anchored to the session the chain data is from.

The Databento options chain we pull is yesterday's cash close (previous
business day). For internal consistency we anchor spot to the **same day's
cash-market open**, so walls, flip, GVWAP, and spot all describe the same
moment in time.

yfinance daily bars are free and include Open/High/Low/Close for every
session. We grab the Open from the session that matches the chain date.

Cached 24h since the historical open doesn't change.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
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


def _previous_business_day(d: date) -> date:
    day = d
    while True:
        day = day - timedelta(days=1)
        if day.weekday() < 5:
            return day


def get_session_open(
    underlying: str, session_date: Optional[date] = None
) -> Optional[float]:
    """Return the cash-market OPEN for ``session_date`` (default = previous business day).

    Matches the same day as the Databento chain we query, so spot and
    structural levels are internally consistent.
    """
    if yf is None:
        log.warning("yfinance not installed; no spot for %s", underlying)
        return None

    yahoo = _YAHOO_SYMBOLS.get(underlying)
    if yahoo is None:
        return None

    if session_date is None:
        session_date = _previous_business_day(datetime.now(timezone.utc).date())

    key = f"{yahoo}:{session_date.isoformat()}"

    return cached_call(
        namespace="spot_open_by_date",
        key=key,
        ttl=timedelta(hours=24),
        loader=lambda: _fetch(yahoo, session_date),
    )


# Back-compat: old callers expected a live spot. They now receive the
# anchored session open instead.
get_live_spot = get_session_open


def _fetch(yahoo: str, session_date: date) -> Optional[float]:
    try:
        t = yf.Ticker(yahoo)
        # Pull a small window around the target date so weekends / holidays
        # don't leave us empty.
        start = session_date - timedelta(days=1)
        end = session_date + timedelta(days=2)
        hist = t.history(start=start.isoformat(), end=end.isoformat(), interval="1d")
        if hist is None or hist.empty or "Open" not in hist.columns:
            return None
        # Normalise the index to date for matching (yfinance returns timestamps).
        hist = hist.reset_index()
        date_col = "Date" if "Date" in hist.columns else hist.columns[0]
        for _, row in hist.iterrows():
            ts = row[date_col]
            try:
                row_date = ts.date() if hasattr(ts, "date") else ts
            except Exception:  # noqa: BLE001
                continue
            if row_date == session_date:
                open_px = float(row["Open"])
                if open_px > 0:
                    return open_px
        # Fallback: last available open in the window.
        open_px = float(hist["Open"].iloc[-1])
        return open_px if open_px > 0 else None
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "yfinance open fetch for %s on %s failed: %s",
            yahoo, session_date, exc,
        )
    return None


__all__ = ["get_session_open", "get_live_spot"]



__all__ = ["get_live_spot"]
