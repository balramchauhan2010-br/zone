"""Dynamic NSE single-stock Futures universe from Dhan's current instrument master."""
from __future__ import annotations

import re
import threading
import time
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from terminal.providers.base import ProviderError
from terminal.providers.dhan import DhanProvider

IST = ZoneInfo("Asia/Kolkata")
_MASTER_TTL_SECONDS = 6 * 60 * 60
_master_lock = threading.Lock()
_master_frame: pd.DataFrame | None = None
_master_loaded_at: datetime | None = None


def get_instrument_master(provider: DhanProvider, refresh: bool = False) -> tuple[pd.DataFrame, datetime]:
    global _master_frame, _master_loaded_at
    now = datetime.now(IST)
    with _master_lock:
        if (
            not refresh and _master_frame is not None and _master_loaded_at is not None
            and (now - _master_loaded_at).total_seconds() < _MASTER_TTL_SECONDS
        ):
            return _master_frame.copy(), _master_loaded_at
        frame = provider.download_instrument_master()
        if frame.empty:
            raise ProviderError("Dhan instrument master is empty", "MASTER_EMPTY")
        _master_frame = frame
        _master_loaded_at = now
        return frame.copy(), now


def _value(row: pd.Series, key: str, default: str = "") -> str:
    value = row.get(key, default)
    if pd.isna(value):
        return default
    return str(value).strip()


def _normal_symbol(row: pd.Series) -> str:
    underlying = _value(row, "UNDERLYING_SYMBOL")
    if underlying:
        return underlying.strip().upper()
    symbol = _value(row, "SYMBOL_NAME")
    # Only parse the trading symbol when the separate underlying field is absent.
    if "-" in symbol:
        symbol = symbol.split("-", 1)[0]
    return symbol.strip().upper()


def _company_names(master: pd.DataFrame) -> dict[str, str]:
    required = {"EXCH_ID", "SEGMENT", "INSTRUMENT", "SYMBOL_NAME"}
    if not required.issubset(master.columns):
        return {}
    eq = master[
        (master["EXCH_ID"].astype(str).str.upper() == "NSE")
        & (master["SEGMENT"].astype(str).str.upper() == "E")
        & (master["INSTRUMENT"].astype(str).str.upper() == "EQUITY")
    ]
    if "SERIES" in eq.columns:
        eq = eq[eq["SERIES"].astype(str).str.upper().eq("EQ")]
    result: dict[str, str] = {}
    for _, row in eq.iterrows():
        symbol = (_value(row, "UNDERLYING_SYMBOL") or _value(row, "SYMBOL_NAME")).upper()
        label = _value(row, "DISPLAY_NAME") or _value(row, "SYMBOL_NAME")
        if symbol and label:
            result.setdefault(symbol, label)
    return result


def build_nse_futures_universe(
    master: pd.DataFrame,
    contract: str = "front",
    include_expired: bool = False,
    exclude_test_instruments: bool = True,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Return front/next/all actual FUTSTK contracts; never a hard-coded symbol list.

    ``front`` means nearest unexpired listed contract, not an assumed expiry date.
    Provider master membership is not represented as official ban clearance.
    """
    contract = contract.lower().strip()
    if contract not in {"front", "next", "all"}:
        raise ValueError("contract must be 'front', 'next', or 'all'")
    required = {"EXCH_ID", "SEGMENT", "SECURITY_ID", "INSTRUMENT", "SM_EXPIRY_DATE"}
    if not required.issubset(master.columns):
        missing = ", ".join(sorted(required.difference(master.columns)))
        raise ProviderError(f"Dhan instrument master is missing columns: {missing}", "MASTER_SCHEMA_ERROR")

    frame = master.copy()
    frame.columns = [str(c).strip().upper() for c in frame.columns]
    frame = frame[
        frame["EXCH_ID"].astype(str).str.upper().eq("NSE")
        & frame["SEGMENT"].astype(str).str.upper().eq("D")
        & frame["INSTRUMENT"].astype(str).str.upper().eq("FUTSTK")
    ].copy()
    if frame.empty:
        return []

    frame["_symbol"] = frame.apply(_normal_symbol, axis=1)
    frame["_expiry"] = pd.to_datetime(frame["SM_EXPIRY_DATE"], errors="coerce").dt.date
    frame["_security_id"] = frame["SECURITY_ID"].astype(str).str.strip()
    frame = frame[frame["_symbol"].ne("") & frame["_security_id"].ne("")]
    if exclude_test_instruments:
        frame = frame[~frame["_symbol"].str.contains(r"NSETEST|TEST", regex=True, na=False)]

    current_date = as_of or datetime.now(IST).date()
    if not include_expired:
        frame = frame[frame["_expiry"].notna() & (frame["_expiry"] >= current_date)]
    else:
        frame["_expiry"] = frame["_expiry"].where(frame["_expiry"].notna(), date.min)

    frame = frame.sort_values(["_symbol", "_expiry", "_security_id"], kind="stable")
    if contract != "all":
        nth = 0 if contract == "front" else 1
        frame = frame.groupby("_symbol", sort=True, group_keys=False).nth(nth).reset_index(drop=True)

    company_names = _company_names(master)
    spot_ids = _spot_security_id_map(master)
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        symbol = _value(row, "_symbol").upper()
        expiry = row.get("_expiry")
        expiry_text = expiry.isoformat() if isinstance(expiry, date) else None
        is_expired = isinstance(expiry, date) and expiry < current_date
        buy_sell = _value(row, "BUY_SELL_INDICATOR")
        rows.append({
            "symbol": symbol,
            "company_name": company_names.get(symbol, _value(row, "DISPLAY_NAME") or symbol),
            "instrument_type": "FUTSTK",
            "exchange": "NSE",
            "exchange_segment": "NSE_FNO",
            "security_id": _value(row, "_security_id"),
            "trading_symbol": _value(row, "SYMBOL_NAME"),
            "display_name": _value(row, "DISPLAY_NAME"),
            "expiry": expiry_text,
            "lot_size": _float_or_none(row.get("LOT_SIZE")),
            "tick_size": _float_or_none(row.get("TICK_SIZE")),
            "contract_status": "EXPIRED" if is_expired else ("VALID" if expiry_text else "MAPPING_ERROR"),
            "eligibility_status": "PROVIDER_LISTED" if buy_sell in {"A", "B", "S"} else "DATA_UNAVAILABLE",
            "fno_ban_status": "UNAVAILABLE",
            "market_data_status": "NOT_FETCHED",
            "spot_security_id": spot_ids.get(symbol),
            "source": "DhanHQ instrument master",
            "master_received_at": datetime.now(IST).isoformat(),
        })
    return rows


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
        return number if pd.notna(number) else None
    except (TypeError, ValueError):
        return None


def _spot_security_id_map(master: pd.DataFrame) -> dict[str, str]:
    required = {"EXCH_ID", "SEGMENT", "SECURITY_ID", "INSTRUMENT"}
    if not required.issubset(master.columns):
        return {}
    eq = master[
        master["EXCH_ID"].astype(str).str.upper().eq("NSE")
        & master["SEGMENT"].astype(str).str.upper().eq("E")
        & master["INSTRUMENT"].astype(str).str.upper().eq("EQUITY")
    ].copy()
    if "SERIES" in eq.columns:
        series_eq = eq[eq["SERIES"].astype(str).str.upper().eq("EQ")]
        if not series_eq.empty:
            eq = series_eq
    result: dict[str, str] = {}
    for _, row in eq.iterrows():
        symbol = _value(row, "UNDERLYING_SYMBOL") or _value(row, "SYMBOL_NAME")
        security_id = _value(row, "SECURITY_ID")
        if symbol and security_id:
            result.setdefault(symbol.upper(), security_id)
    return result


def _spot_security_id(master: pd.DataFrame, symbol: str) -> str | None:
    """Compatibility helper for one-off lookups; bulk mapping is computed once per universe."""
    return _spot_security_id_map(master).get(symbol.upper())


def get_contract_for_symbol(universe: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    target = symbol.strip().upper()
    return next((row for row in universe if row.get("symbol", "").upper() == target), None)


def master_summary(master: pd.DataFrame, received_at: datetime) -> dict[str, Any]:
    futures_count = 0
    symbols_count = 0
    if {"EXCH_ID", "SEGMENT", "INSTRUMENT", "UNDERLYING_SYMBOL"}.issubset(master.columns):
        items = master[
            master["EXCH_ID"].astype(str).str.upper().eq("NSE")
            & master["SEGMENT"].astype(str).str.upper().eq("D")
            & master["INSTRUMENT"].astype(str).str.upper().eq("FUTSTK")
        ]
        futures_count = len(items)
        symbols_count = items["UNDERLYING_SYMBOL"].nunique()
    return {
        "provider": "DhanHQ instrument master",
        "contracts_in_master": int(futures_count),
        "underlyings_in_master": int(symbols_count),
        "received_at": received_at.isoformat(),
        "source_timestamp": None,
        "status": "AVAILABLE",
        "note": "Master file receipt time is known; publisher timestamp is not exposed by this adapter.",
    }
