"""Deterministic OHLCV indicators with explicit missing-data states."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from terminal.timeframes import is_supported_timeframe, resample_ohlcv


def compute_indicators(
    candles: pd.DataFrame,
    timeframe: str,
    ema_fast: int = 35,
    ema_slow: int = 100,
    atr_period: int = 14,
    volume_period: int = 20,
) -> dict[str, Any]:
    if not is_supported_timeframe(timeframe):
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    if ema_fast < 1 or ema_slow < 1 or atr_period < 1 or volume_period < 1:
        raise ValueError("Indicator lengths must be positive integers")
    bars = resample_ohlcv(candles, timeframe)
    if bars.empty:
        return {"status": "UNAVAILABLE", "timeframe": timeframe, "reason": "No candles"}

    close = bars["close"].astype(float)
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    volume = bars["volume"].astype(float)
    ema1 = close.ewm(span=ema_fast, adjust=False, min_periods=ema_fast).mean()
    ema2 = close.ewm(span=ema_slow, adjust=False, min_periods=ema_slow).mean()

    previous_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / atr_period, adjust=False, min_periods=atr_period).mean()
    volume_sma = volume.rolling(volume_period, min_periods=volume_period).mean()

    vwap_value: float | None = None
    vwap_signal = "?"
    vwap_method = "Session VWAP: cumulative typical-price × volume, reset each IST calendar session"
    if timeframe in {"Daily", "Weekly", "Monthly"}:
        vwap_method = "Session VWAP is not calculated on Daily/Weekly/Monthly bars; intraday session profile is required."
    else:
        session_key = pd.Index(bars.index.date)
        typical = (high + low + close) / 3.0
        pv = typical * volume
        cumulative_pv = pv.groupby(session_key).cumsum()
        cumulative_volume = volume.groupby(session_key).cumsum().replace(0, np.nan)
        session_vwap = cumulative_pv / cumulative_volume
        vwap_value = _last_finite(session_vwap)
        if vwap_value is not None:
            vwap_signal = "+" if float(close.iloc[-1]) > vwap_value else ("-" if float(close.iloc[-1]) < vwap_value else "0")

    price = float(close.iloc[-1])
    fast_last = _last_finite(ema1)
    slow_last = _last_finite(ema2)
    atr_last = _last_finite(atr)
    volume_sma_last = _last_finite(volume_sma)
    fast_previous = _last_finite(ema1.iloc[:-1]) if len(ema1) > 1 else None
    slow_previous = _last_finite(ema2.iloc[:-1]) if len(ema2) > 1 else None

    ema_fast_signal = "?" if fast_last is None else ("+" if price > fast_last else ("-" if price < fast_last else "0"))
    ema_slow_signal = "?" if slow_last is None else ("+" if price > slow_last else ("-" if price < slow_last else "0"))
    ema_cross = "?" if fast_last is None or slow_last is None else ("+" if fast_last > slow_last else ("-" if fast_last < slow_last else "0"))
    fast_slope = _slope_pct(fast_previous, fast_last)
    slow_slope = _slope_pct(slow_previous, slow_last)

    structure = _basic_structure(bars)
    return {
        "status": "AVAILABLE" if fast_last is not None and slow_last is not None else "PARTIAL",
        "timeframe": timeframe,
        "last_completed_or_available_candle": bars.index[-1].isoformat(),
        "last_close": price,
        "ema_fast_period": ema_fast,
        "ema_fast": fast_last,
        "ema_fast_signal": ema_fast_signal,
        "ema_fast_slope_pct": fast_slope,
        "ema_slow_period": ema_slow,
        "ema_slow": slow_last,
        "ema_slow_signal": ema_slow_signal,
        "ema_slow_slope_pct": slow_slope,
        "ema_cross_signal": ema_cross,
        "atr_period": atr_period,
        "atr": atr_last,
        "volume_sma_period": volume_period,
        "volume_sma": volume_sma_last,
        "vwap": vwap_value,
        "vwap_signal": vwap_signal,
        "vwap_methodology": vwap_method,
        "market_structure": structure,
        "source_candle_count": int(len(bars)),
        "limitations": [] if len(bars) >= ema_slow else [f"EMA {ema_slow} requires at least {ema_slow} completed candles"],
    }


def _last_finite(series: pd.Series) -> float | None:
    values = series.dropna()
    if values.empty:
        return None
    value = float(values.iloc[-1])
    return value if np.isfinite(value) else None


def _slope_pct(previous: float | None, latest: float | None) -> float | None:
    if previous is None or latest is None or previous == 0:
        return None
    return (latest / previous - 1.0) * 100.0


def _basic_structure(bars: pd.DataFrame, lookback: int = 20) -> str:
    if len(bars) < lookback + 1:
        return "INSUFFICIENT_BARS"
    prior = bars.iloc[-lookback - 1:-1]
    current = bars.iloc[-1]
    if current["close"] > prior["high"].max():
        return "CLOSE_ABOVE_PRIOR_20_BAR_RANGE"
    if current["close"] < prior["low"].min():
        return "CLOSE_BELOW_PRIOR_20_BAR_RANGE"
    return "WITHIN_PRIOR_20_BAR_RANGE"
