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


# Scheduled snapshot cadence (local to SCHEDULE_TZ).
# US cash session = 08:30–15:00 CT (= 09:30–16:00 ET).
# Schwab has no daily-credit budget, so we refresh every 5 minutes from the
# cash open through 30 min post-close (structure into next session):
#   08:30, 08:35, … 14:55, 15:00, … 15:30 CT, Mon–Fri.
# Each entry is a set of CronTrigger fields; together they cover the window.
SCHEDULE_INTERVAL_MINUTES = 5
SCHEDULE_CRONS = [
    {"hour": "8", "minute": f"30-55/{SCHEDULE_INTERVAL_MINUTES}"},
    {"hour": "9-14", "minute": f"*/{SCHEDULE_INTERVAL_MINUTES}"},
    {"hour": "15", "minute": f"0-30/{SCHEDULE_INTERVAL_MINUTES}"},
]
SCHEDULE_TZ = "America/Chicago"


@dataclass(frozen=True)
class Settings:
    databento_api_key: str = os.getenv("DATABENTO_API_KEY", "")
    risk_free_rate: float = float(os.getenv("RISK_FREE_RATE", "0.0525"))
    # bundle / chain TTL; matches the 5-minute scheduled cadence so on-demand
    # requests outside the schedule are never more than ~5 min stale.
    refresh_interval_seconds: int = int(os.getenv("REFRESH_INTERVAL_SECONDS", "300"))
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
