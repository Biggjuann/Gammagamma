"""marketdata.app options-chain source.

Free tier: 100 requests/day. One call per underlying returns the full
chain with strike, expiry, type, bid/ask, last, open interest, volume,
IV and full greeks. That means at 2 scheduled snapshots/day × 3 tickers
we use 6 requests/day — well inside the free quota.

API reference: https://www.marketdata.app/docs/api/options/chain

Auth: Bearer token in ``Authorization`` header. Token is read from the
``MARKETDATA_TOKEN`` env var so it never touches the repo.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx
import pandas as pd

from .cache import cached_call
from .config import get_settings
from .databento_client import OptionDefinition, Quote, TradeTick

log = logging.getLogger(__name__)


_MD_BASE = "https://api.marketdata.app/v1"

# Most tickers pass through as-is. Indices on marketdata.app are bare
# (no caret prefix) — SPX is "SPX", NDX is "NDX".
_MD_SYMBOLS: Dict[str, str] = {
    "SPY": "SPY",
    "QQQ": "QQQ",
    "SPX": "SPX",
    "IWM": "IWM",
    "DIA": "DIA",
    "NDX": "NDX",
    "RUT": "RUT",
    "VIX": "VIX",
}


def _marketdata_symbol(underlying: str) -> str:
    return _MD_SYMBOLS.get(underlying, underlying)


def _instrument_id(contract_symbol: str) -> int:
    h = hashlib.sha1(contract_symbol.encode()).hexdigest()
    return int(h[:15], 16)


# ---------------------------------------------------------------------
# Client (same shape as YahooChainClient / DatabentoClient)
# ---------------------------------------------------------------------
class MarketdataChainClient:
    def __init__(self) -> None:
        self.dataset = "MARKETDATA"
        self._token = os.getenv("MARKETDATA_TOKEN", "").strip()
        self._has_key = bool(self._token)
        if self._has_key:
            log.info("marketdata.app chain client active")
        else:
            log.warning(
                "MARKETDATA_TOKEN not set — marketdata fetches will be skipped",
            )

    def _fetch_all(
        self, underlying: str
    ) -> Tuple[List[OptionDefinition], List[Quote], Dict[int, int]]:
        settings = get_settings()

        def _load():
            if not self._has_key:
                return [], [], {}
            return _fetch_chain(underlying, self._token)

        return cached_call(
            namespace="md_chain",
            key=underlying,
            ttl=timedelta(seconds=max(settings.refresh_interval_seconds - 5, 60)),
            loader=_load,
        )

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
        # marketdata.app doesn't expose the trade tape on the free tier.
        return []

    def underlying_ohlcv(self, underlying: str) -> pd.DataFrame:
        # Spot comes from app.spot (Stooq/Yahoo).
        return pd.DataFrame()


# ---------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------
def _fetch_chain(
    underlying: str, token: str
) -> Tuple[List[OptionDefinition], List[Quote], Dict[int, int]]:
    sym = _marketdata_symbol(underlying)
    url = f"{_MD_BASE}/options/chain/{sym}/"
    headers = {
        "Authorization": f"Token {token}",
        "Accept": "application/json",
    }

    # SPY has 10k+ contracts; the default 20s isn't enough for their
    # endpoint to serialize and return. Bump high and retry once.
    data = _http_get_json(url, headers, timeout=60.0)
    if data is None:
        log.warning("marketdata fetch %s returned None; retrying once", sym)
        data = _http_get_json(url, headers, timeout=90.0)
    if data is None:
        return [], [], {}

    if data.get("s") != "ok":
        log.warning(
            "marketdata %s returned non-ok status: s=%s errmsg=%s",
            sym, data.get("s"), data.get("errmsg"),
        )
        return [], [], {}

    n = len(data.get("optionSymbol", []))
    if n == 0:
        log.warning("marketdata %s returned zero contracts", sym)
        return [], [], {}

    # Diagnostic: how many distinct expiries did we get?
    exps = set()
    for ts in data.get("expiration", []):
        try:
            exps.add(int(ts))
        except (TypeError, ValueError):
            continue
    log.info(
        "marketdata chain %s: %d contracts across %d expiries",
        underlying, n, len(exps),
    )

    defs: List[OptionDefinition] = []
    quotes: List[Quote] = []
    oi_map: Dict[int, int] = {}

    for i in range(n):
        try:
            cs = str(data["optionSymbol"][i])
            strike = float(data["strike"][i])
            exp_ts = int(data["expiration"][i])
            side_raw = str(data["side"][i]).lower()
            side = "C" if side_raw.startswith("c") else "P"

            iid = _instrument_id(cs)

            defs.append(
                OptionDefinition(
                    instrument_id=iid,
                    raw_symbol=cs,
                    underlying=underlying,
                    expiration=datetime.fromtimestamp(exp_ts, tz=timezone.utc),
                    strike=strike,
                    option_type=side,
                    multiplier=100,
                )
            )

            bid = _f(data, "bid", i)
            ask = _f(data, "ask", i)
            last = _f(data, "last", i)
            if bid <= 0 and ask <= 0 and last > 0:
                bid = ask = last

            updated_ts = int(data.get("updated", [0] * n)[i] or 0)
            ts = (
                datetime.fromtimestamp(updated_ts, tz=timezone.utc)
                if updated_ts > 0
                else datetime.now(timezone.utc)
            )

            quotes.append(
                Quote(
                    instrument_id=iid,
                    bid=bid,
                    ask=ask,
                    bid_size=_i(data, "bidSize", i),
                    ask_size=_i(data, "askSize", i),
                    ts=ts,
                )
            )

            oi_map[iid] = _i(data, "openInterest", i)
        except (KeyError, ValueError, TypeError):
            continue

    return defs, quotes, oi_map


def _http_get_json(url: str, headers: dict, timeout: float) -> Optional[dict]:
    try:
        r = httpx.get(url, headers=headers, timeout=timeout)
    except httpx.TimeoutException as exc:
        log.warning("marketdata timeout after %.0fs: %s", timeout, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("marketdata request failed: %s", exc)
        return None

    if r.status_code == 401:
        log.error(
            "marketdata 401 — check MARKETDATA_TOKEN (got: %s)", r.text[:200],
        )
        return None
    if r.status_code == 429:
        log.warning("marketdata 429 — over free-tier quota for today")
        return None
    if r.status_code == 402:
        log.error("marketdata 402 — endpoint requires a paid tier")
        return None
    try:
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("marketdata parse failed: %s", exc)
        return None


def _f(data: dict, key: str, i: int) -> float:
    try:
        v = data.get(key, [])[i]
        if v is None:
            return 0.0
        return float(v)
    except (TypeError, ValueError, IndexError):
        return 0.0


def _i(data: dict, key: str, i: int) -> int:
    try:
        v = data.get(key, [])[i]
        if v is None:
            return 0
        return int(v)
    except (TypeError, ValueError, IndexError):
        return 0


__all__ = ["MarketdataChainClient"]
