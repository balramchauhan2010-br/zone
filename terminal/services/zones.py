"""Adapter around the supplied zone_core; no substitute zone algorithm is used."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from terminal.timeframes import is_supported_timeframe, completed_bars, resample_ohlcv
from terminal.services.risk import estimate_futures_position
from terminal.zone_core import latest_active_zones, scan_zones

_SNAKE_TO_CORE = {
    "eod_high_buffer_pct": "eodHighBufferPct",
    "eod_low_buffer_pct": "eodLowBufferPct",
    "use_eod_range": "useEodRange",
    "account_capital": "accountCapital",
    "risk_pct": "riskPct",
    "target_rr": "targetRR",
    "sl_buffer_atr": "slBufferAtr",
    "atr_period": "atrPeriod",
    "vol_sma_period": "volSmaPeriod",
    "min_valid_score": "minValidScore",
    "hq_score_threshold": "hqScoreThreshold",
}


def zone_params_from_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    source = settings or {}
    out: dict[str, Any] = {}
    for key, value in source.items():
        core_key = _SNAKE_TO_CORE.get(key, key)
        if core_key in {
            "eodHighBufferPct", "eodLowBufferPct", "useEodRange", "accountCapital", "riskPct",
            "targetRR", "slBufferAtr", "atrPeriod", "volSmaPeriod", "minValidScore", "hqScoreThreshold",
        }:
            out[core_key] = value
    return out


def scan_symbol_zones(
    candles: pd.DataFrame,
    symbol: str,
    timeframe: str,
    settings: dict[str, Any] | None = None,
    include_broken: bool = False,
    lot_size: float | int | None = None,
) -> dict[str, Any]:
    if not is_supported_timeframe(timeframe):
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    if candles is None or candles.empty:
        return {"status": "UNAVAILABLE", "symbol": symbol, "timeframe": timeframe, "zones": [], "reason": "No verified candles"}

    bars = completed_bars(candles, timeframe)
    if bars.empty:
        return {"status": "UNAVAILABLE", "symbol": symbol, "timeframe": timeframe, "zones": [], "reason": "No completed candles for selected timeframe"}
    if not {"open", "high", "low", "close", "volume"}.issubset(bars.columns):
        return {"status": "UNAVAILABLE", "symbol": symbol, "timeframe": timeframe, "zones": [], "reason": "OHLCV columns are incomplete"}

    params = zone_params_from_settings(settings)
    # scan_zones uses the supplied core's actual rules and state machine.
    zones = scan_zones(bars, params=params, tf=timeframe)
    active = latest_active_zones(zones)
    selected = zones if include_broken else active
    current_price = float(bars["close"].iloc[-1])
    atr_value = _atr_value(bars, int(params.get("atrPeriod", 14)))
    records: list[dict[str, Any]] = []
    for zone in selected:
        low = min(float(zone.proxVal), float(zone.distVal))
        high = max(float(zone.proxVal), float(zone.distVal))
        if current_price < low:
            distance = low - current_price
        elif current_price > high:
            distance = current_price - high
        else:
            distance = 0.0
        age_bars = max(0, len(bars) - 1 - int(zone.createdBarIndex))
        position_size = estimate_futures_position(
            float(params.get("accountCapital", 25000.0)),
            float(params.get("riskPct", 0.5)),
            float(zone.proxVal), float(zone.slVal), lot_size,
        )
        rr_reference = (abs(float(zone.tpVal) - float(zone.proxVal)) / abs(float(zone.proxVal) - float(zone.slVal))) if abs(float(zone.proxVal) - float(zone.slVal)) else None
        records.append({
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "direction": "DEMAND" if zone.isDemand else "SUPPLY",
            "zone_low": low,
            "zone_high": high,
            "proximal": float(zone.proxVal),
            "distal": float(zone.distVal),
            "state": zone.state,
            "touch_count": int(zone.touchCount),
            "freshness_bars": age_bars,
            "created_at": _iso(zone.timestamp),
            "pattern": zone.patternType,
            "category": zone.zoneCategory,
            "score": int(zone.densityScore),
            "score_10": float(zone.score10),
            "high_quality": bool(zone.isHQ),
            "leg_out_high": float(zone.legOutHigh),
            "leg_out_low": float(zone.legOutLow),
            "stop_loss_reference": float(zone.slVal),
            "target_reference": float(zone.tpVal),
            "risk_pct_at_creation": _finite_or_none(zone.riskPct),
            "risk_reward_reference": rr_reference,
            "lot_size": lot_size,
            "position_sizing": position_size,
            "current_price": current_price,
            "distance_from_zone": distance,
            "distance_atr": (distance / atr_value) if atr_value and atr_value > 0 else None,
            "break_reason": zone.breakReason or None,
            "source": "zone_core.py (user-supplied rules)",
        })

    records.sort(key=lambda row: (row["distance_from_zone"], row["freshness_bars"]))
    return {
        "status": "AVAILABLE",
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "source": "DhanHQ historical candles + user-supplied zone_core.py",
        "source_timestamp": bars.index[-1].isoformat(),
        "received_at": pd.Timestamp.now(tz="Asia/Kolkata").isoformat(),
        "candle_count": int(len(bars)),
        "current_price": current_price,
        "active_zone_count": len(active),
        "total_detected_zone_count": len(zones),
        "eod_range": {
            "enabled": bool(params.get("useEodRange", True)),
            "high_buffer_pct": float(params.get("eodHighBufferPct", 10.0)),
            "low_buffer_pct": float(params.get("eodLowBufferPct", 10.0)),
            "semantics": "proximal must lie within day/session low × (1 − low buffer) and high × (1 + high buffer), as implemented in supplied zone_core.py",
        },
        "lookahead_warning": (
            "For intraday historical backtests, the supplied core groups full-day highs/lows; this can expose later same-day extremes to earlier bars. "
            "Parity was preserved; do not treat historical intraday output as a no-look-ahead backtest."
        ) if timeframe not in {"Daily", "Weekly", "Monthly"} and params.get("useEodRange", True) else None,
        "zones": records,
        "validation_module": "NOT_REQUIRED_BY_USER",
    }


def _atr_value(bars: pd.DataFrame, period: int) -> float | None:
    if len(bars) < period:
        return None
    prev_close = bars["close"].shift(1)
    tr = pd.concat([
        bars["high"] - bars["low"],
        (bars["high"] - prev_close).abs(),
        (bars["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    values = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().dropna()
    if values.empty:
        return None
    result = float(values.iloc[-1])
    return result if np.isfinite(result) else None


def _finite_or_none(value: Any) -> float | None:
    try:
        number = float(value)
        return number if np.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return pd.Timestamp(value).isoformat()
    except Exception:
        return None
