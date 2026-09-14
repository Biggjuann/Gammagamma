"""Data-source diagnostics.

When the dashboard shows zeros there are only a handful of causes:
the configured source has no credentials, the credentials stopped
working, the vendor rate-limited us, or the chain came back but every
row was filtered out. The REST surface returns 200 with empty rows in
all four cases, so this module answers "why is it empty?" directly —
without shell access to the container logs.

Nothing here returns a secret: credentials are reported as booleans and
token values only as lengths.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .config import TICKER_ALIASES, get_settings

log = logging.getLogger(__name__)


def _source_credentials(source: str) -> Dict[str, object]:
    """Which env vars the active source needs, and whether they are set."""
    required = {
        "schwab": ["SCHWAB_TOKEN_URL", "SCHWAB_TOKEN_SHARE_KEY"],
        "marketdata": ["MARKETDATA_TOKEN"],
        "databento": ["DATABENTO_API_KEY"],
        "yahoo": [],
    }.get(source, [])
    present = {name: bool(os.getenv(name, "").strip()) for name in required}
    return {
        "required": required,
        "present": present,
        "complete": all(present.values()) if required else True,
    }


def _schwab_token_probe() -> Dict[str, object]:
    """Try the share endpoint. This is the #1 cause of a silent empty chain."""
    from .schwab_client import _get_token_manager

    tokens = _get_token_manager()
    if not tokens.has_credentials():
        return {
            "configured": False,
            "ok": False,
            "detail": "SCHWAB_TOKEN_URL / SCHWAB_TOKEN_SHARE_KEY not set",
        }
    try:
        tok = tokens.get_access_token()
    except Exception as exc:  # noqa: BLE001
        return {"configured": True, "ok": False, "detail": f"{type(exc).__name__}: {exc}"}
    if not tok:
        return {
            "configured": True,
            "ok": False,
            "detail": (
                "share endpoint returned no usable token — check that the "
                "owning service's Schwab refresh token is still valid "
                "(they expire every 7 days)"
            ),
        }
    return {"configured": True, "ok": True, "token_length": len(tok)}


_SCHWAB_STATUS_HINTS = {
    400: "bad request — usually a symbol Schwab doesn't recognise for this "
         "endpoint (indices need the $ prefix, e.g. $SPX)",
    401: "token rejected — the share endpoint handed us a token Schwab will "
         "not accept. Either it is expired, or it was minted for a different "
         "Schwab app than the one calling.",
    403: "forbidden — the token is valid but the Schwab app behind it is not "
         "entitled to Market Data. Market Data is a separate API product from "
         "Accounts & Trading and must be approved on the app.",
    404: "not found — endpoint path or symbol is wrong",
    429: "rate limited by Schwab",
}


def _schwab_api_probe(symbol: str) -> Dict[str, object]:
    """Call Schwab's market-data endpoints and report the raw HTTP result.

    Credentials being *present* says nothing about whether Schwab accepts
    them. This separates the three failure modes that all surface as an
    empty chain: token rejected (401), app not entitled to market data
    (403), and symbol not recognised (400 / empty expirationList).

    Only Schwab's own status codes and a short body preview are reported —
    never the token or the share key.
    """
    import httpx

    from .schwab_client import (
        _SCHWAB_BASE,
        _get_token_manager,
        _schwab_symbol,
    )

    out: Dict[str, object] = {"symbol": symbol}
    tok = _get_token_manager().get_access_token()
    if not tok:
        out["error"] = "no access token — see schwab_token above"
        return out

    sym = _schwab_symbol(symbol)
    out["schwab_symbol"] = sym
    headers = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}

    def _call(name: str, path: str, params: dict) -> Optional[dict]:
        entry: Dict[str, object] = {"endpoint": path, "params": params}
        try:
            r = httpx.get(
                f"{_SCHWAB_BASE}{path}", headers=headers, params=params, timeout=20.0
            )
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"{type(exc).__name__}: {exc}"
            out[name] = entry
            return None
        entry["status"] = r.status_code
        if r.status_code in _SCHWAB_STATUS_HINTS:
            entry["hint"] = _SCHWAB_STATUS_HINTS[r.status_code]
        if r.status_code != 200:
            entry["body_preview"] = (r.text or "")[:300].replace("\n", " ")
            out[name] = entry
            return None
        try:
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"non-JSON body: {exc}"
            out[name] = entry
            return None
        out[name] = entry
        return data

    exp = _call(
        "expirationchain",
        "/marketdata/v1/expirationchain",
        {"symbol": sym},
    )
    if exp is None:
        return out

    listed = exp.get("expirationList") or []
    probe = out["expirationchain"]
    assert isinstance(probe, dict)
    probe["expirations_returned"] = len(listed)
    probe["first_few"] = [
        row.get("expirationDate") for row in listed[:5] if isinstance(row, dict)
    ]
    if not listed:
        probe["hint"] = (
            "200 OK but no expirations — Schwab does not list options under "
            f"{sym!r}. Check the symbol mapping."
        )
        return out

    # One real chain call against the nearest listed expiry.
    nearest = None
    for row in listed:
        if isinstance(row, dict) and row.get("expirationDate"):
            nearest = str(row["expirationDate"])[:10]
            break
    if not nearest:
        return out

    chain = _call(
        "chains",
        "/marketdata/v1/chains",
        {
            "symbol": sym,
            "strikeCount": "5",
            "contractType": "ALL",
            "fromDate": nearest,
            "toDate": nearest,
        },
    )
    if chain is None:
        return out

    entry = out["chains"]
    assert isinstance(entry, dict)
    entry["schwab_status"] = chain.get("status")
    calls = chain.get("callExpDateMap") or {}
    puts = chain.get("putExpDateMap") or {}
    entry["call_expiries"] = len(calls)
    entry["put_expiries"] = len(puts)
    if not calls and not puts:
        entry["hint"] = (
            "200 OK but both expiry maps are empty — Schwab returned no "
            "contracts for this date window."
        )
    return out


def _probe_symbol(symbol: str) -> Dict[str, object]:
    """Count what the active client actually returns for one symbol.

    Goes through the same cached path the dashboard uses, so this reports
    the state the dashboard is in rather than triggering a separate fetch.
    """
    from .databento_client import get_client

    out: Dict[str, object] = {"symbol": symbol}
    client = get_client()
    try:
        defs = client.get_chain_definitions(symbol)
        quotes = client.snapshot_nbbo(symbol)
        oi = client.open_interest(symbol)
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    out["definitions"] = len(defs)
    out["quotes"] = len(quotes)
    out["open_interest_entries"] = len(oi)
    out["priced_quotes"] = sum(1 for q in quotes if getattr(q, "mid", 0) > 0)
    out["nonzero_oi"] = sum(1 for v in oi.values() if v > 0)
    out["expirations"] = sorted(
        {d.expiration.date().isoformat() for d in defs}
    )[:12]

    try:
        from .spot import get_live_spot

        out["spot"] = get_live_spot(symbol)
    except Exception as exc:  # noqa: BLE001
        out["spot_error"] = f"{type(exc).__name__}: {exc}"

    if not defs:
        out["verdict"] = "NO CHAIN — the source returned zero contracts"
    elif not quotes:
        out["verdict"] = "NO QUOTES — contracts listed but no bid/ask"
    elif not out["priced_quotes"]:
        out["verdict"] = "NO PRICES — every quote is zero-width; IV cannot solve"
    elif not out["nonzero_oi"]:
        out["verdict"] = "NO OPEN INTEREST — GEX falls back to placeholder OI=1"
    else:
        out["verdict"] = "OK"
    return out


def build_report(*, symbols: Optional[List[str]] = None) -> dict:
    """Full picture of what the chain pipeline is seeing right now."""
    settings = get_settings()
    source = settings.data_source

    report: dict = {
        "asof": datetime.now(timezone.utc).isoformat(),
        "data_source": source,
        "credentials": _source_credentials(source),
        "universe": settings.universe,
        "ttl_seconds": {
            "chain": settings.chain_ttl_seconds,
            "bundle": settings.refresh_interval_seconds,
            "empty_bundle": settings.empty_bundle_ttl_seconds,
        },
        "scheduler_enabled": settings.schedule_enabled,
    }

    if source == "schwab":
        report["schwab_token"] = _schwab_token_probe()

    # Aliases (ES, NQ) reuse another symbol's chain — probe the source once.
    requested = symbols or settings.universe
    sources: List[str] = []
    for sym in requested:
        alias = TICKER_ALIASES.get(sym)
        src = alias["source"] if alias else sym
        if src not in sources:
            sources.append(src)

    report["chains"] = [_probe_symbol(src) for src in sources]

    # When Schwab is active and something is empty, go one level deeper and
    # ask Schwab directly — the HTTP status is what separates "token
    # expired" from "app not entitled" from "wrong symbol".
    if source == "schwab" and report.get("schwab_token", {}).get("ok"):
        if any(c.get("verdict") != "OK" for c in report["chains"]):
            report["schwab_api"] = [_schwab_api_probe(src) for src in sources]

    # Databento without a key silently serves the offline fixture chain —
    # it looks healthy but the numbers are synthetic, so call it out.
    report["fixtures_active"] = (
        source == "databento" and not os.getenv("DATABENTO_API_KEY", "").strip()
    )

    bad = [c for c in report["chains"] if c.get("verdict") != "OK"]
    missing = [k for k, v in report["credentials"]["present"].items() if not v]

    # Observed chain state outranks credential inference: if contracts are
    # coming through, the pipeline is working whatever the env looks like.
    if not bad:
        report["summary"] = (
            "All chains returning data"
            + (" (synthetic fixtures — DATABENTO_API_KEY is not set)"
               if report["fixtures_active"] else "")
            + "."
        )
    elif missing:
        report["summary"] = (
            f"DATA_SOURCE={source} but {', '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} not set — "
            "the chain fetch cannot run."
        )
    elif source == "schwab" and not report.get("schwab_token", {}).get("ok"):
        report["summary"] = (
            "Schwab token unavailable: "
            f"{report['schwab_token'].get('detail')}"
        )
    elif report.get("schwab_api"):
        # Lead with whatever Schwab itself said — that is the root cause.
        parts = []
        for probe in report["schwab_api"]:
            for name in ("expirationchain", "chains"):
                call = probe.get(name)
                if not isinstance(call, dict):
                    continue
                if call.get("error"):
                    parts.append(f"{probe['symbol']} {name}: {call['error']}")
                elif call.get("hint"):
                    parts.append(
                        f"{probe['symbol']} {name} HTTP "
                        f"{call.get('status')}: {call['hint']}"
                    )
        report["summary"] = (
            "; ".join(dict.fromkeys(parts))
            if parts
            else "; ".join(
                f"{c['symbol']}: {c.get('verdict') or c.get('error')}" for c in bad
            )
        )
    else:
        report["summary"] = "; ".join(
            f"{c['symbol']}: {c.get('verdict') or c.get('error')}" for c in bad
        )
    return report


__all__ = ["build_report"]
