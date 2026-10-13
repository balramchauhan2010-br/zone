from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from terminal.timeframes import TIMEFRAMES, completed_bars, is_supported_timeframe, resample_ohlcv, timeframe_minutes, volume_spike

IST = ZoneInfo("Asia/Kolkata")


def minute_frame(count=12, start=None, volumes=None):
    start = start or datetime(2026, 10, 12, 9, 15, tzinfo=IST)
    index = pd.date_range(start=start, periods=count, freq="1min")
    vols = volumes or [1.0] * count
    return pd.DataFrame({
        "open": [100.0 + i for i in range(count)],
        "high": [101.0 + i for i in range(count)],
        "low": [99.0 + i for i in range(count)],
        "close": [100.5 + i for i in range(count)],
        "volume": vols,
    }, index=index)


def test_all_user_timeframes_are_supported():
    assert TIMEFRAMES == (
        "3m", "5m", "10m", "15m", "30m", "75m", "1h", "2h", "4h", "6h",
        "Daily", "Weekly", "Monthly",
    )


def test_custom_minute_intervals_are_supported_with_bounds():
    assert is_supported_timeframe("20m")
    assert timeframe_minutes("37m") == 37
    assert is_supported_timeframe("390m")
    assert not is_supported_timeframe("0m")
    assert not is_supported_timeframe("391m")
    bars = resample_ohlcv(minute_frame(40), "20m")
    assert len(bars) == 2
    assert bars.iloc[0]["volume"] == 20.0


def test_intraday_resampling_is_anchored_to_nse_open_and_aggregates_ohlcv():
    bars = resample_ohlcv(minute_frame(6), "3m")
    assert len(bars) == 2
    assert bars.index[0].strftime("%H:%M") == "09:15"
    assert bars.iloc[0]["open"] == 100.0
    assert bars.iloc[0]["high"] == 103.0
    assert bars.iloc[0]["low"] == 99.0
    assert bars.iloc[0]["close"] == 102.5
    assert bars.iloc[0]["volume"] == 3.0


def test_completed_candle_mode_drops_current_partial_bucket():
    frame = minute_frame(6)
    now = datetime(2026, 10, 12, 9, 20, tzinfo=IST)  # 09:18–09:21 bucket is partial
    bars = completed_bars(frame, "3m", now=now)
    assert len(bars) == 1
    assert bars.index[0].strftime("%H:%M") == "09:15"


def test_volume_spike_uses_previous_comparable_candles_not_including_current():
    volumes = [1.0] * 9 + [10.0] * 3
    frame = minute_frame(12, volumes=volumes)
    result = volume_spike(
        frame, "3m", lookback=3, threshold=2.1, completed_only=True,
        now=datetime(2026, 10, 12, 9, 30, tzinfo=IST),
    )
    assert result["status"] == "SPIKE"
    assert result["baseline_candles"] == 3
    assert result["baseline_volume"] == 3.0
    assert result["actual_volume"] == 30.0
    assert result["spike_ratio"] == 10.0
    assert result["completed_candle"] is True


def test_live_partial_projection_is_explicit_and_does_not_claim_completed_candle():
    frame = minute_frame(8, volumes=[1.0] * 6 + [3.0, 3.0])
    result = volume_spike(
        frame, "3m", lookback=1, threshold=2.1, completed_only=False,
        now=datetime(2026, 10, 12, 9, 23, tzinfo=IST),
    )
    assert result["status"] in {"SPIKE", "NORMAL"}
    assert result["completed_candle"] is False
    assert result["live_mode"] is True
    assert result["projected_volume"] is not None
