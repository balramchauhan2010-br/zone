"""Historical candle retrieval with bounded caching and provider limits."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from terminal.providers.dhan import DhanProvider
from terminal.timeframes import is_supported_timeframe

IST = ZoneInfo("Asia/Kolkata")
_cache_lock = threading.Lock()
_cache: dict[tuple[Any, ...], tuple[datetime, pd.DataFrame]] = {}


def fetch_candles(
    provider: DhanProvider,
    instrument: dict[str, Any],
    timeframe: str,
    history_days: int | None = None,
    include_oi: bool = False,
    refresh: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not is_supported_timeframe(timeframe):
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    if not instrument.get("security_id") or not instrument.get("exchange_segment"):
        raise ValueError("Instrument mapping is missing security_id or exchange_segment")
    if history_days is None:
        if timeframe in {"Daily", "Weekly", "Monthly"}:
            history_days = 365 * 3 if timeframe != "Monthly" else 365 * 8
        else:
            history_days = 30
    now = datetime.now(IST)
    cache_timeframe = "daily" if timeframe in {"Daily", "Weekly", "Monthly"} else "intraday_1m"
    cache_key = (
        str(instrument.get("security_id")), str(instrument.get("exchange_segment")),
        str(instrument.get("instrument", "FUTSTK")), cache_timeframe, int(history_days), bool(include_oi),
    )
    ttl = 45 if timeframe not in {"Daily", "Weekly", "Monthly"} else 600
    with _cache_lock:
        cached = _cache.get(cache_key)
        if not refresh and cached and (now - cached[0]).total_seconds() < ttl:
            return cached[1].copy(), {
                "provider": provider.name,
                "source_timestamp": cached[1].index[-1].isoformat() if not cached[1].empty else None,
                "received_at": cached[0].isoformat(),
                "cache_hit": True,
                "data_age_seconds": max(0, (now - cached[0]).total_seconds()),
                "provider_request_latency_ms": None,
            }

    from_dt = now - timedelta(days=int(history_days))
    if timeframe in {"Daily", "Weekly", "Monthly"}:
        candles = provider.get_daily_candles(instrument, from_dt, now, include_oi=include_oi)
    else:
        if history_days > 89:
            from_dt = now - timedelta(days=89)
        candles = provider.get_intraday_candles(
            instrument, from_dt, now, interval="1", include_oi=include_oi
        )
    with _cache_lock:
        _cache[cache_key] = (now, candles.copy())
    return candles, {
        "provider": provider.name,
        "source_timestamp": candles.index[-1].isoformat() if not candles.empty else None,
        "received_at": now.isoformat(),
        "cache_hit": False,
        "data_age_seconds": 0.0,
        "history_days_requested": int(history_days),
        "native_interval": "daily" if timeframe in {"Daily", "Weekly", "Monthly"} else "1 minute",
        "provider_request_latency_ms": provider.last_latency_ms,
    }
