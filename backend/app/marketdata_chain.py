"""marketdata.app options-chain source.

Free tier: 100 requests/day. We pull ~8 strategically-spaced expiries per
underlying (0DTE, +1w, +2w, +1M, +2M, +3M, +6M, +1Y) so the dashboard's
Expiry filter buttons (0DTE / Weekly / Monthly / LEAPS) all light up.

Per-snapshot budget per underlying: 1 expirations call + ~8 chain calls
= ~9 requests. 3 tickers × 2 scheduled snapshots/day = ~54 requests/day,
inside the free 100/day quota.

API reference: https://www.marketdata.app/docs/api/options/chain
Auth: Bearer token in ``Authorization`` header; token read from the
``MARKETDATA_TOKEN`` env var.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
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
    headers = {
        "Authorization": f"Token {token}",
        "Accept": "application/json",
    }

    # 1. Get the full list of available expirations.
    exp_list = _fetch_expirations(sym, headers)
    if not exp_list:
        log.warning(
            "marketdata %s: expirations endpoint empty; falling back to default chain call",
            sym,
        )
        return _fetch_single_expiry(sym, headers, expiration=None, underlying=underlying)

    # 2. Pick a small set of expiries. marketdata.app's free tier
    #    has a tight burst limit (~6-8 calls before 429), so we keep
    #    per-ticker calls low: 0DTE / +7d / +30d / +90d.
    targets = _pick_target_expiries(exp_list)
    log.info(
        "marketdata %s: pulling %d of %d expiries: %s",
        underlying, len(targets), len(exp_list), targets,
    )

    # 3. Fetch each with generous pacing to stay under burst limits.
    all_defs: List[OptionDefinition] = []
    all_quotes: List[Quote] = []
    all_oi: Dict[int, int] = {}

    for exp in targets:
        defs, quotes, oi = _fetch_single_expiry(
            sym, headers, expiration=exp, underlying=underlying
        )
        all_defs.extend(defs)
        all_quotes.extend(quotes)
        all_oi.update(oi)
        time.sleep(1.0)  # 1s between calls — keeps us below burst threshold

    exps_seen = len({d.expiration.date().isoformat() for d in all_defs})
    log.info(
        "marketdata %s merged: %d contracts across %d expiries",
        underlying, len(all_defs), exps_seen,
    )
    return all_defs, all_quotes, all_oi


def _fetch_expirations(sym: str, headers: dict) -> List[str]:
    """Return the symbol's available expiry list as YYYY-MM-DD strings.

    Cached 24h since listed expirations only change at expiration rolls.
    """

    def _load() -> List[str]:
        url = f"{_MD_BASE}/options/expirations/{sym}/"
        data = _http_get_json(url, headers, timeout=15.0)
        if data is None or data.get("s") != "ok":
            return []
        return [str(e) for e in (data.get("expirations") or [])]

    return cached_call(
        namespace="md_expirations",
        key=sym,
        ttl=timedelta(hours=24),
        loader=_load,
    )


def _pick_target_expiries(all_exps: List[str]) -> List[str]:
    """Pick ~4 strategically-spaced expiries.

    Targets: 0DTE, +7d, +30d, +90d — covers 0DTE / weekly / monthly /
    leaps-ish. Fewer targets keeps us inside marketdata's free-tier
    burst limit.
    """
    today = date.today()
    target_dtes = [0, 7, 30, 90]

    parsed: List[Tuple[str, date]] = []
    for e in all_exps:
        try:
            parsed.append((e, date.fromisoformat(e[:10])))
        except (ValueError, TypeError):
            continue
    if not parsed:
        return []
    parsed.sort(key=lambda x: x[1])

    selected = []
    seen = set()
    for dte in target_dtes:
        goal = today + timedelta(days=dte)
        closest = min(parsed, key=lambda x: abs((x[1] - goal).days))
        if closest[0] not in seen:
            seen.add(closest[0])
            selected.append(closest[0])
    return sorted(selected)


def _fetch_single_expiry(
    sym: str,
    headers: dict,
    expiration: Optional[str],
    underlying: str,
) -> Tuple[List[OptionDefinition], List[Quote], Dict[int, int]]:
    url = f"{_MD_BASE}/options/chain/{sym}/"
    # CRITICAL credit-saver: marketdata.app charges per symbol in the
    # response when bid/ask/mid/last columns are present. Without filters
    # a full SPY chain can be ~300 credits per expiry; with these filters
    # ~60. Docs: https://www.marketdata.app/docs/api/rate-limiting
    #
    # strikeLimit=200 covers ±100 strikes around ATM. Narrower values
    # (e.g. 100) trap the response inside the ATM gamma peak zone, which
    # makes call and put walls converge on the same strike. Real dealer
    # walls typically sit 20-50 strikes OTM on either side.
    params: dict = {
        "strikeLimit": "200",
        "mode": "cached",            # EOD-cached quotes: cheaper on paid plans
    }
    if expiration:
        params["expiration"] = expiration

    data = _http_get_json(url, headers, timeout=60.0, params=params)
    if data is None:
        data = _http_get_json(url, headers, timeout=90.0, params=params)
    if data is None:
        # mode=cached isn't available on Starter Trial / Trader Trial. If the
        # first attempt returned None (including a hypothetical 402), retry
        # without mode=cached.
        params.pop("mode", None)
        log.info("marketdata %s (exp=%s): retrying without mode=cached", sym, expiration)
        data = _http_get_json(url, headers, timeout=60.0, params=params)
    if data is None:
        return [], [], {}

    if data.get("s") != "ok":
        log.warning(
            "marketdata %s (exp=%s) non-ok: s=%s errmsg=%s",
            sym, expiration, data.get("s"), data.get("errmsg"),
        )
        return [], [], {}

    n = len(data.get("optionSymbol", []))
    if n == 0:
        return [], [], {}

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


def _http_get_json(
    url: str, headers: dict, timeout: float, params: Optional[dict] = None
) -> Optional[dict]:
    try:
        r = httpx.get(url, headers=headers, params=params or {}, timeout=timeout)
    except httpx.TimeoutException as exc:
        log.warning("marketdata timeout after %.0fs (%s): %s", timeout, url, exc)
        return None
    except Exception as exc:  # noqa: BLE001
        log.warning("marketdata request failed (%s): %s", url, exc)
        return None

    # Log first 300 chars of body + response headers so we can see WHY the
    # server is rejecting — free-tier quota, rate limit, plan restriction,
    # or a plain auth bug.
    body_preview = r.text[:300].replace("\n", " ") if r.text else "<empty>"
    rl_limit = r.headers.get("x-ratelimit-limit")
    rl_remaining = r.headers.get("x-ratelimit-remaining")
    rl_reset = r.headers.get("x-ratelimit-reset")
    rl_consumed = r.headers.get("x-ratelimit-consumed")

    if r.status_code == 401:
        log.error(
            "marketdata 401 (url=%s) — check MARKETDATA_TOKEN. body=%s",
            url, body_preview,
        )
        return None
    if r.status_code == 402:
        log.error(
            "marketdata 402 — endpoint requires a paid tier. body=%s", body_preview,
        )
        return None
    if r.status_code == 429:
        log.warning(
            "marketdata 429 (url=%s) limit=%s remaining=%s consumed=%s reset=%s body=%s",
            url, rl_limit, rl_remaining, rl_consumed, rl_reset, body_preview,
        )
        return None
    try:
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "marketdata parse failed (status=%s): %s. body=%s",
            r.status_code, exc, body_preview,
        )
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
