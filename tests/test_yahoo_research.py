from __future__ import annotations

import pandas as pd
import pytest

from terminal.providers.base import ProviderError
from terminal.providers.yahoo_research import YahooResearchProvider


def _daily_download(tickers: list[str]) -> pd.DataFrame:
    index = pd.DatetimeIndex(["2026-10-12", "2026-10-13"])
    fields = ["Open", "High", "Low", "Close", "Volume"]
    columns = pd.MultiIndex.from_product([tickers, fields])
    values = []
    for row in range(2):
        values.append([
            value
            for _ticker in tickers
            for value in (
                [100.0, 101.0, 99.0, 100.0, 1000.0]
                if row == 0 else [104.0, 106.0, 103.0, 105.0, 1250.0]
            )
        ])
    return pd.DataFrame(values, index=index, columns=columns)


def test_yahoo_research_normalizes_daily_cash_bars_and_labels_them_unverified():
    provider = YahooResearchProvider(enabled=True, downloader=_daily_download)
    result = provider.fetch_spot_snapshots(["RELIANCE"])["RELIANCE"]

    assert result["spot_ltp"] == 105.0
    assert result["spot_change"] == 5.0
    assert result["spot_change_pct"] == 5.0
    assert result["daily_spot_volume"] == 1250.0
    assert result["spot_bar_date"] == "2026-10-13"
    assert result["spot_timestamp"] is None
    assert result["spot_freshness"] == "UNKNOWN"
    assert result["market_data_mode"] == "RESEARCH_ONLY"
    assert result["futures_ltp"] is None
    assert result["final_ai_bias"] == "NO TRADE"


def test_yahoo_research_scan_never_labels_cash_price_as_futures():
    provider = YahooResearchProvider(enabled=True, downloader=_daily_download)
    scan = provider.build_futures_scan([
        {"symbol": "RELIANCE", "instrument_type": "FUTSTK", "security_id": "example"}
    ])

    assert scan["status"] == "PARTIAL"
    row = scan["data"][0]
    assert row["spot_ltp"] == 105.0
    assert row["futures_ltp"] is None
    assert row["futures_oi"] is None
    assert row["fno_ban_status"] == "UNAVAILABLE"
    assert row["risk_status"] == "ENTRY_BLOCKED_RESEARCH_ONLY_CASH_DATA"
    assert row["final_ai_bias"] == "NO TRADE"
    assert scan["market_data_mode"] == "RESEARCH_ONLY"


def test_yahoo_research_is_opt_in_and_disabled_by_default_argument():
    provider = YahooResearchProvider(enabled=False, downloader=_daily_download)
    assert provider.status()["status"] == "DISABLED"
    with pytest.raises(ProviderError) as exc:
        provider.fetch_spot_snapshots(["RELIANCE"])
    assert exc.value.code == "YAHOO_RESEARCH_DISABLED"


def test_yahoo_research_accepts_single_ticker_flat_frame():
    def flat_download(tickers: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            {"Close": [50.0, 55.0], "Volume": [500.0, 650.0]},
            index=pd.DatetimeIndex(["2026-10-12", "2026-10-13"]),
        )

    provider = YahooResearchProvider(enabled=True, downloader=flat_download)
    result = provider.fetch_spot_snapshots(["TCS"])["TCS"]
    assert result["spot_ltp"] == 55.0
    assert result["spot_change"] == 5.0
