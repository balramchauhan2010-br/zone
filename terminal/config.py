"""Runtime configuration and safe defaults for the terminal."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # Makes pure unit tests usable before installing optional local env loader.
    def load_dotenv(*args, **kwargs):
        return False

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=False)

IST_ZONE = "Asia/Kolkata"
DHAN_BASE_URL = os.getenv("DHAN_BASE_URL", "https://api.dhan.co/v2").rstrip("/")
DHAN_MASTER_URL = os.getenv(
    "DHAN_MASTER_URL", "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
)
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "").strip()
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
API_ADMIN_TOKEN = os.getenv("APP_ADMIN_TOKEN", "").strip()

def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Opt-in only: Yahoo is research-only cash-equity data, not an F&O/live feed.
YAHOO_RESEARCH_ENABLED = _env_bool("YAHOO_RESEARCH_ENABLED", False)

DEFAULT_ANALYSIS_SETTINGS: dict[str, Any] = {
    "zone_timeframe": "15m",
    "volume_timeframe": "15m",
    "volume_lookback": 30,
    "volume_spike_threshold": 2.1,
    "minimum_volume": 0.0,
    "completed_candles_only": True,
    "eod_high_buffer_pct": 10.0,
    "eod_low_buffer_pct": 10.0,
    "use_eod_range": True,
    "ema_fast": 35,
    "ema_slow": 100,
    "indicator_timeframes": ["15m", "1h", "Daily"],
    "vwap_timeframe": "15m",
    "custom_timeframes": [],
    "account_capital": 25000.0,
    "risk_pct": 0.5,
    "target_rr": 5.0,
    "pcr_scope": "selected_atm_5_each_side",
    "futures_contract": "front",
    "exclude_test_instruments": True,
    "page_size": 20,
}

GLOBAL_CATALOG_PATH = PROJECT_ROOT / "data" / "global_instruments.json"


def dhan_credentials_configured() -> bool:
    return bool(DHAN_ACCESS_TOKEN and DHAN_CLIENT_ID)


def get_global_catalog() -> list[dict[str, Any]]:
    import json

    try:
        with GLOBAL_CATALOG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []
