from __future__ import annotations

from api import main as api_main


class _DhanWithoutCredentials:
    credentials_ready = False


class _ResearchFallback:
    enabled = True

    def build_futures_scan(self, rows, refresh=False):
        return {
            "status": "PARTIAL",
            "data": [{**rows[0], "spot_ltp": 100.0, "futures_ltp": None,
                      "final_ai_bias": "NO TRADE"}],
            "source": "Yahoo Finance (Research Only)",
            "market_data_mode": "RESEARCH_ONLY",
            "received_at": "2026-10-13T15:00:00+05:30",
            "reason": "Cash research data only.",
        }


def test_futures_snapshot_uses_opted_in_cash_research_fallback(monkeypatch):
    monkeypatch.setattr(api_main, "provider", _DhanWithoutCredentials())
    monkeypatch.setattr(api_main, "yahoo_research_provider", _ResearchFallback())

    result = api_main._get_market_snapshot([{"symbol": "RELIANCE"}])

    assert result["source"] == "Yahoo Finance (Research Only)"
    assert result["market_data_mode"] == "RESEARCH_ONLY"
    assert result["dhan_fallback_code"] == "CREDENTIALS_MISSING"
    assert result["data"][0]["futures_ltp"] is None
    assert result["data"][0]["final_ai_bias"] == "NO TRADE"
