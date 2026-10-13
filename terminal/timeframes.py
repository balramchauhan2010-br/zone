"""Exchange-session aware candle resampling and volume-spike calculations.

Dhan's documented intraday candles are requested as one-minute bars for this
application. Custom scanner intervals are resampled locally; no partial candle
is compared with a completed-candle baseline by default.
"""
from __future__ import annotations

import calendar
import re
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
TIMEFRAMES: tuple[str, ...] = (
    "3m", "5m", "10m", "15m", "30m", "75m",
    "1h", "2h", "4h", "6h", "Daily", "Weekly", "Monthly",
)
_INTRADAY_MINUTES = {
    "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "75m": 75,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360,
}
_OHLCV = ("open", "high", "low", "close", "volume")


def _as_ist_index(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("Candle data must have a DatetimeIndex")
    out = df.copy()
    if out.index.tz is None:
        out.index = out.index.tz_localize(IST)
    else:
        out.index = out.index.tz_convert(IST)
    out = out.sort_index()
    out = out.loc[~out.index.duplicated(keep="last")]
    missing = [col for col in _OHLCV if col not in out.columns]
    if missing:
        raise ValueError(f"Missing candle columns: {', '.join(missing)}")
    for col in _OHLCV:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["open", "high", "low", "close"])
    out["volume"] = out["volume"].fillna(0.0)
    return out


def timeframe_minutes(timeframe: str) -> int | None:
    if timeframe in _INTRADAY_MINUTES:
        return _INTRADAY_MINUTES[timeframe]
    if not isinstance(timeframe, str):
        return None
    match = re.fullmatch(r"([1-9][0-9]{0,2})m", timeframe)
    if not match:
        return None
    minutes = int(match.group(1))
    return minutes if 1 <= minutes <= 390 else None


def is_supported_timeframe(timeframe: str) -> bool:
    return timeframe in TIMEFRAMES or timeframe_minutes(timeframe) is not None


def supported_timeframes(custom_timeframes: list[str] | tuple[str, ...] = ()) -> list[str]:
    return list(dict.fromkeys([*TIMEFRAMES, *(tf for tf in custom_timeframes if is_supported_timeframe(tf))]))


def resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample OHLCV and OI, anchoring intraday bars at the NSE 09:15 IST open."""
    if not is_supported_timeframe(timeframe):
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    source = _as_ist_index(df)
    if source.empty:
        return source

    interval = timeframe_minutes(timeframe)
    if interval is not None:
        rule = f"{interval}min"
        grouped = source.resample(
            rule, origin="start_day", offset="9h15min", label="left", closed="left"
        )
    elif timeframe == "Daily":
        grouped = source.resample("1D", label="left", closed="left")
    elif timeframe == "Weekly":
        grouped = source.resample("W-FRI", label="right", closed="right")
    else:
        grouped = source.resample("ME", label="right", closed="right")

    agg: dict[str, str] = {
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }
    if "oi" in source.columns:
        agg["oi"] = "last"
    bars = grouped.agg(agg)
    bars = bars.dropna(subset=["open", "high", "low", "close"])
    bars["volume"] = bars["volume"].fillna(0.0)
    return bars


def _period_end_for_label(label: pd.Timestamp, timeframe: str) -> datetime:
    label_dt = label.to_pydatetime()
    if label_dt.tzinfo is None:
        label_dt = label_dt.replace(tzinfo=IST)
    else:
        label_dt = label_dt.astimezone(IST)
    interval = timeframe_minutes(timeframe)
    if interval is not None:
        return label_dt + timedelta(minutes=interval)
    if timeframe == "Daily":
        return datetime.combine(label_dt.date(), time(15, 30), tzinfo=IST)
    if timeframe == "Weekly":
        return datetime.combine(label_dt.date(), time(15, 30), tzinfo=IST)
    return datetime.combine(label_dt.date(), time(15, 30), tzinfo=IST)


def _period_is_closed(label: pd.Timestamp, timeframe: str, now: datetime) -> bool:
    """Conservative candle-completion check; no exchange-holiday calendar assumed."""
    local_now = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
    end = _period_end_for_label(label, timeframe)
    if timeframe_minutes(timeframe) is not None or timeframe == "Daily":
        return end <= local_now
    if timeframe == "Weekly":
        # Weekly bars are labeled with their Friday close. A Friday bar is not
        # considered final until the NSE session close. Holiday-shortened weeks
        # are conservatively left incomplete without an exchange calendar.
        return end <= local_now
    if timeframe == "Monthly":
        return end <= local_now
    return False


def completed_bars(df: pd.DataFrame, timeframe: str, now: datetime | None = None) -> pd.DataFrame:
    """Return only bars whose close time has passed. Safe when feed contains a live bar."""
    bars = resample_ohlcv(df, timeframe)
    if bars.empty:
        return bars
    current = now or datetime.now(IST)
    mask = [_period_is_closed(pd.Timestamp(index), timeframe, current) for index in bars.index]
    return bars.loc[mask]


def _elapsed_fraction(label: pd.Timestamp, timeframe: str, now: datetime) -> float:
    local_now = now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)
    start = label.to_pydatetime()
    if start.tzinfo is None:
        start = start.replace(tzinfo=IST)
    else:
        start = start.astimezone(IST)
    interval = timeframe_minutes(timeframe)
    if interval is not None:
        total = interval * 60
        elapsed = max(0.0, min(total, (local_now - start).total_seconds()))
        return max(min(elapsed / total, 1.0), 1.0 / total)
    if timeframe == "Daily":
        open_dt = datetime.combine(start.date(), time(9, 15), tzinfo=IST)
        close_dt = datetime.combine(start.date(), time(15, 30), tzinfo=IST)
        total = (close_dt - open_dt).total_seconds()
        elapsed = max(0.0, min(total, (local_now - open_dt).total_seconds()))
        return max(min(elapsed / total, 1.0), 1.0 / total)
    # Weekly/monthly projected volume needs an exchange-session calendar. Do not
    # manufacture a projection from calendar-time fractions.
    return 0.0


def volume_spike(
    df: pd.DataFrame,
    timeframe: str,
    lookback: int = 30,
    threshold: float = 2.1,
    minimum_volume: float = 0.0,
    completed_only: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compare the chosen candle with the mean of the preceding comparable bars.

    In live mode, volume is projected by elapsed candle time only for intraday
    and daily intervals. Weekly/monthly projections are deliberately unavailable
    without an exchange session calendar.
    """
    if lookback < 1:
        raise ValueError("lookback must be at least 1")
    if not np.isfinite(threshold) or threshold <= 0:
        raise ValueError("threshold must be a positive finite number")
    if minimum_volume < 0:
        raise ValueError("minimum_volume cannot be negative")

    bars = resample_ohlcv(df, timeframe)
    current = now or datetime.now(IST)
    if bars.empty:
        return {"status": "UNAVAILABLE", "reason": "No candle data", "timeframe": timeframe}

    if completed_only:
        bars = completed_bars(df, timeframe, current)
        live_mode = False
    else:
        live_mode = True
    if len(bars) < lookback + 1:
        return {
            "status": "UNAVAILABLE",
            "reason": f"Need at least {lookback + 1} comparable candles; got {len(bars)}",
            "timeframe": timeframe,
            "lookback": lookback,
        }

    current_bar = bars.iloc[-1]
    baseline = bars["volume"].iloc[-(lookback + 1):-1]
    baseline_mean = float(baseline.mean()) if len(baseline) else 0.0
    actual_volume = float(current_bar["volume"])
    if baseline_mean <= 0:
        return {
            "status": "UNAVAILABLE",
            "reason": "Previous-candle baseline is zero or unavailable",
            "timeframe": timeframe,
            "lookback": lookback,
            "actual_volume": actual_volume,
            "baseline_volume": baseline_mean,
            "candle_timestamp": current_bar.name.isoformat(),
        }

    projected_volume: float | None = None
    elapsed_fraction: float | None = None
    if live_mode:
        elapsed_fraction = _elapsed_fraction(pd.Timestamp(current_bar.name), timeframe, current)
        if elapsed_fraction <= 0:
            return {
                "status": "UNAVAILABLE",
                "reason": "Live volume projection for weekly/monthly requires a verified exchange calendar",
                "timeframe": timeframe,
                "lookback": lookback,
                "actual_volume": actual_volume,
                "baseline_volume": baseline_mean,
                "candle_timestamp": current_bar.name.isoformat(),
            }
        projected_volume = actual_volume / elapsed_fraction
        ratio = projected_volume / baseline_mean
    else:
        ratio = actual_volume / baseline_mean

    status = "SPIKE" if actual_volume >= minimum_volume and ratio >= threshold else "NORMAL"
    return {
        "status": status,
        "timeframe": timeframe,
        "lookback": lookback,
        "threshold": float(threshold),
        "actual_volume": actual_volume,
        "baseline_volume": baseline_mean,
        "spike_ratio": float(ratio),
        "minimum_volume": float(minimum_volume),
        "candle_timestamp": current_bar.name.isoformat(),
        "completed_candle": not live_mode,
        "live_mode": live_mode,
        "elapsed_fraction": elapsed_fraction,
        "projected_volume": projected_volume,
        "baseline_candles": len(baseline),
    }
