"""Python market-analysis handlers invoked in-process by the Streamlit terminal."""
from __future__ import annotations

import io
import logging
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote as url_quote
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi import FastAPI, File, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from terminal.config import API_ADMIN_TOKEN, DEFAULT_ANALYSIS_SETTINGS, GEMINI_API_KEY, GEMINI_MODEL, get_global_catalog
from terminal.providers.base import ProviderError
from terminal.providers.dhan import DhanProvider
from terminal.providers.yahoo_research import YahooResearchProvider
from terminal.services.candles import fetch_candles
from terminal.services.indicators import compute_indicators
from terminal.services.gemini import GeminiExplainer
from terminal.services.instruments import (
    build_nse_futures_universe,
    get_contract_for_symbol,
    get_instrument_master,
    master_summary,
)
from terminal.services.market import get_futures_snapshot, get_quote_for_contract
from terminal.services.options import normalize_option_chain
from terminal.services.signals import classify_price_oi, score_setup
from terminal.services.zones import scan_symbol_zones
from terminal.timeframes import TIMEFRAMES, is_supported_timeframe, volume_spike

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("terminal.api")
IST = ZoneInfo("Asia/Kolkata")

provider = DhanProvider()
yahoo_research_provider = YahooResearchProvider()
gemini = GeminiExplainer()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    provider.close()


app = FastAPI(
    title="AI Pro Institutional Trading Terminal API",
    version="2.0.0",
    description="Read-only, source-aware market data and zone-scanner API. No order execution endpoints are enabled.",
    lifespan=lifespan,
)

_OPTION_CACHE: dict[tuple[str, str], tuple[datetime, dict[str, Any]]] = {}
_OPTION_CACHE_LOCK = threading.Lock()
_JOB_LOCK = threading.Lock()
_ZONE_JOBS: dict[str, dict[str, Any]] = {}
_JOB_EXECUTOR = None


def _received_at() -> str:
    return datetime.now(IST).isoformat()


def _envelope(status: str, data: Any = None, *, source: str | None = None,
              source_timestamp: str | None = None, received_at: str | None = None,
              reason: str | None = None, **extra: Any) -> dict[str, Any]:
    stamp = received_at or _received_at()
    age: float | None = None
    if source_timestamp:
        try:
            parsed = pd.Timestamp(source_timestamp)
            if parsed.tzinfo is None:
                parsed = parsed.tz_localize(IST)
            else:
                parsed = parsed.tz_convert(IST)
            age = max(0.0, (pd.Timestamp.now(tz=IST) - parsed).total_seconds())
        except Exception:
            age = None
    result = {
        "status": status,
        "data": data,
        "source": source,
        "source_timestamp": source_timestamp,
        "received_at": stamp,
        "data_age_seconds": age,
        "reason": reason,
    }
    result.update(extra)
    return result


def _error_envelope(exc: Exception, *, data: Any = None, source: str | None = None) -> dict[str, Any]:
    if isinstance(exc, ProviderError):
        return _envelope("UNAVAILABLE", data, source=source, reason=str(exc), error_code=exc.code)
    logger.exception("Unhandled provider/service error")
    return _envelope("UNAVAILABLE", data, source=source, reason="Request could not be completed; check server logs.", error_code="INTERNAL_ERROR")


def _load_master(refresh: bool = False) -> tuple[pd.DataFrame, datetime]:
    return get_instrument_master(provider, refresh=refresh)


def _get_market_snapshot(rows: list[dict[str, Any]], refresh: bool = False) -> dict[str, Any]:
    """Use Dhan for F&O when verified; optionally fall back to Yahoo cash research bars.

    This fallback never labels cash-equity prices as Futures and never produces
    an entry signal when the required F&O fields are missing.
    """
    dhan_error: Exception | None = None
    if provider.credentials_ready:
        try:
            snapshot = get_futures_snapshot(provider, rows)
            if snapshot.get("status") != "UNAVAILABLE":
                snapshot["market_data_mode"] = "BROKER_MARKET_DATA"
                return snapshot
            dhan_error = ProviderError(
                snapshot.get("reason") or "Dhan returned no usable Futures/spot quote.",
                "DHAN_NO_USABLE_QUOTES",
                retryable=True,
            )
        except Exception as exc:
            dhan_error = exc
    else:
        dhan_error = ProviderError("Dhan market-data credentials are not configured", "CREDENTIALS_MISSING")

    if yahoo_research_provider.enabled:
        result = yahoo_research_provider.build_futures_scan(rows, refresh=refresh)
        result["dhan_fallback_code"] = (
            dhan_error.code if isinstance(dhan_error, ProviderError) else "DHAN_REQUEST_ERROR"
        )
        return result

    reason = (
        str(dhan_error) if isinstance(dhan_error, ProviderError)
        else "Dhan request failed; the research fallback is disabled."
    )
    unavailable = _attach_unavailable_quotes(rows, reason,
        dhan_error.code if isinstance(dhan_error, ProviderError) else "DHAN_REQUEST_ERROR")
    return {
        "status": "PARTIAL" if rows else "UNAVAILABLE",
        "data": unavailable,
        "source": provider.name,
        "received_at": _received_at(),
        "source_timestamp": None,
        "reason": reason,
        "market_data_mode": "UNAVAILABLE",
        "dhan_fallback_code": dhan_error.code if isinstance(dhan_error, ProviderError) else "DHAN_REQUEST_ERROR",
    }


def _universe(contract: str = "front", refresh: bool = False) -> tuple[list[dict[str, Any]], pd.DataFrame, datetime]:
    master, received = _load_master(refresh)
    rows = build_nse_futures_universe(
        master,
        contract=contract,
        exclude_test_instruments=bool(DEFAULT_ANALYSIS_SETTINGS["exclude_test_instruments"]),
    )
    return rows, master, received


def _symbol_contract(symbol: str, contract: str = "front") -> tuple[dict[str, Any], pd.DataFrame, datetime]:
    rows, master, received = _universe(contract=contract)
    row = get_contract_for_symbol(rows, symbol)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No current NSE single-stock Futures contract found for {symbol.upper()} in the latest Dhan master")
    return row, master, received


def _history_days_for_timeframe(timeframe: str, requested_days: int, lookback: int = 30) -> int:
    if timeframe == "Daily":
        return max(int(requested_days), 365, int(lookback * 1.7))
    if timeframe == "Weekly":
        return max(int(requested_days), 365 * 3, int(lookback * 8))
    if timeframe == "Monthly":
        return max(int(requested_days), 365 * 10, int(lookback * 45))
    return min(max(int(requested_days), 1), 89)


def _spot_instrument(contract: dict[str, Any]) -> dict[str, Any] | None:
    sid = contract.get("spot_security_id")
    if not sid:
        return None
    return {"security_id": str(sid), "exchange_segment": "NSE_EQ", "instrument": "EQUITY"}


def _attach_unavailable_quotes(rows: list[dict[str, Any]], reason: str, error_code: str | None = None) -> list[dict[str, Any]]:
    for row in rows:
        row.update({
            "spot_ltp": None, "spot_change": None, "spot_change_pct": None,
            "futures_ltp": None, "futures_change": None, "futures_change_pct": None,
            "futures_basis": None, "basis_change": None, "daily_futures_volume": None,
            "daily_spot_volume": None, "selected_timeframe_volume": None,
            "average_volume": None, "volume_spike_ratio": None,
            "volume_signal": "UNAVAILABLE", "futures_oi": None,
            "futures_oi_change": None, "futures_oi_change_pct": None,
            "oi_observation_status": "UNAVAILABLE", "oi_observed_at": None,
            "spot_timestamp": None, "futures_timestamp": None,
            "received_at": _received_at(), "data_age_seconds": None,
            "freshness": "UNAVAILABLE", "spot_freshness": "UNAVAILABLE",
            "source": provider.name, "data_quality_score": 0,
            "data_quality_status": "UNAVAILABLE", "fno_ban_status": "UNAVAILABLE",
            "risk_status": "ENTRY_BLOCKED_BAN_STATUS_UNVERIFIED",
            "final_ai_bias": "NO TRADE", "signal_reason": reason,
            "bullish_score": None, "bearish_score": None, "setup_quality": None,
            "quote_error_code": error_code,
            "tradingview_url": f"https://www.tradingview.com/chart/?symbol={url_quote('NSE:' + str(row.get('symbol', '')), safe='')}",
        })
    return rows


class GeminiEvidenceRequest(BaseModel):
    evidence: dict[str, Any] = Field(default_factory=dict)


class ZoneScanRequest(BaseModel):
    scope: Literal["selected", "universe"] = "selected"
    symbols: list[str] = Field(default_factory=list, max_length=500)
    contract: Literal["front", "next"] = "front"
    timeframe: str = "15m"
    history_days: int = Field(default=30, ge=1, le=89)
    eod_high_buffer_pct: float = Field(default=10.0, ge=0, le=100)
    eod_low_buffer_pct: float = Field(default=10.0, ge=0, le=100)
    use_eod_range: bool = True
    include_broken: bool = False
    account_capital: float = Field(default=25000.0, gt=0)
    risk_pct: float = Field(default=0.5, gt=0, le=10)
    target_rr: float = Field(default=5.0, gt=0, le=20)


@app.get("/api/health")
def health() -> dict[str, Any]:
    dhan_status = provider.status()
    yahoo_status = yahoo_research_provider.status()
    overall = "ok" if dhan_status["status"] == "CONNECTED" else "degraded"
    return _envelope(
        overall,
        {"api": "ok", "dhan": dhan_status, "yahoo_research": yahoo_status,
         "gemini": gemini.status(), "zone_engine": "connected", "timezone": "Asia/Kolkata"},
        source="terminal backend",
        reason=None if overall == "ok" else "API is available; one or more data providers are not verified connected.",
    )


@app.get("/api/market/summary")
def market_summary() -> dict[str, Any]:
    now = datetime.now(IST)
    within_clock = now.weekday() < 5 and (now.hour, now.minute) >= (9, 15) and (now.hour, now.minute) < (15, 30)
    session = "TRADING_HOURS_BY_CLOCK; EXCHANGE_HOLIDAY_NOT_CHECKED" if within_clock else "CLOSED_BY_CLOCK_OR_NON_TRADING_DAY"
    global_items = get_global_catalog()
    return _envelope(
        "PARTIAL",
        {"nse_market_status": session, "market_status_source": "local clock only; official holiday/status source not connected",
         "ist_now": now.isoformat(), "global_market_assets": global_items,
         "indices": [], "fii_dii": "UNAVAILABLE"},
        source="local clock + configured catalog",
        reason="Official index/global feeds and exchange holiday calendar are not configured; no prices are fabricated.",
    )


@app.get("/api/instruments")
def instruments(contract: Literal["front", "next", "all"] = "front", refresh: bool = False) -> dict[str, Any]:
    try:
        rows, master, loaded_at = _universe(contract, refresh)
        return _envelope("AVAILABLE", rows, source="DhanHQ detailed instrument master",
                         received_at=loaded_at.isoformat(), contract_selection=contract,
                         universe_scope="NSE single-stock Futures (FUTSTK)",
                         count=len(rows), master=master_summary(master, loaded_at),
                         ban_status="UNAVAILABLE_FROM_CONFIGURED_PROVIDER")
    except Exception as exc:
        return _error_envelope(exc, data=[], source="DhanHQ detailed instrument master")


@app.get("/api/instruments/live")
def live_instruments(contract: Literal["front", "next"] = "front", refresh: bool = False) -> dict[str, Any]:
    try:
        rows, _, loaded_at = _universe(contract, refresh)
        snapshot = _get_market_snapshot(rows, refresh=refresh)
        return _envelope(snapshot["status"], snapshot["data"], source=snapshot["source"],
                         source_timestamp=snapshot.get("source_timestamp"),
                         received_at=snapshot.get("received_at"), reason=snapshot.get("reason"),
                         provider_latency_ms=snapshot.get("provider_latency_ms_total"),
                         count=len(snapshot["data"]), contract_selection=contract,
                         market_data_mode=snapshot.get("market_data_mode"),
                         master_received_at=loaded_at.isoformat(),
                         dhan_fallback_code=snapshot.get("dhan_fallback_code"))
    except Exception as exc:
        return _error_envelope(exc, data=[], source=provider.name)


@app.get("/api/futures")
def futures(contract: Literal["front", "next", "all"] = "front", refresh: bool = False) -> dict[str, Any]:
    try:
        rows, _, loaded_at = _universe(contract, refresh)
        if contract == "all" and len(rows) > 800:
            return _envelope("PARTIAL", rows, source="DhanHQ instrument master",
                             received_at=loaded_at.isoformat(), count=len(rows),
                             reason="Contract list returned without all-contract quotes to avoid oversized requests; select front/next for quotes.")
        snapshot = _get_market_snapshot(rows, refresh=refresh)
        return _envelope(snapshot["status"], snapshot["data"], source=snapshot["source"],
                         source_timestamp=snapshot.get("source_timestamp"),
                         received_at=snapshot.get("received_at"), reason=snapshot.get("reason"),
                         provider_latency_ms=snapshot.get("provider_latency_ms_total"),
                         count=len(snapshot["data"]), contract_selection=contract,
                         master_received_at=loaded_at.isoformat(),
                         market_data_mode=snapshot.get("market_data_mode"),
                         spot_rows_available=snapshot.get("spot_rows_available"),
                         dhan_fallback_code=snapshot.get("dhan_fallback_code"))
    except Exception as exc:
        return _error_envelope(exc, data=[], source=provider.name)


@app.get("/api/futures/ban-status")
def futures_ban_status(symbol: str | None = None) -> dict[str, Any]:
    try:
        rows, _, loaded_at = _universe("front")
        if symbol:
            rows = [row for row in rows if row["symbol"].upper() == symbol.upper()]
        items = [{"symbol": row["symbol"], "fno_ban_status": "UNAVAILABLE",
                  "source": None, "source_timestamp": None,
                  "reason": "The configured Dhan instrument master does not provide current NSE F&O ban status."} for row in rows]
        return _envelope("UNAVAILABLE", items, source=None, received_at=_received_at(),
                         reason="No verified official NSE ban-status provider is configured; new-entry signals remain blocked.",
                         count=len(items), master_received_at=loaded_at.isoformat())
    except Exception as exc:
        return _error_envelope(exc, data=[], source=None)


@app.get("/api/futures/{symbol}")
def future_detail(symbol: str, contract: Literal["front", "next"] = "front") -> dict[str, Any]:
    try:
        row, _, loaded_at = _symbol_contract(symbol, contract)
        snapshot = _get_market_snapshot([row], refresh=False)
        data = snapshot.get("data") or []
        result = data[0] if data else None
        return _envelope(snapshot["status"], result, source=snapshot["source"],
                         source_timestamp=(result or {}).get("futures_timestamp"),
                         received_at=snapshot.get("received_at"), reason=snapshot.get("reason"),
                         market_data_mode=snapshot.get("market_data_mode"),
                         master_received_at=loaded_at.isoformat(),
                         dhan_fallback_code=snapshot.get("dhan_fallback_code"))
    except HTTPException:
        raise
    except Exception as exc:
        return _error_envelope(exc, data=None, source=provider.name)


@app.get("/api/options/{symbol}")
def options_expiries(symbol: str) -> dict[str, Any]:
    try:
        row, _, _ = _symbol_contract(symbol, "front")
        spot = _spot_instrument(row)
        if not spot:
            return _envelope("UNAVAILABLE", [], reason="No NSE cash-underlying security ID was found in the provider master.")
        if not provider.credentials_ready:
            return _envelope("UNAVAILABLE", [], reason="Dhan Data API credentials are not configured.", error_code="CREDENTIALS_MISSING")
        expiries = provider.get_option_expiries(spot["security_id"], "NSE_EQ")
        today = datetime.now(IST).date().isoformat()
        valid = [expiry for expiry in expiries if expiry >= today]
        return _envelope("AVAILABLE" if valid else "UNAVAILABLE", valid, source=provider.name,
                         reason=None if valid else "No active option expiries were returned.", symbol=symbol.upper(),
                         default_expiry=valid[0] if valid else None)
    except HTTPException:
        raise
    except Exception as exc:
        return _error_envelope(exc, data=[], source=provider.name)


@app.get("/api/options/{symbol}/chain")
def option_chain(symbol: str, expiry: str | None = None,
                 pcr_scope: Literal["all_strikes_selected_expiry", "selected_atm_5_each_side"] = "selected_atm_5_each_side") -> dict[str, Any]:
    try:
        row, _, _ = _symbol_contract(symbol, "front")
        spot = _spot_instrument(row)
        if not spot:
            return _envelope("UNAVAILABLE", None, reason="No mapped NSE cash-underlying security ID.")
        if not provider.credentials_ready:
            return _envelope("UNAVAILABLE", None, reason="Dhan Data API credentials are not configured.", error_code="CREDENTIALS_MISSING")
        if not expiry:
            expiries = provider.get_option_expiries(spot["security_id"], "NSE_EQ")
            today = datetime.now(IST).date().isoformat()
            expiry = next((item for item in expiries if item >= today), None)
        if not expiry:
            return _envelope("UNAVAILABLE", None, reason="No current listed option expiry returned by Dhan.")
        cache_key = (symbol.upper(), expiry)
        with _OPTION_CACHE_LOCK:
            cached = _OPTION_CACHE.get(cache_key)
            if cached and (datetime.now(IST) - cached[0]).total_seconds() <= 180:
                result = normalize_option_chain(cached[1], symbol, expiry, pcr_scope)
                result["cache_hit"] = True
                return _envelope(result["status"], result, source=provider.name,
                                 source_timestamp=None, received_at=cached[0].isoformat())
        response = provider.get_option_chain(spot["security_id"], expiry, "NSE_EQ")
        with _OPTION_CACHE_LOCK:
            _OPTION_CACHE[cache_key] = (datetime.now(IST), response)
        result = normalize_option_chain(response, symbol, expiry, pcr_scope)
        return _envelope(result["status"], result, source=provider.name,
                         received_at=_received_at(), expiry=expiry)
    except HTTPException:
        raise
    except Exception as exc:
        return _error_envelope(exc, data=None, source=provider.name)


@app.get("/api/oi/{symbol}")
def oi_detail(symbol: str) -> dict[str, Any]:
    try:
        row, _, _ = _symbol_contract(symbol, "front")
        if not provider.credentials_ready:
            return _envelope("UNAVAILABLE", None, source=provider.name,
                             reason="Dhan Data API credentials are not configured.", error_code="CREDENTIALS_MISSING")
        snapshot = get_quote_for_contract(provider, row)
        if snapshot.get("futures_oi") is None:
            return _envelope("UNAVAILABLE", None, source=provider.name,
                             reason="No OI value was returned for this Futures contract.")
        return _envelope("AVAILABLE", {
            "symbol": symbol.upper(), "contract_expiry": row.get("expiry"),
            "futures_oi": snapshot.get("futures_oi"),
            "oi_change": snapshot.get("futures_oi_change"),
            "oi_change_pct": snapshot.get("futures_oi_change_pct"),
            "observation_interval_seconds": snapshot.get("oi_observation_interval_seconds"),
            "observation_status": snapshot.get("oi_observation_status"),
            "observed_at": snapshot.get("oi_observed_at"),
            "source_timestamp": snapshot.get("futures_timestamp"),
            "note": "Change is a delta between snapshots collected by this running process; first observation has no baseline.",
        }, source=provider.name, source_timestamp=snapshot.get("futures_timestamp"), received_at=snapshot.get("received_at"))
    except HTTPException:
        raise
    except Exception as exc:
        return _error_envelope(exc, data=None, source=provider.name)


@app.get("/api/delivery/{symbol}")
def delivery_data(symbol: str) -> dict[str, Any]:
    return _envelope("UNAVAILABLE", None, source=None,
                     reason="No verified delivery-percentage data source is configured. Dhan quote APIs do not provide the required final delivery history in this adapter.",
                     symbol=symbol.upper(), publication_status="UNAVAILABLE")


@app.get("/api/indicators/{symbol}")
def indicators(symbol: str, timeframe: str = "15m", instrument_type: Literal["futures", "spot"] = "futures",
               ema_fast: int = Query(35, ge=1, le=500), ema_slow: int = Query(100, ge=1, le=1000),
               refresh: bool = False) -> dict[str, Any]:
    if not is_supported_timeframe(timeframe):
        raise HTTPException(status_code=422, detail="timeframe must be a listed interval or a custom intraday Xm interval from 1 to 390 minutes")
    try:
        row, _, _ = _symbol_contract(symbol, "front")
        instrument = row if instrument_type == "futures" else _spot_instrument(row)
        if not instrument:
            return _envelope("UNAVAILABLE", None, source=provider.name, reason="Spot instrument mapping is missing.")
        instrument = dict(instrument)
        if instrument_type == "spot":
            instrument.update({"instrument": "EQUITY", "exchange_segment": "NSE_EQ"})
        if not provider.credentials_ready:
            return _envelope("UNAVAILABLE", None, source=provider.name,
                             reason="Dhan Data API credentials are not configured.", error_code="CREDENTIALS_MISSING")
        candles, meta = fetch_candles(provider, instrument, timeframe, include_oi=(instrument_type == "futures"), refresh=refresh)
        computed = compute_indicators(candles, timeframe, ema_fast=ema_fast, ema_slow=ema_slow)
        return _envelope(computed["status"], computed, source=provider.name,
                         source_timestamp=meta.get("source_timestamp"), received_at=meta.get("received_at"),
                         provider_latency_ms=meta.get("provider_request_latency_ms"),
                         instrument_type=instrument_type, cache_hit=meta.get("cache_hit"))
    except HTTPException:
        raise
    except Exception as exc:
        return _error_envelope(exc, data=None, source=provider.name)


@app.get("/api/volume/{symbol}")
def volume_details(symbol: str, timeframe: str = "15m",
                   lookback: int = Query(30, ge=1, le=200),
                   threshold: float = Query(2.1, gt=0, le=100),
                   minimum_volume: float = Query(0.0, ge=0),
                   completed_only: bool = True,
                   instrument_type: Literal["futures", "spot"] = "futures",
                   refresh: bool = False) -> dict[str, Any]:
    if not is_supported_timeframe(timeframe):
        raise HTTPException(status_code=422, detail="timeframe must be a listed interval or a custom intraday Xm interval from 1 to 390 minutes")
    try:
        row, _, _ = _symbol_contract(symbol, "front")
        instrument = row if instrument_type == "futures" else _spot_instrument(row)
        if not instrument:
            return _envelope("UNAVAILABLE", None, source=provider.name, reason="Spot instrument mapping is missing.")
        instrument = dict(instrument)
        if instrument_type == "spot":
            instrument.update({"instrument": "EQUITY", "exchange_segment": "NSE_EQ"})
        if not provider.credentials_ready:
            return _envelope("UNAVAILABLE", None, source=provider.name,
                             reason="Dhan Data API credentials are not configured.", error_code="CREDENTIALS_MISSING")
        history_days = _history_days_for_timeframe(timeframe, 89, lookback)
        candles, meta = fetch_candles(provider, instrument, timeframe, history_days=history_days,
                                      include_oi=False, refresh=refresh)
        result = volume_spike(candles, timeframe, lookback=lookback,
                              threshold=threshold, minimum_volume=minimum_volume,
                              completed_only=completed_only)
        result.update({"symbol": symbol.upper(), "instrument_type": instrument_type,
                       "source": provider.name, "source_timestamp": meta.get("source_timestamp"),
                       "received_at": meta.get("received_at"), "cache_hit": meta.get("cache_hit")})
        return _envelope(result.get("status", "UNAVAILABLE"), result,
                         source=provider.name, source_timestamp=meta.get("source_timestamp"),
                         received_at=meta.get("received_at"),
                         provider_latency_ms=meta.get("provider_request_latency_ms"),
                         cache_hit=meta.get("cache_hit"))
    except HTTPException:
        raise
    except Exception as exc:
        return _error_envelope(exc, data=None, source=provider.name)


@app.get("/api/zones")
def zone_results(job_id: str | None = None, symbol: str | None = None) -> dict[str, Any]:
    if job_id:
        job = _get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Zone scan job not found (jobs are in-memory and reset on API restart).")
        items = job.get("results", [])
    else:
        with _JOB_LOCK:
            jobs = sorted(_ZONE_JOBS.values(), key=lambda item: item.get("created_at", ""), reverse=True)
        completed = next((j for j in jobs if j.get("status") in {"COMPLETE", "PARTIAL", "FAILED"}), None)
        items = completed.get("results", []) if completed else []
        job = completed
    if symbol:
        items = [item for item in items if item.get("symbol", "").upper() == symbol.upper()]
    status = job.get("status", "UNAVAILABLE") if job else "UNAVAILABLE"
    return _envelope(status, items, source="DhanHQ candles + supplied zone_core.py",
                     reason=None if items else "No completed scan results are available. Start a Zone scan from the UI.",
                     job_id=job.get("job_id") if job else None,
                     progress=job.get("progress") if job else None,
                     count=len(items))


def _get_job(job_id: str) -> dict[str, Any] | None:
    with _JOB_LOCK:
        job = _ZONE_JOBS.get(job_id)
        return dict(job) if job else None


def _run_zone_job(job_id: str, request: ZoneScanRequest, symbols: list[str]) -> None:
    with _JOB_LOCK:
        _ZONE_JOBS[job_id]["status"] = "RUNNING"
        _ZONE_JOBS[job_id]["started_at"] = _received_at()
        _ZONE_JOBS[job_id]["total"] = len(symbols)
    try:
        universe_rows, _, _ = _universe(request.contract)
        by_symbol = {row["symbol"].upper(): row for row in universe_rows}
        results: list[dict[str, Any]] = []
        for index, symbol in enumerate(symbols, start=1):
            contract = by_symbol.get(symbol.upper())
            if contract is None:
                result = {"status": "MAPPING_ERROR", "symbol": symbol.upper(), "timeframe": request.timeframe,
                          "zones": [], "reason": "Symbol is not in the selected current Dhan FUTSTK universe."}
            elif not provider.credentials_ready:
                result = {"status": "UNAVAILABLE", "symbol": symbol.upper(), "timeframe": request.timeframe,
                          "zones": [], "reason": "Dhan Data API credentials are not configured."}
            else:
                instrument = dict(contract)
                history_days = _history_days_for_timeframe(request.timeframe, request.history_days)
                candles, meta = fetch_candles(provider, instrument, request.timeframe,
                                             history_days=history_days,
                                             include_oi=False, refresh=False)
                settings = {
                    "eod_high_buffer_pct": request.eod_high_buffer_pct,
                    "eod_low_buffer_pct": request.eod_low_buffer_pct,
                    "use_eod_range": request.use_eod_range,
                    "account_capital": request.account_capital,
                    "risk_pct": request.risk_pct,
                    "target_rr": request.target_rr,
                }
                result = scan_symbol_zones(candles, symbol, request.timeframe, settings,
                                           include_broken=request.include_broken,
                                           lot_size=contract.get("lot_size"))
                result["data_received_at"] = meta.get("received_at")
                result["contract_expiry"] = contract.get("expiry")
            results.append(result)
            with _JOB_LOCK:
                job = _ZONE_JOBS.get(job_id)
                if job is None:
                    return
                job["results"] = results
                job["completed"] = index
                job["progress"] = round(index / max(len(symbols), 1) * 100, 1)
                job["updated_at"] = _received_at()
        with _JOB_LOCK:
            job = _ZONE_JOBS[job_id]
            successes = sum(item.get("status") == "AVAILABLE" for item in results)
            job["status"] = "COMPLETE" if successes == len(results) else ("PARTIAL" if successes else "FAILED")
            job["finished_at"] = _received_at()
            job["available_symbols"] = successes
    except Exception as exc:
        logger.exception("Zone scan job failed")
        with _JOB_LOCK:
            if job_id in _ZONE_JOBS:
                _ZONE_JOBS[job_id]["status"] = "FAILED"
                _ZONE_JOBS[job_id]["reason"] = str(exc) if isinstance(exc, ProviderError) else "Scan failed; inspect API logs."
                _ZONE_JOBS[job_id]["finished_at"] = _received_at()


@app.post("/api/zones/scan")
def start_zone_scan(request: ZoneScanRequest) -> dict[str, Any]:
    if not is_supported_timeframe(request.timeframe):
        raise HTTPException(status_code=422, detail="timeframe must be a listed interval or a custom intraday Xm interval from 1 to 390 minutes")
    try:
        all_rows, _, _ = _universe(request.contract)
        universe_symbols = [row["symbol"] for row in all_rows]
        if request.scope == "universe":
            symbols = universe_symbols
        else:
            symbols = list(dict.fromkeys(s.strip().upper() for s in request.symbols if s.strip()))
            if not symbols:
                raise HTTPException(status_code=422, detail="Select at least one symbol, or set scope='universe'.")
            unknown = [symbol for symbol in symbols if symbol not in set(universe_symbols)]
            if unknown:
                raise HTTPException(status_code=422, detail=f"Not in current provider universe: {', '.join(unknown[:10])}")
        if not symbols:
            return _envelope("UNAVAILABLE", None, source="DhanHQ instrument master", reason="No eligible active NSE FUTSTK instruments were returned.")
        if len(symbols) > 500:
            raise HTTPException(status_code=413, detail="Scan size exceeds 500 instruments; use smaller batches.")
        if not provider.credentials_ready:
            return _envelope("UNAVAILABLE", None, source=provider.name,
                             reason="Add DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN to Streamlit Secrets before scanning candles.",
                             error_code="CREDENTIALS_MISSING")
        job_id = str(uuid.uuid4())
        job = {
            "job_id": job_id, "status": "QUEUED", "scope": request.scope,
            "contract": request.contract, "timeframe": request.timeframe,
            "symbols": symbols, "total": len(symbols), "completed": 0, "progress": 0.0,
            "results": [], "created_at": _received_at(), "updated_at": _received_at(),
            "rate_limit_note": "Provider requests are serialized and throttled; a full-universe scan can take several minutes.",
        }
        with _JOB_LOCK:
            _ZONE_JOBS[job_id] = job
            # Bound in-memory job retention.
            if len(_ZONE_JOBS) > 20:
                oldest = sorted(_ZONE_JOBS.values(), key=lambda item: item.get("created_at", ""))[:5]
                for old in oldest:
                    _ZONE_JOBS.pop(old.get("job_id"), None)
        global _JOB_EXECUTOR
        if _JOB_EXECUTOR is None:
            from concurrent.futures import ThreadPoolExecutor
            _JOB_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="zone-scan")
        _JOB_EXECUTOR.submit(_run_zone_job, job_id, request, symbols)
        return _envelope("QUEUED", {"job_id": job_id, "total": len(symbols), "symbols": symbols,
                                     "timeframe": request.timeframe},
                         source=provider.name, reason=None,
                         note=job["rate_limit_note"])
    except HTTPException:
        raise
    except Exception as exc:
        return _error_envelope(exc, data=None, source=provider.name)


@app.get("/api/zones/jobs/{job_id}")
def zone_job_status(job_id: str) -> dict[str, Any]:
    job = _get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Zone scan job not found (jobs are in-memory and reset on API restart).")
    return _envelope(job["status"], job, source="zone scan worker", received_at=job.get("updated_at"),
                     progress=job.get("progress"), reason=job.get("reason"))


@app.get("/api/zones/{symbol}")
def zones_for_symbol(symbol: str, timeframe: str = "15m", contract: Literal["front", "next"] = "front",
                     history_days: int = Query(30, ge=1, le=89),
                     eod_high_buffer_pct: float = Query(10.0, ge=0, le=100),
                     eod_low_buffer_pct: float = Query(10.0, ge=0, le=100),
                     use_eod_range: bool = True, include_broken: bool = False,
                     account_capital: float = Query(25000.0, gt=0),
                     risk_pct: float = Query(0.5, gt=0, le=10),
                     target_rr: float = Query(5.0, gt=0, le=20),
                     refresh: bool = False) -> dict[str, Any]:
    if not is_supported_timeframe(timeframe):
        raise HTTPException(status_code=422, detail="timeframe must be a listed interval or a custom intraday Xm interval from 1 to 390 minutes")
    try:
        row, _, _ = _symbol_contract(symbol, contract)
        if not provider.credentials_ready:
            return _envelope("UNAVAILABLE", None, source=provider.name,
                             reason="Dhan Data API credentials are not configured.", error_code="CREDENTIALS_MISSING")
        candle_history_days = _history_days_for_timeframe(timeframe, history_days)
        candles, meta = fetch_candles(provider, row, timeframe, history_days=candle_history_days,
                                      include_oi=False, refresh=refresh)
        result = scan_symbol_zones(candles, symbol, timeframe,
                                   {"eod_high_buffer_pct": eod_high_buffer_pct,
                                    "eod_low_buffer_pct": eod_low_buffer_pct,
                                    "use_eod_range": use_eod_range,
                                    "account_capital": account_capital,
                                    "risk_pct": risk_pct,
                                    "target_rr": target_rr},
                                   include_broken=include_broken,
                                   lot_size=row.get("lot_size"))
        return _envelope(result["status"], result, source=provider.name,
                         source_timestamp=meta.get("source_timestamp"), received_at=meta.get("received_at"),
                         cache_hit=meta.get("cache_hit"))
    except HTTPException:
        raise
    except Exception as exc:
        return _error_envelope(exc, data=None, source=provider.name)


@app.get("/api/signals")
def signals(symbol: str | None = None) -> dict[str, Any]:
    try:
        rows, _, _ = _universe("front")
        if symbol:
            rows = [row for row in rows if row["symbol"].upper() == symbol.upper()]
        snapshot = _get_market_snapshot(rows)
        items = snapshot.get("data") or []
        research_only = snapshot.get("market_data_mode") == "RESEARCH_ONLY"
        for item in items:
            if research_only:
                item.update({
                    "final_ai_bias": "NO TRADE",
                    "risk_status": "ENTRY_BLOCKED_RESEARCH_ONLY_CASH_DATA",
                    "signal_reason": "Yahoo research-mode cash bars cannot validate a Futures setup, OI, option PCR, delivery, or official F&O-ban status.",
                })
                continue
            evaluation = score_setup(
                {}, eligibility_status=item.get("eligibility_status", "UNKNOWN"),
                fno_ban_status=item.get("fno_ban_status", "UNAVAILABLE"),
            )
            item.update(evaluation)
        return _envelope("PARTIAL", items, source=snapshot.get("source"),
                         reason=(snapshot.get("reason") or "Verified F&O ban status and full signal evidence are unavailable; new entries remain blocked."),
                         count=len(items), market_data_mode=snapshot.get("market_data_mode"))
    except Exception as exc:
        return _error_envelope(exc, data=[], source=provider.name)


@app.get("/api/signals/top-buy")
def top_buy() -> dict[str, Any]:
    return _top_signals("STRONG BUY")


@app.get("/api/signals/top-sell")
def top_sell() -> dict[str, Any]:
    return _top_signals("STRONG SELL")


def _top_signals(target: str) -> dict[str, Any]:
    response = signals(None)
    if response.get("status") == "UNAVAILABLE":
        return _envelope("UNAVAILABLE", [], reason=response.get("reason"))
    items = [item for item in (response.get("data") or []) if item.get("final_bias") == target]
    return _envelope("AVAILABLE" if items else "NO_QUALIFIED_SETUPS", items,
                     source=provider.name, count=len(items),
                     reason=None if items else "No setup passed all source, data-quality, risk, entry-confirmation and F&O-ban filters.")


@app.post("/api/ai/explain")
def explain_evidence(request: GeminiEvidenceRequest,
                    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token")) -> dict[str, Any]:
    if not API_ADMIN_TOKEN:
        return _envelope("UNAVAILABLE", None, source="Google Gemini",
                         reason="APP_ADMIN_TOKEN is not configured; Gemini endpoint is fail-closed to avoid a public billable endpoint.")
    import hmac
    if not x_admin_token or not hmac.compare_digest(x_admin_token, API_ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid or missing admin token")
    if not GEMINI_API_KEY:
        return _envelope("UNAVAILABLE", None, source="Google Gemini",
                         reason="GEMINI_API_KEY is not configured; deterministic rules remain available.")
    try:
        result = gemini.explain(request.evidence)
        return _envelope("AVAILABLE", result, source="Google Gemini", received_at=_received_at(),
                         note="Explanation only; it does not set or modify final signal/risk filters.")
    except Exception as exc:
        return _error_envelope(exc, data=None, source="Google Gemini")


@app.get("/api/news")
def news(symbol: str | None = None) -> dict[str, Any]:
    return _envelope("UNAVAILABLE", [], source=None,
                     reason="No licensed/verified news feed is configured. No headlines or AI summaries are fabricated.",
                     symbol=symbol.upper() if symbol else None)


@app.get("/api/events")
def events(symbol: str | None = None) -> dict[str, Any]:
    return _envelope("UNAVAILABLE", [], source=None,
                     reason="No verified NSE/BSE corporate-action or macro-calendar provider is configured.",
                     symbol=symbol.upper() if symbol else None)


@app.get("/api/sectors")
def sectors() -> dict[str, Any]:
    return _envelope("UNAVAILABLE", [], source=None,
                     reason="No verified NSE sector-index feed is configured; relative strength is not inferred.")


@app.get("/api/fii-dii")
def fii_dii() -> dict[str, Any]:
    return _envelope("UNAVAILABLE", [], source=None,
                     reason="FII/FPI and DII data require a verified publication source; no live values are fabricated.")


@app.get("/api/global/markets")
def global_markets() -> dict[str, Any]:
    items = get_global_catalog()
    for item in items:
        item["data"] = None
        item["provider_timestamp"] = None
        item["received_at"] = _received_at()
        item["data_age_seconds"] = None
        if not item.get("provider_symbol"):
            item["status"] = "MAPPING_REVIEW" if item.get("status") == "MAPPING_REVIEW" else "UNMAPPED"
    return _envelope("UNAVAILABLE", items, source=None,
                     reason="A licensed global-market data provider and confirmed provider-symbol mappings have not been configured.",
                     count=len(items))


@app.get("/api/global/zones")
def global_zones(symbol: str | None = None, timeframe: str = "15m") -> dict[str, Any]:
    if not is_supported_timeframe(timeframe):
        raise HTTPException(status_code=422, detail="timeframe must be a listed interval or a custom intraday Xm interval from 1 to 390 minutes")
    items = get_global_catalog()
    if symbol:
        items = [item for item in items if item.get("id", "").upper() == symbol.upper()]
    return _envelope("UNAVAILABLE", [], source=None,
                     reason="Global OHLCV source/mapping is not configured. The supplied zone_core is not run on synthetic data.",
                     timeframe=timeframe, unresolved_instruments=items)


@app.post("/api/global/zones/upload")
async def global_zone_upload(
    symbol: str = Query(..., min_length=1, max_length=40),
    timeframe: str = "15m",
    eod_high_buffer_pct: float = Query(10.0, ge=0, le=100),
    eod_low_buffer_pct: float = Query(10.0, ge=0, le=100),
    use_eod_range: bool = True,
    account_capital: float = Query(25000.0, gt=0),
    risk_pct: float = Query(0.5, gt=0, le=10),
    target_rr: float = Query(5.0, gt=0, le=20),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    if not is_supported_timeframe(timeframe):
        raise HTTPException(status_code=422, detail="timeframe must be a listed interval or a custom intraday Xm interval from 1 to 390 minutes")
    catalog = {item.get("id", "").upper() for item in get_global_catalog()}
    if symbol.upper() not in catalog:
        raise HTTPException(status_code=422, detail="Symbol must be selected from the configured global-instrument catalog.")
    if file.filename and not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=415, detail="Upload a CSV file with timestamp, OHLC and volume columns.")
    raw = await file.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV exceeds the 5 MB upload limit.")
    try:
        frame = pd.read_csv(io.BytesIO(raw))
        frame.columns = [str(col).strip().lower() for col in frame.columns]
        time_col = next((col for col in ("timestamp", "datetime", "date_time", "date") if col in frame.columns), None)
        required = {"open", "high", "low", "close", "volume"}
        if time_col is None or not required.issubset(frame.columns):
            raise ValueError("CSV must contain timestamp/date and open, high, low, close, volume columns.")
        frame[time_col] = pd.to_datetime(frame[time_col], errors="coerce")
        frame = frame.dropna(subset=[time_col]).set_index(time_col).sort_index()
        for col in required:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.dropna(subset=["open", "high", "low", "close"])
        if frame.empty or len(frame) > 500000:
            raise ValueError("CSV must contain 1–500,000 rows with valid OHLC values.")
        scan = scan_symbol_zones(
            frame, symbol, timeframe,
            {"eod_high_buffer_pct": eod_high_buffer_pct,
             "eod_low_buffer_pct": eod_low_buffer_pct,
             "use_eod_range": use_eod_range,
             "account_capital": account_capital,
             "risk_pct": risk_pct,
             "target_rr": target_rr},
            include_broken=False, lot_size=None,
        )
        scan["source"] = "USER_UPLOADED_CSV"
        scan["source_timestamp"] = frame.index[-1].isoformat()
        scan["data_quality_status"] = "UNVERIFIED_USER_SUPPLIED_DATA"
        scan["limitations"] = [
            "Uploaded values are not independently verified or labeled live.",
            "Intraday bars use the application's 09:15 Asia/Kolkata resampling anchor; align CSV timestamps to the intended exchange session before scanning.",
        ] + ([scan["lookahead_warning"]] if scan.get("lookahead_warning") else [])
        return _envelope("USER_PROVIDED", scan, source="USER_UPLOADED_CSV",
                         source_timestamp=frame.index[-1].isoformat(), received_at=_received_at(),
                         row_count=len(frame), data_quality_status="UNVERIFIED_USER_SUPPLIED_DATA")
    except HTTPException:
        raise
    except Exception as exc:
        return _envelope("UNAVAILABLE", None, source="USER_UPLOADED_CSV",
                         reason=str(exc) if isinstance(exc, ValueError) else "CSV could not be parsed; verify its headers and timestamps.")


@app.get("/api/providers/status")
def providers_status() -> dict[str, Any]:
    providers = [provider.status(), yahoo_research_provider.status(), gemini.status(), {
        "provider": "Global market data",
        "status": "NOT_CONFIGURED",
        "note": "No authorized global feed or provider-symbol mappings are configured.",
    }, {
        "provider": "NSE delivery / F&O ban / FII-DII / news-events",
        "status": "NOT_CONFIGURED",
        "note": "These fields are deliberately unavailable until a verified source is selected.",
    }]
    return _envelope("PARTIAL", providers, source="provider health registry", count=len(providers))


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return _envelope("AVAILABLE", {
        **DEFAULT_ANALYSIS_SETTINGS,
        "available_timeframes": list(TIMEFRAMES),
        "custom_intraday_timeframes": {"min_minutes": 1, "max_minutes": 390, "supports_label_format": "<integer>m"},
        "providers": {"primary": "DhanHQ v2", "research_fallback": yahoo_research_provider.status(),
                      "global": "UNCONFIGURED", "gemini": gemini.status()},
        "zone_engine": "terminal.zone_core (user-supplied implementation)",
        "fno_ban_status": "UNAVAILABLE; new entries blocked",
        "global_assets": get_global_catalog(),
    }, source="application defaults")


@app.get("/api/news/status")
def news_status() -> dict[str, Any]:
    return news()
