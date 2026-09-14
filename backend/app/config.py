"""Runtime configuration for the Gammagamma backend."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List

from dotenv import load_dotenv

load_dotenv()


DEFAULT_UNIVERSE = ["SPX", "SPY", "QQQ", "ES", "NQ"]


# Display aliases reuse another underlying's option chain and render
# strikes / spot at the alias's price scale. No extra chain fetch.
#   ES → SPY × ~10
#   NQ → QQQ × ~41.5
TICKER_ALIASES = {
    # ES actually trades at ~SPY × 10.084 (SPX/SPY tracking delta + ES
    # futures basis premium). The live ES=F / SPY ratio is used at query
    # time when available; this fallback is the historical average.
    "ES": {"source": "SPY", "scale": 10.084},
    # NQ tracks NDX nearly 1:1 (small futures basis), and NDX ≈ QQQ × ~41.5
    # in the current price regime. Live NQ=F / QQQ ratio overrides at query
    # time; the fallback is the recent running average.
    "NQ": {"source": "QQQ", "scale": 41.5},
}


# Scheduled snapshot times (local to SCHEDULE_TZ).
# US cash session = 08:30–15:00 CT (= 09:30–16:00 ET).
# Schwab has no daily-credit budget, so we can refresh aggressively
# without cost pressure. This schedule captures opening drive, every
# hour of the cash session, the close ramp, and a post-close tick:
#   08:30 CT = cash open
#   09:30 CT
#   10:30 CT
#   11:30 CT
#   12:30 CT = midday / lunch
#   13:30 CT
#   14:30 CT = power hour
#   14:50 CT = last 10 min, where closing imbalance prints
#   15:00 CT = cash close
#   15:30 CT = 30 min post-close (structure into next session)
SCHEDULE_TIMES = [
    (8, 30),
    (9, 30),
    (10, 30),
    (11, 30),
    (12, 30),
    (13, 30),
    (14, 30),
    (14, 50),
    (15, 0),
    (15, 30),
]
SCHEDULE_TZ = "America/Chicago"


@dataclass(frozen=True)
class Settings:
    databento_api_key: str = os.getenv("DATABENTO_API_KEY", "")
    risk_free_rate: float = float(os.getenv("RISK_FREE_RATE", "0.0525"))
    # fallback TTL if the scheduler isn't running; ~12h lets one snapshot last
    # until the next scheduled refresh.
    refresh_interval_seconds: int = int(os.getenv("REFRESH_INTERVAL_SECONDS", "43200"))
    # TTL for raw vendor chain payloads. Deliberately separate from
    # refresh_interval_seconds: that one is a 12h *bundle* fallback, and
    # reusing it for the chain cache meant the chain was fetched once at
    # boot and replayed for the rest of the day, so the 10x/day schedule
    # produced identical snapshots. One hour matches the schedule gap; the
    # scheduled refresh invalidates these caches explicitly anyway, so this
    # only bounds on-demand requests between ticks.
    chain_ttl_seconds: int = int(os.getenv("CHAIN_TTL_SECONDS", "3600"))
    # How long an *empty* bundle stays cached. Short, so a vendor outage or
    # an expired token doesn't freeze the dashboard at zero until the next
    # scheduled tick.
    empty_bundle_ttl_seconds: int = int(os.getenv("EMPTY_BUNDLE_TTL_SECONDS", "120"))
    data_source: str = os.getenv("DATA_SOURCE", "yahoo").strip().lower()
    # cmbp-1 tick-stream window for each snapshot call. 5 s is enough for a
    # point-in-time NBBO without pulling a full minute of tape (~10× saving).
    cmbp_window_seconds: int = int(os.getenv("CMBP_WINDOW_SECONDS", "5"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    universe: List[str] = field(
        default_factory=lambda: [
            t.strip().upper()
            for t in os.getenv(
                "TICKER_UNIVERSE", ",".join(DEFAULT_UNIVERSE)
            ).split(",")
            if t.strip()
        ]
    )
    opra_dataset: str = "OPRA.PILLAR"
    schedule_enabled: bool = os.getenv("SCHEDULE_ENABLED", "true").lower() == "true"


@lru_cache
def get_settings() -> Settings:
    return Settings()
