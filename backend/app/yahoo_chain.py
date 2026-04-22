"""Yahoo Finance options-chain source — free, no API key, has real OI.

Drop-in replacement for :mod:`app.databento_client`. Provides the same
three methods consumed by :mod:`app.gex`:

  * ``get_chain_definitions(underlying)`` → List[OptionDefinition]
  * ``snapshot_nbbo(underlying)``          → List[Quote]
  * ``open_interest(underlying)``          → Dict[instrument_id, int]
  * ``recent_trades(underlying)``          → List[TradeTick]   (empty)
  * ``underlying_ohlcv(underlying)``       → empty DataFrame

All three option-chain methods draw from a single internal fetch per
(underlying, refresh-window), so calling all three costs one pass over
Yahoo's API — not three.

Yahoo's v7 options endpoint returns one expiry at a time. We hit:

  GET  /v7/finance/options/<symbol>              # metadata + first expiry
  GET  /v7/finance/options/<symbol>?date=<ts>    # each subsequent expiry

Roughly 15 expiries × 500 ms pacing = ~8 s per underlying, cached for
the full refresh window. ~1-2 MB per ticker per snapshot.
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx
import pandas as pd

from .cache import cached_call
from .config import get_settings
from .databento_client import (  # reuse the dataclasses so gex.py stays agnostic
    OptionDefinition,
    Quote,
    TradeTick,
)

log = logging.getLogger(__name__)


_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


# Map Gammagamma ticker → Yahoo options symbol. Indices use caret prefix.
_YAHOO_OPTION_SYMBOLS: Dict[str, str] = {
    "SPY": "SPY",
    "QQQ": "QQQ",
    "IWM": "IWM",
    "DIA": "DIA",
    # SPX index options on Yahoo. If this 404s, client falls back to SPY.
    "SPX": "^SPX",
    "NDX": "^NDX",
    "RUT": "^RUT",
    "VIX": "^VIX",
}


def _yahoo_symbol(underlying: str) -> str:
    return _YAHOO_OPTION_SYMBOLS.get(underlying, underlying)


def _instrument_id(contract_symbol: str) -> int:
    """Stable 63-bit int derived from Yahoo's contractSymbol."""
    h = hashlib.sha1(contract_symbol.encode()).hexdigest()
    return int(h[:15], 16)


# ---------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------
class YahooChainClient:
    def __init__(self) -> None:
        self.dataset = "YAHOO"  # for log parity with DatabentoClient
        self._has_key = True    # always on
        log.info("Yahoo chain client active (free, no key)")

    # ---- core full-chain fetch (cached) -----------------------------
    def _fetch_all(
        self, underlying: str
    ) -> Tuple[List[OptionDefinition], List[Quote], Dict[int, int]]:
        settings = get_settings()
        key = underlying

        def _load():
            return _fetch_full_chain(underlying)

        return cached_call(
            namespace="yahoo_chain",
            key=key,
            ttl=timedelta(seconds=max(settings.refresh_interval_seconds - 5, 60)),
            loader=_load,
        )

    # ---- public methods (same shape as DatabentoClient) -------------
    def get_chain_definitions(
        self, underlying: str, asof: Optional[datetime] = None
    ) -> List[OptionDefinition]:
        defs, _, _ = self._fetch_all(underlying)
        return defs

    def snapshot_nbbo(
        self, underlying: str, asof: Optional[datetime] = None
    ) -> List[Quote]:
        _, quotes, _ = self._fetch_all(underlying)
        return quotes

    def open_interest(self, underlying: str) -> Dict[int, int]:
        _, _, oi = self._fetch_all(underlying)
        return oi

    def recent_trades(
        self, underlying: str, window_minutes: int = 10
    ) -> List[TradeTick]:
        # Yahoo doesn't expose the trade tape. Flow panel shows "no data".
        return []

    def underlying_ohlcv(self, underlying: str) -> pd.DataFrame:
        # Spot comes from the standalone spot fetcher; the options chain
        # path doesn't need underlying OHLCV.
        return pd.DataFrame()


# ---------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------
def _fetch_full_chain(
    underlying: str,
) -> Tuple[List[OptionDefinition], List[Quote], Dict[int, int]]:
    ysym = _yahoo_symbol(underlying)

    defs: List[OptionDefinition] = []
    quotes: List[Quote] = []
    oi_map: Dict[int, int] = {}

    # 1. First call — returns metadata (expirationDates) AND the first expiry.
    first = _get_chain_json(ysym)
    if not first:
        log.warning("yahoo chain: no result for %s", underlying)
        return defs, quotes, oi_map

    expirations = first.get("expirationDates") or []
    log.info(
        "yahoo chain %s: %d expirations to pull",
        underlying, len(expirations),
    )

    # ingest the first expiry right away
    if first.get("options"):
        _ingest_expiry(first["options"][0], underlying, defs, quotes, oi_map)

    # 2. Pull each additional expiry, with polite pacing to avoid 429s.
    first_ts = first["options"][0]["expirationDate"] if first.get("options") else None
    for ts in expirations:
        if ts == first_ts:
            continue
        data = _get_chain_json(ysym, exp_ts=ts)
        if data and data.get("options"):
            _ingest_expiry(data["options"][0], underlying, defs, quotes, oi_map)
        time.sleep(0.3)

    log.info(
        "yahoo chain %s: %d contracts, %d OI entries",
        underlying, len(defs), len(oi_map),
    )
    return defs, quotes, oi_map


def _get_chain_json(ysym: str, exp_ts: Optional[int] = None) -> Optional[dict]:
    url = f"https://query1.finance.yahoo.com/v7/finance/options/{ysym}"
    params = {"date": exp_ts} if exp_ts else {}
    try:
        r = httpx.get(
            url,
            params=params,
            headers={"User-Agent": _BROWSER_UA, "Accept": "application/json"},
            timeout=10.0,
        )
        if r.status_code == 429:
            log.warning("yahoo 429 — sleeping 2s and retrying")
            time.sleep(2.0)
            r = httpx.get(
                url,
                params=params,
                headers={"User-Agent": _BROWSER_UA, "Accept": "application/json"},
                timeout=10.0,
            )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("yahoo chain fetch %s (date=%s) failed: %s", ysym, exp_ts, exc)
        return None

    try:
        return data["optionChain"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return None


def _ingest_expiry(
    expiry_block: dict,
    underlying: str,
    defs: List[OptionDefinition],
    quotes: List[Quote],
    oi_map: Dict[int, int],
) -> None:
    exp_ts = expiry_block.get("expirationDate")
    if not exp_ts:
        return
    # Yahoo timestamps are Eastern-close-aligned; coerce to UTC.
    exp_dt = datetime.fromtimestamp(int(exp_ts), tz=timezone.utc)

    for side, rows in (("C", expiry_block.get("calls", [])),
                       ("P", expiry_block.get("puts", []))):
        for row in rows:
            cs = row.get("contractSymbol") or ""
            try:
                strike = float(row.get("strike") or 0)
            except (TypeError, ValueError):
                continue
            if strike <= 0:
                continue

            iid = _instrument_id(cs)
            defs.append(
                OptionDefinition(
                    instrument_id=iid,
                    raw_symbol=cs,
                    underlying=underlying,
                    expiration=exp_dt,
                    strike=strike,
                    option_type=side,
                    multiplier=100,
                )
            )

            bid = _f(row.get("bid"))
            ask = _f(row.get("ask"))
            last = _f(row.get("lastPrice"))
            # Some thinly-traded contracts have no bid/ask; fall back to last.
            if bid <= 0 and ask <= 0 and last > 0:
                bid = ask = last

            last_trade_ts = row.get("lastTradeDate") or exp_ts
            try:
                ts = datetime.fromtimestamp(int(last_trade_ts), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                ts = datetime.now(timezone.utc)

            quotes.append(
                Quote(
                    instrument_id=iid,
                    bid=bid,
                    ask=ask,
                    bid_size=0,
                    ask_size=0,
                    ts=ts,
                )
            )

            oi = row.get("openInterest")
            if oi is None:
                oi = 0
            try:
                oi_map[iid] = int(oi)
            except (TypeError, ValueError):
                oi_map[iid] = 0


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


__all__ = ["YahooChainClient"]
