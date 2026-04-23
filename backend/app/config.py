"""Runtime configuration for the Gammagamma backend."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List

from dotenv import load_dotenv

load_dotenv()


DEFAULT_UNIVERSE = ["SPX", "SPY", "QQQ", "ES"]


# ES is a display alias: it reuses SPY's option chain and renders strikes / spot
# at ES scale (×10). No extra Databento fetch.
TICKER_ALIASES = {
    # ES actually trades at ~SPY × 10.084 (SPX/SPY tracking delta + ES
    # futures basis premium). The live ES=F / SPY ratio is used at query
    # time when available; this fallback is the historical average.
    "ES": {"source": "SPY", "scale": 10.084},
}


# Scheduled snapshot times (local to SCHEDULE_TZ).
#   08:30 CT = cash open  — overnight OI + open structure
#   12:00 CT = midday     — catches any big intraday shift
#   16:00 CT = after close — structure going into next session
SCHEDULE_TIMES = [(8, 30), (12, 0), (16, 0)]
SCHEDULE_TZ = "America/Chicago"


@dataclass(frozen=True)
class Settings:
    databento_api_key: str = os.getenv("DATABENTO_API_KEY", "")
    risk_free_rate: float = float(os.getenv("RISK_FREE_RATE", "0.0525"))
    # fallback TTL if the scheduler isn't running; ~12h lets one snapshot last
    # until the next scheduled refresh.
    refresh_interval_seconds: int = int(os.getenv("REFRESH_INTERVAL_SECONDS", "43200"))
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
