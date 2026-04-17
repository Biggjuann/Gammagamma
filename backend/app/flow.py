"""Options flow analysis: sweep / block detection, OI exceedance, dealer-side inference.

Runs off the same cached Databento trade tape used everywhere else —
no extra quota consumed beyond the 55-second bucket.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List

from .databento_client import TradeTick, get_client
from .gex import ChainSnapshot


@dataclass
class FlowRow:
    instrument_id: int
    strike: float
    expiry: str
    option_type: str
    premium: float
    size: int
    side: str          # "ASK" (bought) / "BID" (sold) / "MID"
    sweep: bool
    block: bool
    oi_exceedance: float
    dealer_side: str   # "short" / "long" from the dealer's POV
    ts: datetime


def _classify_side(raw: str) -> str:
    if raw in ("A", "B"):
        return "ASK" if raw == "A" else "BID"
    return "MID"


def build_flow(
    underlying: str,
    snapshot: ChainSnapshot,
    *,
    sweep_threshold: int = 4,
    block_threshold: int = 250,
) -> List[FlowRow]:
    client = get_client()
    trades: List[TradeTick] = client.recent_trades(underlying)
    if not trades:
        return []

    by_id: Dict[int, List[TradeTick]] = {}
    for t in trades:
        by_id.setdefault(t.instrument_id, []).append(t)

    def_lookup = {r.instrument_id: r for r in snapshot.rows}
    rows: List[FlowRow] = []
    for iid, ticks in by_id.items():
        legs = len({(t.ts.replace(microsecond=0), t.side) for t in ticks})
        is_sweep = legs >= sweep_threshold
        total_size = sum(t.size for t in ticks)
        is_block = any(t.size >= block_threshold for t in ticks)

        # aggregate premium
        prem = sum(t.price * t.size * 100 for t in ticks)

        meta = def_lookup.get(iid)
        if not meta:
            continue
        oi = meta.oi or 1
        oi_exceedance = total_size / oi if oi else 0.0

        # dealer side: public ASK-lift → dealer short; BID-hit → dealer long
        ask_lifts = sum(1 for t in ticks if t.side == "A")
        bid_hits = sum(1 for t in ticks if t.side == "B")
        dealer = (
            "short"
            if ask_lifts > bid_hits
            else "long"
            if bid_hits > ask_lifts
            else "neutral"
        )

        rows.append(
            FlowRow(
                instrument_id=iid,
                strike=meta.strike,
                expiry=meta.expiry.date().isoformat(),
                option_type=meta.option_type,
                premium=prem,
                size=total_size,
                side=_classify_side(max(ticks, key=lambda t: t.size).side),
                sweep=is_sweep,
                block=is_block,
                oi_exceedance=oi_exceedance,
                dealer_side=dealer,
                ts=max(t.ts for t in ticks),
            )
        )

    rows.sort(key=lambda r: r.premium, reverse=True)
    return rows[:50]
