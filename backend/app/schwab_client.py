"""Schwab Trader API options-chain source.

Schwab's `/marketdata/v1/chains` endpoint returns real-time bid/ask,
open interest, and Greeks for the full chain in a single call — no
EOD cache like marketdata.app, no per-symbol credit budget.

Token model: another service owns the Schwab refresh token and exposes
a shared endpoint that returns a current access token. Gammagamma
fetches from it; we never see the refresh token directly. This avoids
collisions between services that share one Schwab OAuth app.

Env vars:
  * SCHWAB_TOKEN_URL        — full URL of the share endpoint
  * SCHWAB_TOKEN_SHARE_KEY  — shared secret, sent as Bearer
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx
import pandas as pd

from .cache import cached_call
from .config import get_settings
from .databento_client import OptionDefinition, Quote, TradeTick

log = logging.getLogger(__name__)


_SCHWAB_BASE = "https://api.schwabapi.com"


# Index symbols on Schwab's chains endpoint use a $ prefix.
_SCHWAB_SYMBOLS: Dict[str, str] = {
    "SPX": "$SPX",
    "NDX": "$NDX",
    "RUT": "$RUT",
    "VIX": "$VIX",
}


def _schwab_symbol(underlying: str) -> str:
    return _SCHWAB_SYMBOLS.get(underlying, underlying)


def _instrument_id(option_symbol: str) -> int:
    h = hashlib.sha1(option_symbol.encode()).hexdigest()
    return int(h[:15], 16)


# ---------------------------------------------------------------------
# Token fetch — hits the shared endpoint owned by another service.
# Response shape supported:
#   1. JSON  → {"access_token": "...", "expires_in": 1800}
#   2. JSON  → {"token": "..."}  (any common alias)
#   3. Raw text body = the access token
# Default cache lifetime when no expires_in is returned: 25 minutes
# (Schwab tokens are 30 min, so a 5-min safety margin).
# ---------------------------------------------------------------------
class _TokenManager:
    _DEFAULT_TTL_SECONDS = 25 * 60

    def __init__(self) -> None:
        self._url = os.getenv("SCHWAB_TOKEN_URL", "").strip()
        self._share_key = os.getenv("SCHWAB_TOKEN_SHARE_KEY", "").strip()
        self._access_token: Optional[str] = None
        self._access_expires_at: float = 0.0
        self._lock = threading.Lock()
        if not self.has_credentials():
            log.warning(
                "Schwab share-token config incomplete — set "
                "SCHWAB_TOKEN_URL and SCHWAB_TOKEN_SHARE_KEY.",
            )

    def has_credentials(self) -> bool:
        return bool(self._url and self._share_key)

    def get_access_token(self) -> Optional[str]:
        if not self.has_credentials():
            return None
        with self._lock:
            # refresh 60 s before expiry to avoid mid-flight 401s
            if self._access_token and self._access_expires_at - 60 > time.time():
                return self._access_token
            return self._fetch_token_locked()

    def _fetch_token_locked(self) -> Optional[str]:
        try:
            r = httpx.get(
                self._url,
                headers={
                    "Authorization": f"Bearer {self._share_key}",
                    "Accept": "application/json",
                },
                timeout=10.0,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Schwab share-token fetch failed: %s", exc)
            return None
        if r.status_code != 200:
            log.error(
                "Schwab share-token endpoint returned %d: %s — check "
                "SCHWAB_TOKEN_URL / SCHWAB_TOKEN_SHARE_KEY, and verify the "
                "owner service's refresh token hasn't expired.",
                r.status_code, r.text[:200],
            )
            self._access_token = None
            return None

        token: Optional[str] = None
        expires_in = self._DEFAULT_TTL_SECONDS
        try:
            payload = r.json()
            if isinstance(payload, dict):
                token = (
                    payload.get("access_token")
                    or payload.get("accessToken")
                    or payload.get("token")
                )
                ei = payload.get("expires_in") or payload.get("expiresIn")
                if ei:
                    expires_in = int(ei)
            elif isinstance(payload, str):
                token = payload.strip()
        except Exception:  # noqa: BLE001
            # plain-text body
            token = r.text.strip() if r.text else None

        if not token:
            log.error(
                "Schwab share-token response had no token. body=%s",
                r.text[:200],
            )
            self._access_token = None
            return None

        self._access_token = token
        self._access_expires_at = time.time() + expires_in
        log.info("Schwab access token fetched from share endpoint (ttl=%ds)", expires_in)
        return self._access_token


# ---------------------------------------------------------------------
# Client (same shape as MarketdataChainClient / DatabentoClient)
# ---------------------------------------------------------------------
class SchwabChainClient:
    def __init__(self) -> None:
        self.dataset = "SCHWAB"
        self._tokens = _TokenManager()
        if self._tokens.has_credentials():
            log.info("Schwab chain client active (share-token mode)")

    def _fetch_all(
        self, underlying: str
    ) -> Tuple[List[OptionDefinition], List[Quote], Dict[int, int]]:
        settings = get_settings()

        def _load():
            tok = self._tokens.get_access_token()
            if not tok:
                return [], [], {}
            return _fetch_chain(underlying, tok)

        return cached_call(
            namespace="schwab_chain",
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
        # Trade tape is on the streaming API; out of scope for the dashboard.
        return []

    def underlying_ohlcv(self, underlying: str) -> pd.DataFrame:
        # Spot resolves via app.spot (Stooq/Yahoo).
        return pd.DataFrame()


# ---------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------
def _fetch_chain(
    underlying: str, access_token: str
) -> Tuple[List[OptionDefinition], List[Quote], Dict[int, int]]:
    sym = _schwab_symbol(underlying)
    url = f"{_SCHWAB_BASE}/marketdata/v1/chains"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    # strikeCount = strikes above and below ATM. 200 ≈ ±200 strikes covers
    # OTM walls for SPY/QQQ; index chains can be wider but Schwab caps.
    params = {
        "symbol": sym,
        "strikeCount": "200",
        "contractType": "ALL",
    }
    try:
        r = httpx.get(url, headers=headers, params=params, timeout=30.0)
    except Exception as exc:  # noqa: BLE001
        log.warning("Schwab chains %s failed: %s", sym, exc)
        return [], [], {}
    if r.status_code != 200:
        log.warning(
            "Schwab chains %s non-200 (%d): %s",
            sym, r.status_code, r.text[:300],
        )
        return [], [], {}
    try:
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("Schwab chains %s bad JSON: %s", sym, exc)
        return [], [], {}
    if (data.get("status") or "").upper() != "SUCCESS":
        log.warning(
            "Schwab chains %s status=%s body=%s",
            sym, data.get("status"), r.text[:200],
        )
        return [], [], {}

    defs: List[OptionDefinition] = []
    quotes: List[Quote] = []
    oi_map: Dict[int, int] = {}

    for side_key, opt_type in (("callExpDateMap", "C"), ("putExpDateMap", "P")):
        exp_map = data.get(side_key) or {}
        for exp_label, strike_map in exp_map.items():
            # Format: "YYYY-MM-DD:DTE"
            try:
                exp_date_str = exp_label.split(":", 1)[0]
                # 20:00 UTC ≈ 4 PM ET cash close (within DST hour drift,
                # which doesn't matter for DTE bucketing)
                exp_dt = datetime.fromisoformat(exp_date_str).replace(
                    hour=20, minute=0, tzinfo=timezone.utc,
                )
            except (ValueError, AttributeError):
                continue
            for strike_str, rows in (strike_map or {}).items():
                try:
                    strike = float(strike_str)
                except (ValueError, TypeError):
                    continue
                for row in rows or []:
                    osym = str(row.get("symbol", "")).strip()
                    if not osym:
                        continue
                    iid = _instrument_id(osym)
                    defs.append(
                        OptionDefinition(
                            instrument_id=iid,
                            raw_symbol=osym,
                            underlying=underlying,
                            expiration=exp_dt,
                            strike=strike,
                            option_type=opt_type,
                            multiplier=int(row.get("multiplier", 100) or 100),
                        )
                    )
                    bid = float(row.get("bid", 0.0) or 0.0)
                    ask = float(row.get("ask", 0.0) or 0.0)
                    last = float(row.get("last", 0.0) or 0.0)
                    if bid <= 0 and ask <= 0 and last > 0:
                        bid = ask = last
                    quotes.append(
                        Quote(
                            instrument_id=iid,
                            bid=bid,
                            ask=ask,
                            bid_size=int(row.get("bidSize", 0) or 0),
                            ask_size=int(row.get("askSize", 0) or 0),
                            ts=datetime.now(timezone.utc),
                        )
                    )
                    oi_map[iid] = int(row.get("openInterest", 0) or 0)

    exps_seen = len({d.expiration.date().isoformat() for d in defs})
    log.info(
        "Schwab %s: %d contracts across %d expiries",
        underlying, len(defs), exps_seen,
    )
    return defs, quotes, oi_map


__all__ = ["SchwabChainClient"]
