"""Databento wrapper calibrated for a historical-OPRA-only subscription.

Design rules, all enforced here:

1. Every request routes through :func:`cache.cached_call` so each atom is
   fetched at most once per its natural cadence.
2. All queries target **settled historical data** — previous business day's
   definitions/statistics, and the last minute of yesterday's US cash session
   for NBBO. Avoids 422 ``data_end_after_available_end`` on licences that lag
   real-time.
3. Only the OPRA.PILLAR dataset is touched. No equity / futures / ITCH feeds,
   so no license errors on auxiliary datasets. Spot is derived from the
   options chain itself via put-call parity (see ``gex.build_chain_snapshot``).
4. Every loader is resilient: empty / invalid responses return empty
   collections, not exceptions. A partial chain is better than a 500.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from .cache import cached_call
from .config import get_settings  # noqa: F401

log = logging.getLogger(__name__)

try:
    import databento as db  # type: ignore
except ImportError:  # pragma: no cover
    db = None  # noqa: N816


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------------
# data classes
# ---------------------------------------------------------------------
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
    side: str
    ts: datetime


# ---------------------------------------------------------------------
# client
# ---------------------------------------------------------------------
class DatabentoClient:
    def __init__(self) -> None:
        s = get_settings()
        self.dataset = s.opra_dataset
        self._has_key = bool(s.databento_api_key) and db is not None
        self._client = db.Historical(s.databento_api_key) if self._has_key else None
        if not self._has_key:
            log.warning(
                "DATABENTO_API_KEY not set or SDK missing — running in replay mode"
            )

    # -- definitions ---------------------------------------------------
    def get_chain_definitions(
        self, underlying: str, asof: Optional[datetime] = None
    ) -> List[OptionDefinition]:
        asof = asof or datetime.now(timezone.utc)
        day = (
            _working_query_day(self._client).isoformat()
            if self._has_key
            else _previous_business_day(asof).isoformat()
        )
        key = f"{self.dataset}:{underlying}:{day}"

        def _load() -> List[OptionDefinition]:
            if not self._has_key:
                return _fixture_definitions(underlying)
            parent = _parent_symbol(underlying)
            log.info(
                "databento fetch: definition symbol=%s day=%s", parent, day,
            )
            try:
                data = self._client.timeseries.get_range(  # type: ignore[union-attr]
                    dataset=self.dataset,
                    schema="definition",
                    symbols=[parent],
                    stype_in="parent",
                    start=f"{day}T00:00:00",
                    end=f"{day}T23:59:59",
                )
                df = data.to_df()
            except Exception as exc:  # noqa: BLE001
                log.exception("definition fetch failed for %s: %s", parent, exc)
                return []
            defs = _rows_to_definitions(df, underlying)
            log.info(
                "databento %s: %d definition rows → %d options",
                parent, len(df), len(defs),
            )
            return defs

        return cached_call(
            namespace="definitions",
            key=key,
            ttl=timedelta(hours=24),
            loader=_load,
        )

    # -- NBBO snapshot -------------------------------------------------
    def snapshot_nbbo(
        self, underlying: str, asof: Optional[datetime] = None
    ) -> List[Quote]:
        asof = asof or datetime.now(timezone.utc)
        # Quantise to the day so callers within the same session reuse one
        # fetch (bundle cache handles TTL; this dedupes loader work).
        bucket = _last_settled_close(asof).date().isoformat()
        key = f"{self.dataset}:{underlying}:{bucket}"

        def _load() -> List[Quote]:
            if not self._has_key:
                return _fixture_quotes(underlying)
            parent = _parent_symbol(underlying)
            end = _last_settled_close(asof)
            start = end - timedelta(minutes=1)
            log.info(
                "databento fetch: cbbo-1m symbol=%s end=%s", parent, end.isoformat(),
            )
            # Let exceptions bubble up — cached_call won't cache empties and
            # allow_stale_on_error will fall back to the last good snapshot.
            data = self._client.timeseries.get_range(  # type: ignore[union-attr]
                dataset=self.dataset,
                schema="cbbo-1m",
                symbols=[parent],
                stype_in="parent",
                start=start.isoformat(),
                end=end.isoformat(),
            )
            df = data.to_df()
            quotes = _rows_to_quotes(df)
            log.info(
                "databento %s: %d cbbo-1m rows → %d quotes",
                parent, len(df), len(quotes),
            )
            return quotes

        return cached_call(
            namespace="nbbo",
            key=key,
            ttl=timedelta(hours=6),
            loader=_load,
        )

    # -- open interest -------------------------------------------------
    def open_interest(self, underlying: str) -> Dict[int, int]:
        day = (
            _working_query_day(self._client).isoformat()
            if self._has_key
            else _previous_business_day(datetime.now(timezone.utc)).isoformat()
        )
        key = f"{self.dataset}:{underlying}:{day}"

        def _load() -> Dict[int, int]:
            if not self._has_key:
                return _fixture_oi(underlying)
            parent = _parent_symbol(underlying)
            log.info("databento fetch: statistics symbol=%s day=%s", parent, day)
            # OI is published after the cash close. Narrow the window to
            # 20:00-23:00 UTC so we don't pull the full day's statistics
            # stream (can be multi-GB per ticker).
            try:
                data = self._client.timeseries.get_range(  # type: ignore[union-attr]
                    dataset=self.dataset,
                    schema="statistics",
                    symbols=[parent],
                    stype_in="parent",
                    start=f"{day}T20:00:00",
                    end=f"{day}T23:00:00",
                )
                df = data.to_df()
            except Exception as exc:  # noqa: BLE001
                log.warning("OI statistics failed for %s: %s", parent, exc)
                df = None
            if df is not None and not df.empty and "stat_type" in df.columns:
                try:
                    oi = df[df["stat_type"] == 9]
                    if not oi.empty:
                        return dict(
                            zip(
                                oi["instrument_id"].astype(int),
                                oi["quantity"].astype(int),
                            )
                        )
                except (KeyError, ValueError):
                    pass

            # Fallback: daily volume per instrument via ohlcv-1d. Much smaller
            # than the statistics stream, still gives a reasonable distribution
            # across strikes so call/put walls diverge.
            log.info(
                "OI empty for %s — falling back to ohlcv-1d daily volume", parent,
            )
            try:
                data = self._client.timeseries.get_range(  # type: ignore[union-attr]
                    dataset=self.dataset,
                    schema="ohlcv-1d",
                    symbols=[parent],
                    stype_in="parent",
                    start=f"{day}T00:00:00",
                    end=f"{day}T23:59:59",
                )
                df = data.to_df()
            except Exception as exc:  # noqa: BLE001
                log.warning("ohlcv-1d fallback failed for %s: %s", parent, exc)
                return {}
            if df is None or df.empty:
                return {}
            df = df.reset_index()
            if "instrument_id" not in df.columns or "volume" not in df.columns:
                return {}
            try:
                return dict(
                    zip(
                        df["instrument_id"].astype(int),
                        df["volume"].astype(int),
                    )
                )
            except (KeyError, ValueError):
                return {}

        return cached_call(
            namespace="oi",
            key=key,
            ttl=timedelta(hours=12),
            loader=_load,
        )

    # -- trades (flow) -------------------------------------------------
    def recent_trades(
        self, underlying: str, window_minutes: int = 10
    ) -> List[TradeTick]:
        # Query the last N minutes of yesterday's cash session so flow
        # stays inside the historical licence boundary.
        end = _last_settled_close(datetime.now(timezone.utc))
        start = end - timedelta(minutes=window_minutes)
        key = f"{self.dataset}:{underlying}:{end.isoformat()}:{window_minutes}"

        def _load() -> List[TradeTick]:
            if not self._has_key:
                return _fixture_trades(underlying)
            parent = _parent_symbol(underlying)
            log.info(
                "databento fetch: trades symbol=%s window=%sm end=%s",
                parent, window_minutes, end.isoformat(),
            )
            try:
                data = self._client.timeseries.get_range(  # type: ignore[union-attr]
                    dataset=self.dataset,
                    schema="trades",
                    symbols=[parent],
                    stype_in="parent",
                    start=start.isoformat(),
                    end=end.isoformat(),
                    limit=50_000,
                )
                df = data.to_df()
            except Exception as exc:  # noqa: BLE001
                log.warning("trades fetch failed for %s: %s", parent, exc)
                return []
            ticks = _rows_to_trades(df)
            log.info(
                "databento %s: %d trades rows → %d ticks",
                parent, len(df), len(ticks),
            )
            return ticks

        return cached_call(
            namespace="trades",
            key=key,
            ttl=timedelta(hours=6),
            loader=_load,
        )

    # -- underlying OHLCV (not available under options-only license) ---
    def underlying_ohlcv(self, underlying: str) -> pd.DataFrame:
        """Empty by design.

        We don't have a licence for equity / index / futures datasets. Spot
        is derived in :func:`gex.build_chain_snapshot` via put-call parity.
        """
        return pd.DataFrame()


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------
_PARENT_SYMBOL_OVERRIDES: Dict[str, str] = {
    # SPX options trade as ``SPX.OPT`` and ``SPXW.OPT`` (weeklys) on OPRA.
    # Parent "SPX.OPT" pulls both roots because Databento groups them.
}


def _parent_symbol(underlying: str) -> str:
    return _PARENT_SYMBOL_OVERRIDES.get(underlying, f"{underlying}.OPT")


def _previous_business_day(dt: datetime) -> date:
    d = dt.date()
    while True:
        d = d - timedelta(days=1)
        if d.weekday() < 5:
            return d


def _nth_business_day_back(dt: datetime, n: int) -> date:
    """Return the date that is ``n`` business days before ``dt``."""
    d = dt.date()
    count = 0
    while count < n:
        d = d - timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d


# Cached result of probing for a date Databento lets us query. The licence
# boundary message is inconsistent, so we empirically find a working date
# once and reuse it all day.
_WORKING_DAY_CACHE: Dict[str, date] = {}


def _working_query_day(client) -> date:
    """Find the most recent business day whose full session is licensed.

    Probes with an end time of 23:59 — the latest any of our real queries
    goes. A date only qualifies if that end is inside Databento's licence
    window, otherwise cbbo-1m (end=20:00) and statistics (end=23:00)
    would 403 later.
    """
    today_key = datetime.now(timezone.utc).date().isoformat()
    if today_key in _WORKING_DAY_CACHE:
        return _WORKING_DAY_CACHE[today_key]

    now = datetime.now(timezone.utc)
    for n in range(1, 10):
        day = _nth_business_day_back(now, n)
        try:
            probe = client.timeseries.get_range(
                dataset="OPRA.PILLAR",
                schema="definition",
                symbols=["SPY.OPT"],
                stype_in="parent",
                start=f"{day}T23:00:00",
                end=f"{day}T23:59:59",
                limit=10,
            )
            _ = probe.to_df()
            log.info(
                "probe OK: databento accepts full-day range for %s", day,
            )
            _WORKING_DAY_CACHE[today_key] = day
            return day
        except Exception as exc:  # noqa: BLE001
            log.warning("probe full-day for %s rejected: %s", day, exc)
            continue

    fallback = _nth_business_day_back(now, 2)
    log.error("no working databento day found; using %s", fallback)
    return fallback


def _last_settled_close(asof: datetime) -> datetime:
    """Last business-day cash close in UTC that the Databento licence accepts.

    Uses ``_working_query_day`` if a live client is available so NBBO, trades,
    and statistics all target the same vetted date.
    """
    from .databento_client import _working_query_day  # late import

    client = get_client()._client  # type: ignore[attr-defined]
    if client is not None:
        try:
            d = _working_query_day(client)
        except Exception:  # noqa: BLE001
            d = _previous_business_day(asof)
    else:
        d = _previous_business_day(asof)
    return datetime(d.year, d.month, d.day, 20, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------
# DataFrame → dataclass converters (SDK-version tolerant)
# ---------------------------------------------------------------------
_CALL_CLASSES = {"C", "CALL", "OPT", "OC"}
_PUT_CLASSES = {"P", "PUT", "OP"}


def _coerce_price(v) -> float:
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    # DBN fixed-point nanos: a price of 5.25 arrives as 5_250_000_000.
    # Anything above 1e6 is definitely nanos (option prices are <$10k).
    return f / 1e9 if f > 1e6 else f


def _coerce_strike(v) -> float:
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return 0.0
    # Strikes can be nanos (5250_000_000_000 for SPX 5250) or plain decimals.
    # Anything over 1e6 that doesn't look like a real strike is scaled.
    return f / 1e9 if f > 1e6 else f


def _rows_to_definitions(df: pd.DataFrame, underlying: str) -> List[OptionDefinition]:
    if df is None or df.empty:
        return []
    df = df.reset_index()
    defs: List[OptionDefinition] = []
    for _, r in df.iterrows():
        try:
            cls_raw = str(r.get("instrument_class", "")).strip().upper()
            if cls_raw in _CALL_CLASSES:
                cp = "C"
            elif cls_raw in _PUT_CLASSES:
                cp = "P"
            else:
                # skip anything that isn't a call/put (futures, combos, etc.)
                continue

            strike = _coerce_strike(r.get("strike_price"))
            if strike <= 0:
                continue

            exp_raw = r.get("expiration")
            if exp_raw is None or (isinstance(exp_raw, float) and pd.isna(exp_raw)):
                continue
            exp = pd.to_datetime(exp_raw, utc=True, errors="coerce")
            if pd.isna(exp):
                continue

            defs.append(
                OptionDefinition(
                    instrument_id=int(r["instrument_id"]),
                    raw_symbol=str(r.get("raw_symbol", "")),
                    underlying=underlying,
                    expiration=exp.to_pydatetime(),
                    strike=strike,
                    option_type=cp,
                    multiplier=int(r.get("contract_multiplier") or 100),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return defs


def _rows_to_quotes(df: pd.DataFrame) -> List[Quote]:
    if df is None or df.empty:
        return []
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
        try:
            bid = _coerce_price(r.get("bid_px_00") or r.get("bid_px"))
            ask = _coerce_price(r.get("ask_px_00") or r.get("ask_px"))
            ts_val = r[ts_col] if ts_col else None
            ts = (
                pd.to_datetime(ts_val, utc=True).to_pydatetime()
                if ts_val is not None and not pd.isna(ts_val)
                else datetime.now(timezone.utc)
            )
            quotes.append(
                Quote(
                    instrument_id=int(r["instrument_id"]),
                    bid=bid,
                    ask=ask,
                    bid_size=int(r.get("bid_sz_00") or r.get("bid_sz") or 0),
                    ask_size=int(r.get("ask_sz_00") or r.get("ask_sz") or 0),
                    ts=ts,
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    return quotes


def _rows_to_trades(df: pd.DataFrame) -> List[TradeTick]:
    if df is None or df.empty:
        return []
    df = df.reset_index()
    ts_col = next(
        (c for c in ("ts_recv", "ts_event") if c in df.columns),
        None,
    )
    ticks: List[TradeTick] = []
    for _, r in df.iterrows():
        try:
            ts_val = r[ts_col] if ts_col else None
            ts = (
                pd.to_datetime(ts_val, utc=True).to_pydatetime()
                if ts_val is not None and not pd.isna(ts_val)
                else datetime.now(timezone.utc)
            )
            ticks.append(
                TradeTick(
                    instrument_id=int(r["instrument_id"]),
                    price=_coerce_price(r.get("price")),
                    size=int(r.get("size") or 0),
                    side=str(r.get("side", "N")),
                    ts=ts,
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
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


def _fixture_oi(underlying: str) -> Dict[int, int]:
    from .fixtures import build_fixture_oi

    return build_fixture_oi(underlying)


def _fixture_trades(underlying: str) -> List[TradeTick]:
    from .fixtures import build_fixture_trades

    return build_fixture_trades(underlying)


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
