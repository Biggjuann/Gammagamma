"""Thin Databento wrapper that is *aggressive* about caching.

Every call to the Databento HTTP / streaming APIs is routed through
``cache.cached_call`` so we fetch each atom of data at most once per its
natural cadence:

| asset                         | TTL       | rationale                                  |
|-------------------------------|-----------|--------------------------------------------|
| ``definitions`` (chain)       | 24 h      | option defs only change at the daily roll  |
| ``statistics`` (open interest)| 12 h      | OI updates overnight                       |
| ``cmbp-1`` NBBO snapshot      | 55 s      | matches the 60 s refresh tick              |
| ``trades`` (flow)             | 55 s      | aligned to refresh tick                    |
| underlying ``ohlcv-1m``       | 55 s      | one call serves every chart consumer       |

If no ``DATABENTO_API_KEY`` is configured the client goes into ``replay``
mode and serves bundled fixture data so the rest of the stack is
exercisable locally.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, List, Optional

import pandas as pd

from .cache import cached_call
from .config import get_settings  # noqa: F401  (used indirectly for cmbp window)

log = logging.getLogger(__name__)

try:
    import databento as db  # type: ignore
except ImportError:  # pragma: no cover - SDK is optional at import time
    db = None  # noqa: N816


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


@dataclass
class OptionDefinition:
    instrument_id: int
    raw_symbol: str
    underlying: str
    expiration: datetime
    strike: float
    option_type: str  # "C" or "P"
    multiplier: int = 100


@dataclass
class Quote:
    instrument_id: int
    bid: float
    ask: float
    bid_size: int
    ask_size: int
    ts: datetime

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        return max(self.bid, self.ask, 0.0)


@dataclass
class TradeTick:
    instrument_id: int
    price: float
    size: int
    side: str  # "A" (ask-lifted), "B" (bid-hit), "N"
    ts: datetime


class DatabentoClient:
    """Cache-first Databento facade.

    Only instantiated once via :func:`get_client`. Holds a single
    ``historical`` handle and (optionally) one ``live`` session.
    """

    def __init__(self) -> None:
        s = get_settings()
        self.dataset = s.opra_dataset
        self._has_key = bool(s.databento_api_key) and db is not None
        self._client = db.Historical(s.databento_api_key) if self._has_key else None
        if not self._has_key:
            log.warning(
                "DATABENTO_API_KEY not set or SDK missing — running in replay mode"
            )

    # ------------------------------------------------------------------
    # Definitions (strike/expiry metadata)
    # ------------------------------------------------------------------
    def get_chain_definitions(
        self, underlying: str, asof: Optional[datetime] = None
    ) -> List[OptionDefinition]:
        asof = asof or datetime.now(timezone.utc)
        # OPRA historical has ~15-30 min lag; use previous trading day for
        # definitions to guarantee the record is available.
        day = _previous_business_day(asof).isoformat()
        key = f"{self.dataset}:{underlying}:{day}"

        def _load() -> List[OptionDefinition]:
            if not self._has_key:
                return _fixture_definitions(underlying)
            parent = _parent_symbol(underlying)
            log.info(
                "databento fetch: definition dataset=%s symbol=%s day=%s",
                self.dataset, parent, day,
            )
            data = self._client.timeseries.get_range(  # type: ignore[union-attr]
                dataset=self.dataset,
                schema="definition",
                symbols=[parent],
                stype_in="parent",
                start=f"{day}T00:00:00",
                end=f"{day}T23:59:59",
            )
            df = data.to_df()
            log.info("databento fetched %d definition rows for %s", len(df), parent)
            return _rows_to_definitions(df, underlying)

        return cached_call(
            namespace="definitions",
            key=key,
            ttl=timedelta(hours=24),
            loader=_load,
        )

    # ------------------------------------------------------------------
    # NBBO snapshot (one row per instrument)
    # ------------------------------------------------------------------
    def snapshot_nbbo(
        self, underlying: str, asof: Optional[datetime] = None
    ) -> List[Quote]:
        asof = asof or datetime.now(timezone.utc)
        # quantise to the minute so parallel callers share a cache slot
        bucket = asof.replace(second=0, microsecond=0).isoformat()
        key = f"{self.dataset}:{underlying}:{bucket}"

        def _load() -> List[Quote]:
            if not self._has_key:
                return _fixture_quotes(underlying)
            parent = _parent_symbol(underlying)
            # bbo-1m = one NBBO row per instrument per minute. ~100× less data
            # than cmbp-1's tick stream. We query the final minute of yesterday's
            # cash session (19:59-20:00 UTC) and take the last row per contract.
            end = _last_settled_close(asof)
            start = end - timedelta(minutes=1)
            log.info(
                "databento fetch: bbo-1m dataset=%s symbol=%s end=%s",
                self.dataset, parent, end.isoformat(),
            )
            data = self._client.timeseries.get_range(  # type: ignore[union-attr]
                dataset=self.dataset,
                schema="bbo-1m",
                symbols=[parent],
                stype_in="parent",
                start=start.isoformat(),
                end=end.isoformat(),
            )
            df = data.to_df()
            log.info("databento fetched %d bbo-1m rows for %s", len(df), parent)
            return _rows_to_quotes(df)

        return cached_call(
            namespace="nbbo",
            key=key,
            ttl=timedelta(seconds=55),
            loader=_load,
        )

    # ------------------------------------------------------------------
    # Open interest statistics (overnight)
    # ------------------------------------------------------------------
    def open_interest(self, underlying: str) -> dict[int, int]:
        day = datetime.now(timezone.utc).date().isoformat()
        key = f"{self.dataset}:{underlying}:{day}"

        def _load() -> dict[int, int]:
            if not self._has_key:
                return _fixture_oi(underlying)
            parent = _parent_symbol(underlying)
            log.info("databento fetch: statistics (OI) symbol=%s day=%s", parent, day)
            data = self._client.timeseries.get_range(  # type: ignore[union-attr]
                dataset=self.dataset,
                schema="statistics",
                symbols=[parent],
                stype_in="parent",
                start=f"{day}T00:00:00",
                end=f"{day}T12:00:00",
            )
            df = data.to_df()
            if df.empty:
                log.warning("databento OI empty for %s on %s", parent, day)
                return {}
            oi = df[df["stat_type"] == 9]  # 9 = open interest
            return dict(zip(oi["instrument_id"].astype(int), oi["quantity"].astype(int)))

        return cached_call(
            namespace="oi",
            key=key,
            ttl=timedelta(hours=12),
            loader=_load,
        )

    # ------------------------------------------------------------------
    # Trade tape (for flow)
    # ------------------------------------------------------------------
    def recent_trades(
        self, underlying: str, window_minutes: int = 5
    ) -> List[TradeTick]:
        now = datetime.now(timezone.utc)
        bucket = now.replace(second=0, microsecond=0).isoformat()
        key = f"{self.dataset}:{underlying}:{bucket}:{window_minutes}"

        def _load() -> List[TradeTick]:
            if not self._has_key:
                return _fixture_trades(underlying)
            data = self._client.timeseries.get_range(  # type: ignore[union-attr]
                dataset=self.dataset,
                schema="trades",
                symbols=[f"{underlying}.OPT"],
                stype_in="parent",
                start=(now - timedelta(minutes=window_minutes)).isoformat(),
                end=now.isoformat(),
                limit=500_000,
            )
            df = data.to_df()
            return _rows_to_trades(df)

        return cached_call(
            namespace="trades",
            key=key,
            ttl=timedelta(seconds=55),
            loader=_load,
        )

    # ------------------------------------------------------------------
    # Underlying spot / OHLCV
    # ------------------------------------------------------------------
    def underlying_ohlcv(self, underlying: str) -> pd.DataFrame:
        now = datetime.now(timezone.utc)
        bucket = now.replace(second=0, microsecond=0).isoformat()
        key = f"{underlying}:{bucket}"

        def _load() -> pd.DataFrame:
            if not self._has_key:
                return _fixture_ohlcv(underlying)
            data = self._client.timeseries.get_range(  # type: ignore[union-attr]
                dataset="XNAS.ITCH" if underlying != "SPX" else "OPRA.PILLAR",
                schema="ohlcv-1m",
                symbols=[underlying],
                start=(now - timedelta(hours=8)).isoformat(),
                end=now.isoformat(),
            )
            return data.to_df()

        return cached_call(
            namespace="ohlcv",
            key=key,
            ttl=timedelta(seconds=55),
            loader=_load,
        )


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------
# Some underlyings need a non-default parent symbol. Index options (SPX, NDX,
# RUT, VIX) trade on their own CBOE roots that differ from the ticker.
_PARENT_SYMBOL_OVERRIDES = {
    # SPX index options use the SPX parent on OPRA already, but some feeds
    # route through SPXW (weeklys). Database override hook left intentionally
    # permissive; most tickers just take ``{sym}.OPT``.
}


def _parent_symbol(underlying: str) -> str:
    return _PARENT_SYMBOL_OVERRIDES.get(underlying, f"{underlying}.OPT")


def _previous_business_day(dt: datetime):
    d = dt.date()
    while True:
        d = d - timedelta(days=1)
        if d.weekday() < 5:  # Mon-Fri
            return d


def _last_settled_close(asof: datetime) -> datetime:
    """Previous business day at 20:00 UTC (~16:00 ET US cash close).

    Databento historical-only licenses lag real-time by several hours. Querying
    yesterday's cash-close window is always inside the historical boundary and
    gives us the overnight structural levels the dashboard actually needs.
    """
    d = _previous_business_day(asof)
    return datetime(d.year, d.month, d.day, 20, 0, 0, tzinfo=timezone.utc)



def _rows_to_definitions(df: pd.DataFrame, underlying: str) -> List[OptionDefinition]:
    defs: List[OptionDefinition] = []
    if df is None or df.empty:
        return defs
    for _, r in df.iterrows():
        try:
            defs.append(
                OptionDefinition(
                    instrument_id=int(r["instrument_id"]),
                    raw_symbol=str(r.get("raw_symbol", "")),
                    underlying=underlying,
                    expiration=pd.to_datetime(r["expiration"]).to_pydatetime(),
                    strike=float(r["strike_price"]) / 1e9
                    if r["strike_price"] > 1e6
                    else float(r["strike_price"]),
                    option_type="C" if str(r.get("instrument_class", "C")).upper().startswith("C") else "P",
                    multiplier=int(r.get("contract_multiplier", 100) or 100),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return defs


def _rows_to_quotes(df: pd.DataFrame) -> List[Quote]:
    if df is None or df.empty:
        return []
    # Newer Databento SDKs put ts_recv / ts_event as the DataFrame *index*.
    # Reset so we can address it as a column regardless of version.
    df = df.reset_index()
    ts_col = next(
        (c for c in ("ts_recv", "ts_event") if c in df.columns),
        None,
    )
    if ts_col:
        df = df.sort_values(ts_col)
    df = df.drop_duplicates("instrument_id", keep="last")

    quotes: List[Quote] = []
    for _, r in df.iterrows():
        bid = _to_float(r.get("bid_px_00") or r.get("bid_px"))
        ask = _to_float(r.get("ask_px_00") or r.get("ask_px"))
        # DBN prices are fixed-point nanos (1e9 scale). A raw mid quote of e.g.
        # 1.25 will be 1_250_000_000 in the column.
        if bid > 1e6:
            bid /= 1e9
        if ask > 1e6:
            ask /= 1e9
        ts_val = r[ts_col] if ts_col else None
        quotes.append(
            Quote(
                instrument_id=int(r["instrument_id"]),
                bid=bid,
                ask=ask,
                bid_size=int(r.get("bid_sz_00") or r.get("bid_sz") or 0),
                ask_size=int(r.get("ask_sz_00") or r.get("ask_sz") or 0),
                ts=pd.to_datetime(ts_val).to_pydatetime() if ts_val is not None
                else datetime.now(timezone.utc),
            )
        )
    return quotes


def _to_float(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _rows_to_trades(df: pd.DataFrame) -> List[TradeTick]:
    if df is None or df.empty:
        return []
    ticks: List[TradeTick] = []
    for _, r in df.iterrows():
        ticks.append(
            TradeTick(
                instrument_id=int(r["instrument_id"]),
                price=float(r.get("price", 0) or 0) / 1e9,
                size=int(r.get("size", 0) or 0),
                side=str(r.get("side", "N")),
                ts=pd.to_datetime(r["ts_recv"]).to_pydatetime(),
            )
        )
    return ticks


# ---------------------------------------------------------------------
# offline fixtures (used when DATABENTO_API_KEY is missing)
# ---------------------------------------------------------------------
def _fixture_definitions(underlying: str) -> List[OptionDefinition]:
    from .fixtures import build_fixture_chain

    return build_fixture_chain(underlying)


def _fixture_quotes(underlying: str) -> List[Quote]:
    from .fixtures import build_fixture_quotes

    return build_fixture_quotes(underlying)


def _fixture_oi(underlying: str) -> dict[int, int]:
    from .fixtures import build_fixture_oi

    return build_fixture_oi(underlying)


def _fixture_trades(underlying: str) -> List[TradeTick]:
    from .fixtures import build_fixture_trades

    return build_fixture_trades(underlying)


def _fixture_ohlcv(underlying: str) -> pd.DataFrame:
    from .fixtures import build_fixture_ohlcv

    return build_fixture_ohlcv(underlying)


_CLIENT: Optional[DatabentoClient] = None


def get_client() -> DatabentoClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = DatabentoClient()
    return _CLIENT


__all__ = [
    "DatabentoClient",
    "OptionDefinition",
    "Quote",
    "TradeTick",
    "get_client",
]
