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
    else:
        report["summary"] = "; ".join(
            f"{c['symbol']}: {c.get('verdict') or c.get('error')}" for c in bad
        )
    return report


__all__ = ["build_report"]
