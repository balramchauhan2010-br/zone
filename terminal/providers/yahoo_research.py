"""Opt-in Yahoo Finance research fallback for NSE cash-equity daily bars.

This is intentionally not a substitute for an authorized live F&O feed. Yahoo
bars may be delayed or incomplete, exact exchange timestamps are not exposed by
this adapter, and no F&O quote/OI/option-chain/delivery/ban fields are inferred.
"""
from __future__ import annotations

import importlib.util
import math
import threading
import time
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from terminal.config import YAHOO_RESEARCH_ENABLED
from terminal.providers.base import ProviderError

IST = ZoneInfo("Asia/Kolkata")


class YahooResearchProvider:
    name = "Yahoo Finance (Research Only)"
    cache_ttl_seconds = 300
    batch_size = 40

    def __init__(
        self,
        enabled: bool | None = None,
        downloader: Callable[[list[str]], pd.DataFrame] | None = None,
        cache_ttl_seconds: int = 300,
    ):
        self.enabled = YAHOO_RESEARCH_ENABLED if enabled is None else bool(enabled)
        self._downloader = downloader
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self._lock = threading.RLock()
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._last_success_at: datetime | None = None
        self._last_error_code: str | None = None
        self._last_latency_ms: float | None = None

    @property
    def package_available(self) -> bool:
        if self._downloader is not None:
            return True
        try:
            return importlib.util.find_spec("yfinance") is not None
        except (ImportError, ValueError):
            return False

    def status(self) -> dict[str, Any]:
        if not self.enabled:
            state = "DISABLED"
            note = "Set YAHOO_RESEARCH_ENABLED=true only for private, personal research use."
        elif not self.package_available:
            state = "PACKAGE_MISSING"
            note = "Install the yfinance dependency in the Streamlit app environment."
        elif self._last_error_code:
            state = "DEGRADED"
            note = f"Last Yahoo research request error code: {self._last_error_code}"
        elif self._last_success_at:
            state = "CONNECTED_RESEARCH_ONLY"
            note = "Daily NSE cash-equity bars were received; they are not verified live/F&O data."
        else:
            state = "CONFIGURED_NOT_CHECKED"
            note = "Opt-in research fallback is ready; it has not fetched data in this process."
        return {
            "provider": self.name,
            "status": state,
            "enabled": self.enabled,
            "package_available": self.package_available,
            "scope": "NSE cash-equity daily bars only",
            "data_mode": "RESEARCH_ONLY; delay/freshness not verified",
            "last_success_at": self._last_success_at.isoformat() if self._last_success_at else None,
            "last_error_code": self._last_error_code,
            "last_http_request_latency_ms": self._last_latency_ms,
            "note": note,
        }

    def _download(self, tickers: list[str]) -> pd.DataFrame:
        if self._downloader is not None:
            return self._downloader(tickers)
        if not self.package_available:
            raise ProviderError("The yfinance package is not installed", "YFINANCE_PACKAGE_MISSING")
        try:
            import yfinance as yf

            # Batch requests conservatively; this is a research fallback, not a
            # high-frequency or unrestricted bulk-feed integration.
            return yf.download(
                tickers=tickers,
                period="5d",
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                actions=False,
                progress=False,
                threads=4,
                timeout=20,
                multi_level_index=True,
            )
        except Exception as exc:
            raise ProviderError("Yahoo Finance research request failed", "YFINANCE_REQUEST_ERROR", retryable=True) from exc

    @staticmethod
    def _ticker_frame(downloaded: pd.DataFrame, ticker: str, ticker_count: int) -> pd.DataFrame:
        if not isinstance(downloaded, pd.DataFrame) or downloaded.empty:
            return pd.DataFrame()
        frame = downloaded
        if isinstance(frame.columns, pd.MultiIndex):
            found = False
            for level in range(frame.columns.nlevels):
                values = {str(item) for item in frame.columns.get_level_values(level)}
                if ticker in values:
                    frame = frame.xs(ticker, axis=1, level=level, drop_level=True)
                    found = True
                    break
            if not found:
                if ticker_count == 1:
                    frame = frame.copy()
                else:
                    return pd.DataFrame()
            if isinstance(frame.columns, pd.MultiIndex):
                # Provider versions have returned the remaining level in either
                # order; retain the last field-name component deterministically.
                frame.columns = [str(col[-1]) for col in frame.columns.to_list()]
        elif ticker_count != 1:
            return pd.DataFrame()
        frame = frame.copy()
        frame.columns = [str(col).strip().title() for col in frame.columns]
        if "Close" not in frame.columns:
            return pd.DataFrame()
        frame["Close"] = pd.to_numeric(frame["Close"], errors="coerce")
        frame = frame.dropna(subset=["Close"]).sort_index()
        return frame

    @staticmethod
    def _snapshot_from_frame(symbol: str, frame: pd.DataFrame, received_at: str) -> dict[str, Any] | None:
        if frame.empty:
            return None
        last = frame.iloc[-1]
        ltp = _finite_number(last.get("Close"))
        if ltp is None or ltp <= 0:
            return None
        previous_close = _finite_number(frame.iloc[-2].get("Close")) if len(frame) > 1 else None
        change = ltp - previous_close if previous_close is not None else None
        change_pct = (change / previous_close * 100.0) if change is not None and previous_close else None
        volume = _finite_number(last.get("Volume")) if "Volume" in frame.columns else None
        bar_date: str | None = None
        try:
            stamp = pd.Timestamp(frame.index[-1])
            if stamp.tzinfo is not None:
                stamp = stamp.tz_convert(IST)
            bar_date = stamp.date().isoformat()
        except (TypeError, ValueError, OverflowError):
            pass
        return {
            "symbol": symbol,
            "spot_ltp": ltp,
            "spot_change": change,
            "spot_change_pct": change_pct,
            "daily_spot_volume": volume,
            "spot_bar_date": bar_date,
            # Daily-bar timestamps do not identify a verified last-trade time.
            "spot_timestamp": None,
            "spot_freshness": "UNKNOWN",
            "spot_data_status": "RESEARCH_ONLY_UNVERIFIED",
            "spot_data_source": "Yahoo Finance via yfinance",
            "market_data_mode": "RESEARCH_ONLY",
            "received_at": received_at,
            "data_age_seconds": None,
            "freshness": "UNKNOWN",
            "futures_ltp": None,
            "futures_change": None,
            "futures_change_pct": None,
            "futures_basis": None,
            "basis_change": None,
            "daily_futures_volume": None,
            "selected_timeframe_volume": None,
            "average_volume": None,
            "volume_spike_ratio": None,
            "volume_signal": "NOT_CALCULATED",
            "futures_oi": None,
            "futures_oi_change": None,
            "futures_oi_change_pct": None,
            "oi_observation_status": "UNAVAILABLE",
            "futures_timestamp": None,
            "fno_ban_status": "UNAVAILABLE",
            "risk_status": "ENTRY_BLOCKED_RESEARCH_ONLY_CASH_DATA",
            "final_ai_bias": "NO TRADE",
            "signal_reason": "Yahoo daily cash-equity bars are research-only; verified Futures, OI, options and F&O-ban data are unavailable.",
            "source": "Yahoo Finance via yfinance (Research Only)",
            "data_quality_status": "RESEARCH_ONLY_UNVERIFIED",
            "data_quality_score": 0,
        }

    def fetch_spot_snapshots(self, symbols: list[str], refresh: bool = False) -> dict[str, dict[str, Any]]:
        if not self.enabled:
            raise ProviderError("Yahoo research fallback is disabled", "YAHOO_RESEARCH_DISABLED")
        if not self.package_available:
            raise ProviderError("The yfinance package is not installed", "YFINANCE_PACKAGE_MISSING")
        normalized = list(dict.fromkeys(
            str(symbol).strip().upper() for symbol in symbols
            if isinstance(symbol, str) and symbol.strip()
        ))
        if not normalized:
            return {}
        if len(normalized) > 400:
            raise ProviderError("Yahoo research request exceeds the 400-symbol safety cap", "YFINANCE_SYMBOL_LIMIT")

        with self._lock:
            now_mono = time.monotonic()
            output: dict[str, dict[str, Any]] = {}
            missing: list[str] = []
            for symbol in normalized:
                cached = self._cache.get(symbol)
                if not refresh and cached and now_mono - cached[0] <= self.cache_ttl_seconds:
                    output[symbol] = dict(cached[1])
                else:
                    missing.append(symbol)

            started = time.perf_counter()
            received_at = datetime.now(IST).isoformat()
            request_error: ProviderError | None = None
            for start in range(0, len(missing), self.batch_size):
                batch = missing[start:start + self.batch_size]
                tickers = [f"{symbol}.NS" for symbol in batch]
                try:
                    downloaded = self._download(tickers)
                except ProviderError as exc:
                    request_error = exc
                    continue
                except Exception as exc:
                    request_error = ProviderError("Yahoo Finance research request failed", "YFINANCE_REQUEST_ERROR", retryable=True)
                    request_error.__cause__ = exc
                    continue
                for symbol, ticker in zip(batch, tickers):
                    ticker_frame = self._ticker_frame(downloaded, ticker, len(batch))
                    snapshot = self._snapshot_from_frame(symbol, ticker_frame, received_at)
                    if snapshot:
                        self._cache[symbol] = (time.monotonic(), snapshot)
                        output[symbol] = dict(snapshot)

            self._last_latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
            if output:
                self._last_success_at = datetime.now(IST)
                self._last_error_code = None
            elif request_error:
                self._last_error_code = request_error.code
                raise request_error
            else:
                self._last_error_code = "NO_USABLE_DAILY_BARS"
            return output

    def build_futures_scan(self, contracts: list[dict[str, Any]], refresh: bool = False) -> dict[str, Any]:
        symbols = [str(item.get("symbol", "")).strip().upper() for item in contracts if item.get("symbol")]
        try:
            quotes = self.fetch_spot_snapshots(symbols, refresh=refresh)
        except ProviderError as exc:
            return {
                "status": "UNAVAILABLE",
                "data": _unavailable_rows(contracts, self.name, exc.code),
                "source": self.name,
                "received_at": datetime.now(IST).isoformat(),
                "reason": str(exc),
                "error_code": exc.code,
                "market_data_mode": "RESEARCH_ONLY",
            }
        output: list[dict[str, Any]] = []
        for contract in contracts:
            symbol = str(contract.get("symbol", "")).strip().upper()
            row = dict(contract)
            row.update(_unavailable_row_values(self.name, "YAHOO_CASH_ONLY"))
            quote = quotes.get(symbol)
            if quote:
                row.update(quote)
            else:
                row.update({
                    "spot_ltp": None,
                    "spot_change": None,
                    "spot_change_pct": None,
                    "daily_spot_volume": None,
                    "spot_bar_date": None,
                    "spot_data_status": "UNAVAILABLE",
                    "spot_data_source": self.name,
                    "source": self.name,
                    "market_data_mode": "RESEARCH_ONLY",
                })
            row["symbol"] = symbol
            row["futures_ltp"] = None
            row["futures_change"] = None
            row["futures_change_pct"] = None
            row["futures_oi"] = None
            row["futures_oi_change"] = None
            row["futures_oi_change_pct"] = None
            row["futures_timestamp"] = None
            row["fno_ban_status"] = "UNAVAILABLE"
            row["risk_status"] = "ENTRY_BLOCKED_RESEARCH_ONLY_CASH_DATA"
            row["final_ai_bias"] = "NO TRADE"
            output.append(row)
        available = sum(row.get("spot_ltp") is not None for row in output)
        return {
            "status": "PARTIAL" if available else "UNAVAILABLE",
            "data": output,
            "source": self.name,
            "received_at": datetime.now(IST).isoformat(),
            "source_timestamp": None,
            "reason": "Research-only NSE cash-equity daily bars; no Futures quote, OI, options PCR, delivery, verified ban status, or trade signal is substituted.",
            "market_data_mode": "RESEARCH_ONLY",
            "spot_rows_available": available,
            "total_contracts": len(output),
        }


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _unavailable_row_values(source: str, mode: str) -> dict[str, Any]:
    return {
        "spot_ltp": None,
        "spot_change": None,
        "spot_change_pct": None,
        "daily_spot_volume": None,
        "spot_bar_date": None,
        "spot_timestamp": None,
        "spot_freshness": "UNKNOWN",
        "spot_data_status": "UNAVAILABLE",
        "spot_data_source": source,
        "market_data_mode": mode,
        "futures_ltp": None,
        "futures_change": None,
        "futures_change_pct": None,
        "futures_oi": None,
        "futures_oi_change": None,
        "futures_oi_change_pct": None,
        "futures_timestamp": None,
        "fno_ban_status": "UNAVAILABLE",
        "risk_status": "ENTRY_BLOCKED_RESEARCH_ONLY_CASH_DATA",
        "final_ai_bias": "NO TRADE",
        "data_quality_status": "RESEARCH_ONLY_UNVERIFIED",
        "data_quality_score": 0,
        "oi_observation_status": "UNAVAILABLE",
        "volume_signal": "NOT_CALCULATED",
        "signal_reason": "No verified live Futures data source is configured; research bars never substitute for Futures or options data.",
        "source": source,
    }


def _unavailable_rows(contracts: list[dict[str, Any]], source: str, code: str) -> list[dict[str, Any]]:
    result = []
    for contract in contracts:
        row = dict(contract)
        row.update(_unavailable_row_values(source, "RESEARCH_ONLY"))
        row["spot_data_error_code"] = code
        result.append(row)
    return result
