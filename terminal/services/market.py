"""Market snapshot normalization, freshness, and data-quality helpers."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd

from terminal.providers.dhan import DhanProvider

IST = ZoneInfo("Asia/Kolkata")


def _get_quote(data: dict[str, Any], segment: str, security_id: str | None) -> dict[str, Any]:
    if not security_id:
        return {}
    return ((data.get(segment) or {}).get(str(security_id)) or {})


def _parse_provider_time(value: Any) -> datetime | None:
    if value in (None, "", "NA", "N/A"):
        return None
    try:
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        stamp = parsed.to_pydatetime()
        if stamp.year <= 1980:
            return None
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=IST)
        else:
            stamp = stamp.astimezone(IST)
        return stamp
    except (TypeError, ValueError, OverflowError):
        return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if pd.notna(result) else None
    except (TypeError, ValueError):
        return None


def _change(price: float | None, previous_close: float | None) -> tuple[float | None, float | None]:
    if price is None or previous_close is None or previous_close == 0:
        return None, None
    delta = price - previous_close
    return delta, delta / previous_close * 100.0


def _freshness(provider_time: datetime | None, received_at: datetime, threshold_seconds: int = 180) -> tuple[str, float | None]:
    if provider_time is None:
        return "TIMESTAMP_UNAVAILABLE", None
    age = max(0.0, (received_at - provider_time).total_seconds())
    return ("FRESH" if age <= threshold_seconds else "STALE"), age


def get_futures_snapshot(
    provider: DhanProvider,
    contracts: list[dict[str, Any]],
    master_received_at: datetime | None = None,
) -> dict[str, Any]:
    ids: list[dict[str, str]] = []
    for contract in contracts:
        if contract.get("security_id"):
            ids.append({"exchange_segment": "NSE_FNO", "security_id": str(contract["security_id"])})
        if contract.get("spot_security_id"):
            ids.append({"exchange_segment": "NSE_EQ", "security_id": str(contract["spot_security_id"])})
    raw = provider.get_quotes(ids)
    received_at = datetime.now(IST)
    payload = raw.get("data", {})
    rows: list[dict[str, Any]] = []
    for contract in contracts:
        fut = _get_quote(payload, "NSE_FNO", contract.get("security_id"))
        spot = _get_quote(payload, "NSE_EQ", contract.get("spot_security_id"))
        fut_ltp = _number(fut.get("last_price"))
        spot_ltp = _number(spot.get("last_price"))
        fut_close = _number((fut.get("ohlc") or {}).get("close"))
        spot_close = _number((spot.get("ohlc") or {}).get("close"))
        fut_change, fut_change_pct = _change(fut_ltp, fut_close)
        spot_change, spot_change_pct = _change(spot_ltp, spot_close)
        basis = (fut_ltp - spot_ltp) if fut_ltp is not None and spot_ltp is not None else None
        previous_basis = (fut_close - spot_close) if fut_close is not None and spot_close is not None else None
        basis_change = basis - previous_basis if basis is not None and previous_basis is not None else None

        provider_time = _parse_provider_time(fut.get("last_trade_time"))
        spot_provider_time = _parse_provider_time(spot.get("last_trade_time"))
        freshness, age = _freshness(provider_time, received_at)
        spot_freshness, spot_age = _freshness(spot_provider_time, received_at)
        oi = _number(fut.get("oi"))
        oi_delta = provider.observe_oi(
            f"{contract.get('security_id')}:{contract.get('expiry')}", oi, received_at
        ) if oi is not None else {
            "oi": None, "oi_change": None, "oi_change_pct": None,
            "observation_interval_seconds": None, "status": "UNAVAILABLE"
        }
        volume = _number(fut.get("volume"))
        spot_volume = _number(spot.get("volume"))
        actual_checks = [
            fut_ltp is not None, spot_ltp is not None, fut_change_pct is not None,
            spot_change_pct is not None, oi is not None, provider_time is not None,
            freshness == "FRESH", spot_freshness == "FRESH",
        ]
        quality_score = round(sum(actual_checks) / len(actual_checks) * 100)
        quality_status = "VERIFIED_FIELDS_PARTIAL" if quality_score < 100 else "VERIFIED_SNAPSHOT"
        row = {
            **contract,
            "spot_ltp": spot_ltp,
            "spot_change": spot_change,
            "spot_change_pct": spot_change_pct,
            "futures_ltp": fut_ltp,
            "futures_change": fut_change,
            "futures_change_pct": fut_change_pct,
            "futures_basis": basis,
            "basis_change": basis_change,
            "daily_futures_volume": volume,
            "daily_spot_volume": spot_volume,
            "selected_timeframe_volume": None,
            "average_volume": None,
            "volume_spike_ratio": None,
            "volume_signal": "NOT_CALCULATED",
            "futures_oi": oi,
            "futures_oi_change": oi_delta.get("oi_change"),
            "futures_oi_change_pct": oi_delta.get("oi_change_pct"),
            "oi_observation_status": oi_delta.get("status"),
            "oi_observation_interval_seconds": oi_delta.get("observation_interval_seconds"),
            "oi_observed_at": received_at.isoformat() if oi is not None else None,
            "spot_timestamp": spot_provider_time.isoformat() if spot_provider_time else None,
            "futures_timestamp": provider_time.isoformat() if provider_time else None,
            "received_at": received_at.isoformat(),
            "provider_latency_ms": raw.get("provider_latency_ms_total"),
            "provider_latency_ms_max": raw.get("provider_latency_ms_max"),
            "api_fetch_duration_ms": raw.get("api_fetch_duration_ms"),
            "data_age_seconds": age,
            "spot_data_age_seconds": spot_age,
            "freshness": freshness,
            "spot_freshness": spot_freshness,
            "source": provider.name,
            "data_quality_score": quality_score,
            "data_quality_status": quality_status,
            "fno_ban_status": "UNAVAILABLE",
            "risk_status": "ENTRY_BLOCKED_BAN_STATUS_UNVERIFIED",
            "final_ai_bias": "NO TRADE",
            "signal_reason": "Verified F&O ban status, zone/structure confirmations, and essential context are not all available.",
            "bullish_score": None,
            "bearish_score": None,
            "setup_quality": None,
            "tradingview_url": f"https://www.tradingview.com/chart/?symbol={quote('NSE:' + str(contract.get('symbol', '')), safe='')}",
        }
        rows.append(row)
    quotes_with_any_ltp = sum(1 for row in rows if row.get("futures_ltp") is not None or row.get("spot_ltp") is not None)
    if not rows or quotes_with_any_ltp == 0:
        snapshot_status = "UNAVAILABLE"
    elif quotes_with_any_ltp < len(rows):
        snapshot_status = "PARTIAL"
    else:
        snapshot_status = "AVAILABLE"
    return {
        "status": snapshot_status,
        "data": rows,
        "reason": None if snapshot_status == "AVAILABLE" else (
            "No usable instrument last-traded prices were returned." if snapshot_status == "UNAVAILABLE" else "Some instruments did not return a quote/timestamp."
        ),
        "source": provider.name,
        "source_timestamp": None,
        "received_at": received_at.isoformat(),
        "data_age_seconds": None,
        "note": "Quote timestamps are per instrument; receive time is measured by this API process.",
        "provider_latency_ms_total": raw.get("provider_latency_ms_total"),
        "provider_latency_ms_max": raw.get("provider_latency_ms_max"),
        "api_fetch_duration_ms": raw.get("api_fetch_duration_ms"),
        "request_count": raw.get("request_count"),
        "contracts_with_any_ltp": quotes_with_any_ltp,
        "total_contracts": len(rows),
        "missing_quote_rows": len(rows) - quotes_with_any_ltp,
        "master_received_at": master_received_at.isoformat() if master_received_at else None,
    }


def get_quote_for_contract(provider: DhanProvider, contract: dict[str, Any]) -> dict[str, Any]:
    return get_futures_snapshot(provider, [contract])["data"][0]
