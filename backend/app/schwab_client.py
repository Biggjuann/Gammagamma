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
from datetime import date, datetime, timedelta, timezone
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


# Schwab quote endpoint accepts different symbol conventions for spot
# quotes than the chains endpoint. ETFs are bare, indices use $, and
# futures use a leading slash (continuous front-month).
_SCHWAB_QUOTE_SYMBOLS: Dict[str, str] = {
    "SPX": "$SPX",
    "NDX": "$NDX",
    "RUT": "$RUT",
    "VIX": "$VIX",
    "ES": "/ES",
    "NQ": "/NQ",
}


def _schwab_quote_symbol(underlying: str) -> str:
    return _SCHWAB_QUOTE_SYMBOLS.get(underlying, underlying)


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

    # A share endpoint is someone else's contract, and not every one of them
    # authenticates with a Bearer header. Try the common schemes in order and
    # keep the first that answers 200 — otherwise a perfectly valid shared
    # token is unreachable purely because of how the key is presented.
    _AUTH_SCHEMES = (
        ("bearer", lambda key: ({"Authorization": f"Bearer {key}"}, {})),
        ("x-api-key", lambda key: ({"X-API-Key": key}, {})),
        ("query-key", lambda key: ({}, {"key": key})),
    )

    def __init__(self) -> None:
        self._url = os.getenv("SCHWAB_TOKEN_URL", "").strip()
        self._share_key = os.getenv("SCHWAB_TOKEN_SHARE_KEY", "").strip()
        self._access_token: Optional[str] = None
        self._access_expires_at: float = 0.0
        self._lock = threading.Lock()
        # Populated on every fetch attempt so /api/diagnostics can explain a
        # failure without anyone tailing container logs.
        self.last_attempts: List[dict] = []
        self.last_token_source: Optional[str] = None
        self.last_payload_keys: Optional[List[str]] = None
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
        self.last_attempts = []
        self.last_token_source = None
        self.last_payload_keys = None

        r = None
        for name, build in self._AUTH_SCHEMES:
            headers, params = build(self._share_key)
            headers = {**headers, "Accept": "application/json"}
            try:
                resp = httpx.get(
                    self._url, headers=headers, params=params, timeout=10.0
                )
            except Exception as exc:  # noqa: BLE001
                self.last_attempts.append(
                    {"scheme": name, "error": f"{type(exc).__name__}: {exc}"}
                )
                log.error("Schwab share-token fetch (%s) failed: %s", name, exc)
                continue
            self.last_attempts.append(
                {"scheme": name, "status": resp.status_code}
            )
            if resp.status_code == 200:
                if name != "bearer":
                    log.warning(
                        "Schwab share-token endpoint accepted the %s scheme, "
                        "not Bearer — the endpoint's auth differs from what "
                        "this service sends by default.",
                        name,
                    )
                r = resp
                break
            log.error(
                "Schwab share-token endpoint returned %d for the %s scheme: "
                "%s",
                resp.status_code, name, resp.text[:200],
            )

        if r is None:
            log.error(
                "Schwab share-token endpoint rejected every auth scheme "
                "(%s) — check SCHWAB_TOKEN_URL / SCHWAB_TOKEN_SHARE_KEY, and "
                "verify the owner service's refresh token hasn't expired.",
                ", ".join(
                    f"{a['scheme']}={a.get('status') or a.get('error')}"
                    for a in self.last_attempts
                ),
            )
            self._access_token = None
            return None

        token: Optional[str] = None
        expires_in = self._DEFAULT_TTL_SECONDS
        payload = None
        try:
            payload = r.json()
        except Exception:  # noqa: BLE001
            payload = None

        if payload is not None:
            if isinstance(payload, dict):
                self.last_payload_keys = sorted(str(k) for k in payload)
            token, source = _extract_token_with_source(payload)
            self.last_token_source = source
            ei = _extract_expires_in(payload)
            if ei:
                expires_in = ei
        if token is None:
            # plain-text body fallback (must still look like a token)
            token = _validated_token(r.text.strip()) if r.text else None

        if not token:
            log.error(
                "Schwab share-token response had no usable token. "
                "status=%d content-type=%r top_level_keys=%s body_preview=%r",
                r.status_code,
                r.headers.get("content-type"),
                self.last_payload_keys,
                _redact(r.text or ""),
            )
            self._access_token = None
            return None

        self._access_token = token
        self._access_expires_at = time.time() + expires_in
        log.info(
            "Schwab access token fetched from share endpoint "
            "(token_len=%d, ttl=%ds, key=%s)",
            len(token), expires_in, self.last_token_source,
        )
        return self._access_token


# Module-level singleton so other modules (spot.py) can reuse the
# same access token without re-initializing the OAuth state.
_TOKEN_MANAGER: Optional[_TokenManager] = None


def _get_token_manager() -> _TokenManager:
    global _TOKEN_MANAGER
    if _TOKEN_MANAGER is None:
        _TOKEN_MANAGER = _TokenManager()
    return _TOKEN_MANAGER


def fetch_schwab_spot(underlying: str) -> Optional[float]:
    """Real-time spot from Schwab's /marketdata/v1/quotes endpoint.

    Returns None if not configured or the call fails — caller should
    fall back to Stooq/Yahoo.
    """
    tokens = _get_token_manager()
    if not tokens.has_credentials():
        return None
    tok = tokens.get_access_token()
    if not tok:
        return None
    sym = _schwab_quote_symbol(underlying)
    try:
        r = httpx.get(
            f"{_SCHWAB_BASE}/marketdata/v1/quotes",
            params={"symbols": sym},
            headers={
                "Authorization": f"Bearer {tok}",
                "Accept": "application/json",
            },
            timeout=10.0,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Schwab quote %s failed: %s", sym, exc)
        return None
    if r.status_code != 200:
        log.warning(
            "Schwab quote %s non-200 (%d): %s",
            sym, r.status_code, r.text[:200],
        )
        return None
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        return None

    # Response shape:
    #   {"SPY": {"quote": {"lastPrice": 742.09, "mark": ..., "closePrice": ...}}, ...}
    # Index/futures responses sometimes nest differently — walk defensively.
    entry = data.get(sym) or data.get(underlying)
    if not isinstance(entry, dict):
        for v in data.values():
            if isinstance(v, dict) and ("quote" in v or "lastPrice" in v):
                entry = v
                break
    if not isinstance(entry, dict):
        return None
    quote = entry.get("quote") if isinstance(entry.get("quote"), dict) else entry
    for k in (
        "lastPrice", "last", "mark", "regularMarketLastPrice",
        "closePrice", "askPrice", "bidPrice",
    ):
        v = quote.get(k)
        if v is not None:
            try:
                fv = float(v)
                if fv > 0:
                    return fv
            except (TypeError, ValueError):
                continue
    return None


def _redact(text: str, limit: int = 300) -> str:
    """Preview a response body with anything token-shaped removed.

    Diagnostics are read over HTTP by whoever can reach the API, so a raw
    body preview must never carry the credential it was fetched to get.
    Any unbroken run of 20+ non-space characters is replaced by its length.
    """
    import re

    cleaned = re.sub(
        r"[^\s\"',}\]]{20,}",
        lambda m: f"<redacted:{len(m.group(0))}>",
        text or "",
    )
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit]


# Plausibility check: real Schwab access tokens are 100–4000 char pure-ASCII
# JWTs with no whitespace. Rejecting anything else avoids passing wrapper HTML
# or full response bodies as the "token" (which then explodes with a
# UnicodeEncodeError when httpx tries to ASCII-encode the Authorization header).
def _validated_token(s: Optional[str]) -> Optional[str]:
    if not isinstance(s, str):
        return None
    s = s.strip()
    if len(s) < 20 or len(s) > 4000:
        return None
    if any(c.isspace() for c in s):
        return None
    try:
        s.encode("ascii")
    except UnicodeEncodeError:
        return None
    return s


# A refresh token must never be sent as a Bearer — Schwab rejects it, and
# it is the long-lived credential. Any key that looks like one is skipped.
def _is_refresh_key(key: str) -> bool:
    return "refresh" in key.lower()


def _extract_token_with_source(payload, path: str = "") -> tuple:
    """Find a plausible access token in JSON of unknown shape.

    Returns ``(token, key_path)`` so callers can report *where* the token
    came from. Three passes, most specific first:

      1. the well-known key names, at this level
      2. the well-known wrapper objects, recursively
      3. a generic walk over every key whose name suggests an access token

    Pass 3 exists because a share endpoint is someone else's contract: a
    payload like ``{"schwabAccessToken": "..."}`` used to fall through
    every explicit name, hit the raw-text fallback, get rejected for
    containing whitespace (it is JSON, after all), and surface as "no
    usable token" — indistinguishable from an expired credential.
    """
    if isinstance(payload, str):
        return _validated_token(payload), path or "<body>"
    if not isinstance(payload, dict):
        return None, None

    for k in ("access_token", "accessToken", "token", "bearer", "bearerToken"):
        v = payload.get(k)
        if isinstance(v, str) and not _is_refresh_key(k):
            tok = _validated_token(v)
            if tok:
                return tok, f"{path}.{k}" if path else k

    for k in ("data", "result", "payload", "response", "schwab"):
        if k in payload:
            tok, src = _extract_token_with_source(
                payload[k], f"{path}.{k}" if path else k
            )
            if tok:
                return tok, src

    # Generic pass: any key that reads like an access token. "access" wins
    # over a bare "token" so {"access": ..., "id_token": ...} picks right.
    def _score(key: str) -> int:
        kl = key.lower()
        if "access" in kl and ("token" in kl or "tok" in kl):
            return 3
        if "token" in kl or "jwt" in kl or "bearer" in kl:
            return 2
        if "access" in kl:
            return 1
        return 0

    candidates = []
    for k, v in payload.items():
        if not isinstance(k, str) or _is_refresh_key(k):
            continue
        score = _score(k)
        if score and isinstance(v, str):
            tok = _validated_token(v)
            if tok:
                candidates.append((score, k, tok))
    if candidates:
        candidates.sort(key=lambda c: -c[0])
        _, k, tok = candidates[0]
        return tok, f"{path}.{k}" if path else k

    # Last resort: recurse into any nested object we have not tried yet.
    for k, v in payload.items():
        if isinstance(v, dict) and k not in (
            "data", "result", "payload", "response", "schwab"
        ):
            tok, src = _extract_token_with_source(v, f"{path}.{k}" if path else k)
            if tok:
                return tok, src
    return None, None


def _extract_token(payload) -> Optional[str]:
    """Backwards-compatible wrapper — see :func:`_extract_token_with_source`."""
    tok, _ = _extract_token_with_source(payload)
    return tok


def _extract_expires_in(payload) -> Optional[int]:
    if not isinstance(payload, dict):
        return None
    for k in ("expires_in", "expiresIn", "ttl", "expiry"):
        v = payload.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
        if isinstance(v, str) and v.isdigit():
            return int(v)
    for k in ("data", "result", "payload", "response"):
        nested = payload.get(k)
        ei = _extract_expires_in(nested) if isinstance(nested, dict) else None
        if ei:
            return ei
    return None


# ---------------------------------------------------------------------
# Client (same shape as MarketdataChainClient / DatabentoClient)
# ---------------------------------------------------------------------
class SchwabChainClient:
    def __init__(self) -> None:
        self.dataset = "SCHWAB"
        self._tokens = _get_token_manager()
        if self._tokens.has_credentials():
            log.info("Schwab chain client active (share-token mode)")

    def _fetch_all(
        self, underlying: str
    ) -> Tuple[List[OptionDefinition], List[Quote], Dict[int, int]]:
        settings = get_settings()

        def _load():
            tok = self._tokens.get_access_token()
            if not tok:
                # Loud, because every downstream symptom of this is silent:
                # the chain comes back empty and the dashboard just shows
                # zeros. cached_call() no longer caches this empty tuple,
                # so the next request retries the token fetch.
                log.error(
                    "Schwab chain %s skipped — no access token. %s",
                    underlying,
                    "SCHWAB_TOKEN_URL / SCHWAB_TOKEN_SHARE_KEY are not set"
                    if not self._tokens.has_credentials()
                    else "the share endpoint did not return a usable token "
                         "(its refresh token may have expired)",
                )
                return [], [], {}
            return _fetch_chain(underlying, tok)

        return cached_call(
            namespace="schwab_chain",
            key=underlying,
            ttl=timedelta(seconds=max(settings.chain_ttl_seconds, 60)),
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
#
# Strategy: Schwab's gateway (Apigee) returns 502 "Body buffer overflow"
# when the chains response exceeds ~5 MB. Wide date windows on tickers
# with daily expirations (SPY, QQQ, SPX) blow past that cap — SPY at
# strikeCount=60 across a 70-day LEAPS window packs ~15 daily expiries
# × ~120 strikes × ~600 bytes each = ~10 MB.
#
# The robust fix: hit /expirationchain first to see the actual listed
# expirations, pick 4 target dates (0DTE / Weekly / Monthly / LEAPS),
# and call /chains once per exact date with fromDate=toDate=that date.
# Each call is guaranteed to hold at most one expiration (~120 strikes,
# ~72 KB) — no cap risk, and coverage is precise instead of guessed.
# ---------------------------------------------------------------------
_TARGET_DTES = [0, 7, 30, 180]


def _fetch_chain(
    underlying: str, access_token: str
) -> Tuple[List[OptionDefinition], List[Quote], Dict[int, int]]:
    sym = _schwab_symbol(underlying)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    target_dates = _pick_target_expirations(sym, headers)
    if not target_dates:
        log.warning(
            "Schwab %s: expirationchain returned nothing, cannot fetch chain",
            sym,
        )
        return [], [], {}

    log.info(
        "Schwab %s: fetching %d target expirations: %s",
        underlying, len(target_dates), [d.isoformat() for d in target_dates],
    )

    all_defs: List[OptionDefinition] = []
    all_quotes: List[Quote] = []
    all_oi: Dict[int, int] = {}

    for exp_date in target_dates:
        defs, quotes, oi = _fetch_single_expiration(
            sym, underlying, headers, exp_date
        )
        all_defs.extend(defs)
        all_quotes.extend(quotes)
        all_oi.update(oi)
        time.sleep(0.3)  # gentle pacing between Schwab calls

    exps_seen = len({d.expiration.date().isoformat() for d in all_defs})
    log.info(
        "Schwab %s merged: %d contracts across %d expiries",
        underlying, len(all_defs), exps_seen,
    )
    return all_defs, all_quotes, all_oi


def _pick_target_expirations(sym: str, headers: dict) -> List[date]:
    """Query /expirationchain and pick 4 target dates near 0/7/30/180 DTE.

    Uses "first listed on-or-after target" so the LEAPS bucket doesn't
    accidentally land on a 178d expiry (which would then be filtered out
    of the LEAPS tab by dte>=180 check in gex.py).
    """
    url = f"{_SCHWAB_BASE}/marketdata/v1/expirationchain"
    try:
        r = httpx.get(
            url,
            headers=headers,
            params={"symbol": sym},
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Schwab expirationchain %s failed: %s", sym, exc)
        return []
    if r.status_code != 200:
        log.warning(
            "Schwab expirationchain %s non-200 (%d): %s",
            sym, r.status_code, r.text[:200],
        )
        return []
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        return []

    parsed: List[date] = []
    for row in data.get("expirationList") or []:
        s = row.get("expirationDate")
        if not s:
            continue
        try:
            parsed.append(date.fromisoformat(s[:10]))
        except (ValueError, TypeError):
            continue
    if not parsed:
        return []
    parsed.sort()

    today = date.today()
    chosen: List[date] = []
    seen = set()
    for dte in _TARGET_DTES:
        goal = today + timedelta(days=dte)
        at_or_after = [d for d in parsed if d >= goal]
        pick = at_or_after[0] if at_or_after else parsed[-1]
        if pick not in seen:
            seen.add(pick)
            chosen.append(pick)
    return sorted(chosen)


def _fetch_single_expiration(
    sym: str,
    underlying: str,
    headers: dict,
    exp_date: date,
) -> Tuple[List[OptionDefinition], List[Quote], Dict[int, int]]:
    url = f"{_SCHWAB_BASE}/marketdata/v1/chains"
    params = {
        "symbol": sym,
        # strikeCount = strikes above AND below ATM. 60 → 120 strikes total.
        # Covers ±$300 on SPY (typical OTM wall zone) and ±$1500 on SPX.
        # With fromDate=toDate=one expiry, response is ~72 KB — no cap risk.
        "strikeCount": "60",
        "contractType": "ALL",
        "fromDate": exp_date.isoformat(),
        "toDate": exp_date.isoformat(),
    }
    try:
        r = httpx.get(url, headers=headers, params=params, timeout=30.0)
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "Schwab chains %s [%s] failed: %s", sym, exp_date, exc,
        )
        return [], [], {}
    if r.status_code != 200:
        log.warning(
            "Schwab chains %s [%s] non-200 (%d): %s",
            sym, exp_date, r.status_code, r.text[:200],
        )
        return [], [], {}
    try:
        data = r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "Schwab chains %s [%s] bad JSON: %s", sym, exp_date, exc,
        )
        return [], [], {}
    if (data.get("status") or "").upper() != "SUCCESS":
        log.warning(
            "Schwab chains %s [%s] status=%s body=%s",
            sym, exp_date, data.get("status"), r.text[:200],
        )
        return [], [], {}

    return _parse_chain_payload(data, underlying)


def _parse_chain_payload(
    data: dict, underlying: str
) -> Tuple[List[OptionDefinition], List[Quote], Dict[int, int]]:
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

    return defs, quotes, oi_map


__all__ = ["SchwabChainClient"]
