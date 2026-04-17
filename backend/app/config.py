"""Runtime configuration for the Gammagamma backend."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import List

from dotenv import load_dotenv

load_dotenv()


DEFAULT_UNIVERSE = [
    "SPX", "SPY", "QQQ", "IWM", "DIA", "VIX", "NDX", "RUT",
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA",
    "AMD", "AVGO", "NFLX", "COST", "JPM", "V", "XOM", "UNH", "JNJ",
    "WMT", "PG", "MA", "HD", "CVX", "LLY", "ABBV", "MRK", "PEP", "KO",
    "BAC", "CRM", "ORCL", "MCD", "ADBE", "CSCO", "NKE", "TMO", "ABT",
    "DIS", "INTC", "VZ", "T", "PFE", "WFC", "LIN", "BMY", "CMCSA",
    "PM", "RTX", "UPS", "HON", "IBM", "QCOM", "AMGN", "LOW", "GS",
    "CAT", "DE", "BA", "MS", "BLK", "NOW", "AXP", "UBER", "INTU",
    "BKNG", "SBUX", "SPGI", "TXN", "AMAT", "ISRG", "TJX", "PANW",
    "PLTR", "SMCI", "COIN", "ARKK", "SOXL", "TQQQ", "SQQQ", "GLD",
    "SLV", "USO", "TLT", "HYG", "XLF", "XLE", "XLK", "XLV", "SMH",
]


@dataclass(frozen=True)
class Settings:
    databento_api_key: str = os.getenv("DATABENTO_API_KEY", "")
    risk_free_rate: float = float(os.getenv("RISK_FREE_RATE", "0.0525"))
    refresh_interval_seconds: int = int(os.getenv("REFRESH_INTERVAL_SECONDS", "60"))
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
