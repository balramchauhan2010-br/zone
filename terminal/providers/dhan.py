"""DhanHQ v2 read-only market-data adapter.

Credentials are read only from the process environment. This module never
places or modifies orders and never logs credential headers.
"""
from __future__ import annotations

import io
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from terminal.config import DHAN_ACCESS_TOKEN, DHAN_BASE_URL, DHAN_CLIENT_ID, DHAN_MASTER_URL
from terminal.providers.base import ProviderError

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class DhanProvider:
    name = "DhanHQ v2"

    def __init__(self, access_token: str | None = None, client_id: str | None = None,
                 base_url: str | None = None, timeout: float = 20.0):
        self._access_token = (access_token if access_token is not None else DHAN_ACCESS_TOKEN).strip()
        self._client_id = (client_id if client_id is not None else DHAN_CLIENT_ID).strip()
        self.base_url = (base_url or DHAN_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(timeout=httpx.Timeout(timeout, connect=8.0))
        self._rate_lock = threading.Lock()
        self._last_request_by_path: dict[str, float] = {}
        self._snapshot_lock = threading.Lock()
        self._oi_snapshots: dict[str, tuple[float, datetime]] = {}
        self._last_success_at: datetime | None = None
        self._last_error_code: str | None = None
        self._last_latency_ms: float | None = None

    @property
    def credentials_ready(self) -> bool:
        return bool(self._access_token and self._client_id)

    def close(self) -> None:
        self._client.close()

    def _ensure_client(self) -> None:
        if self._client.is_closed:
            self._client = httpx.Client(timeout=httpx.Timeout(self.timeout, connect=8.0))

    @property
    def last_latency_ms(self) -> float | None:
        return self._last_latency_ms

    def status(self) -> dict[str, Any]:
        if not self.credentials_ready:
            state = "CREDENTIALS_MISSING"
            note = "Set DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN in server environment variables."
        elif self._last_error_code:
            state = "DEGRADED"
            note = f"Last provider error code: {self._last_error_code}"
        elif self._last_success_at:
            state = "CONNECTED"
            note = "A provider request has succeeded in this process."
        else:
            state = "CONFIGURED_NOT_CHECKED"
            note = "Credentials are present; health is confirmed after a successful data request."
        return {
            "provider": self.name,
            "status": state,
            "credentials_configured": self.credentials_ready,
            "last_success_at": self._last_success_at.isoformat() if self._last_success_at else None,
            "last_error_code": self._last_error_code,
            "last_http_request_latency_ms": self._last_latency_ms,
            "note": note,
        }

    def _headers(self) -> dict[str, str]:
        if not self.credentials_ready:
            raise ProviderError("Dhan API credentials are not configured", "CREDENTIALS_MISSING")
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": self._access_token,
            "client-id": self._client_id,
        }

    def _throttle(self, path: str, minimum_interval: float) -> None:
        if minimum_interval <= 0:
            return
        # Treat expiry-list and chain retrieval as one option-chain quota bucket.
        rate_key = "option-chain" if path.startswith("/optionchain") else path
        with self._rate_lock:
            now = time.monotonic()
            previous = self._last_request_by_path.get(rate_key, 0.0)
            delay = minimum_interval - (now - previous)
            if delay > 0:
                time.sleep(delay)
            self._last_request_by_path[rate_key] = time.monotonic()

    @staticmethod
    def _error_code(response: httpx.Response) -> tuple[str, str]:
        code = f"HTTP_{response.status_code}"
        body_text = ""
        try:
            body = response.json()
            body_text = str(body.get("errorCode", body.get("error", body.get("message", ""))))
            if body_text.isdigit():
                code = body_text
        except Exception:
            pass
        mapping = {
            "806": "Dhan Data APIs are not subscribed for this account.",
            "807": "Dhan access token is expired; generate/renew it in Dhan.",
            "808": "Dhan authentication failed; check client ID and access token.",
            "809": "Dhan access token is invalid.",
            "810": "Dhan client ID is invalid.",
            "811": "The requested Dhan contract expiry is invalid.",
            "812": "Dhan rejected the requested date format.",
        }
        message = mapping.get(code, f"Dhan request failed with HTTP {response.status_code}.")
        return code, message

    def _post(self, path: str, payload: dict[str, Any], throttle_seconds: float = 1.0) -> dict[str, Any]:
        self._ensure_client()
        self._throttle(path, throttle_seconds)
        headers = self._headers()
        request_started = time.perf_counter()
        try:
            response = self._client.post(
                f"{self.base_url}{path}", headers=headers, json=payload
            )
        except httpx.TimeoutException as exc:
            self._last_latency_ms = round((time.perf_counter() - request_started) * 1000, 2)
            self._last_error_code = "TIMEOUT"
            raise ProviderError("Dhan API request timed out", "TIMEOUT", retryable=True) from exc
        except httpx.HTTPError as exc:
            self._last_latency_ms = round((time.perf_counter() - request_started) * 1000, 2)
            self._last_error_code = "NETWORK_ERROR"
            raise ProviderError("Could not connect to Dhan API", "NETWORK_ERROR", retryable=True) from exc
        self._last_latency_ms = round((time.perf_counter() - request_started) * 1000, 2)
        if response.status_code >= 400:
            code, message = self._error_code(response)
            self._last_error_code = code
            raise ProviderError(message, code, retryable=response.status_code in (429, 500, 502, 503, 504))
        try:
            payload_out = response.json()
        except ValueError as exc:
            self._last_error_code = "INVALID_JSON"
            raise ProviderError("Dhan returned a non-JSON response", "INVALID_JSON") from exc
        self._last_success_at = datetime.now(IST)
        self._last_error_code = None
        if isinstance(payload_out, dict) and str(payload_out.get("status", "success")).lower() == "failure":
            code = str(payload_out.get("errorCode", "PROVIDER_FAILURE"))
            self._last_error_code = code
            raise ProviderError("Dhan reported an unsuccessful data request", code)
        return payload_out

    def download_instrument_master(self) -> pd.DataFrame:
        """Fetch Dhan's public detailed instrument master; no account token required."""
        self._ensure_client()
        try:
            response = self._client.get(DHAN_MASTER_URL, timeout=self.timeout)
            response.raise_for_status()
            frame = pd.read_csv(io.BytesIO(response.content), dtype=str, low_memory=False)
        except (httpx.HTTPError, pd.errors.ParserError, ValueError) as exc:
            raise ProviderError("Could not load Dhan instrument master", "INSTRUMENT_MASTER_ERROR", True) from exc
        frame.columns = [str(c).strip().upper() for c in frame.columns]
        if "SECURITY_ID" not in frame.columns or "INSTRUMENT" not in frame.columns:
            raise ProviderError("Dhan instrument master schema changed; required columns are missing", "MASTER_SCHEMA_ERROR")
        return frame

    def get_quotes(self, instruments: list[dict[str, str]]) -> dict[str, Any]:
        """Get quote/OI/volume snapshots in groups of at most 1000 instruments."""
        grouped: dict[str, list[str]] = {}
        for inst in instruments:
            segment = str(inst.get("exchange_segment", "")).strip()
            security_id = str(inst.get("security_id", "")).strip()
            if segment and security_id and security_id not in grouped.setdefault(segment, []):
                grouped[segment].append(security_id)
        if not grouped:
            return {"data": {}, "status": "success", "received_at": datetime.now(IST).isoformat()}
        merged: dict[str, Any] = {}
        call_latencies: list[float] = []
        fetch_started = time.perf_counter()
        # Send one segment's <=1000 IDs per request, conservatively staying below
        # the documented overall batch size even when multiple segments are used.
        for segment, ids in grouped.items():
            for sub_index in range(0, len(ids), 1000):
                batch = [int(x) for x in ids[sub_index:sub_index + 1000] if x.isdigit()]
                if not batch:
                    continue
                response = self._post("/marketfeed/quote", {segment: batch}, throttle_seconds=1.05)
                if self._last_latency_ms is not None:
                    call_latencies.append(self._last_latency_ms)
                for returned_segment, records in (response.get("data") or {}).items():
                    merged.setdefault(returned_segment, {}).update(records or {})
        return {
            "data": merged,
            "status": "success",
            "received_at": datetime.now(IST).isoformat(),
            "provider_latency_ms_total": round(sum(call_latencies), 2),
            "provider_latency_ms_max": max(call_latencies) if call_latencies else None,
            "api_fetch_duration_ms": round((time.perf_counter() - fetch_started) * 1000, 2),
            "request_count": len(call_latencies),
        }

    def get_intraday_candles(
        self, instrument: dict[str, Any], from_dt: datetime, to_dt: datetime,
        interval: str = "1", include_oi: bool = False,
    ) -> pd.DataFrame:
        if interval not in {"1", "5", "15", "25", "60"}:
            raise ValueError("Dhan native intraday interval must be one of 1, 5, 15, 25, 60 minutes")
        if from_dt.tzinfo is None:
            from_dt = from_dt.replace(tzinfo=IST)
        if to_dt.tzinfo is None:
            to_dt = to_dt.replace(tzinfo=IST)
        if to_dt <= from_dt:
            raise ValueError("to_dt must be later than from_dt")
        if (to_dt - from_dt).days > 90:
            raise ValueError("Dhan intraday requests are limited to 90 days per call; request a shorter range")
        body = {
            "securityId": str(instrument["security_id"]),
            "exchangeSegment": str(instrument["exchange_segment"]),
            "instrument": str(instrument.get("instrument", "FUTSTK")),
            "interval": interval,
            "oi": bool(include_oi),
            "fromDate": from_dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
            "toDate": to_dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
        }
        response = self._post("/charts/intraday", body, throttle_seconds=1.05)
        return self._candles_from_response(response)

    def get_daily_candles(
        self, instrument: dict[str, Any], from_dt: datetime, to_dt: datetime,
        include_oi: bool = False,
    ) -> pd.DataFrame:
        if from_dt.tzinfo is None:
            from_dt = from_dt.replace(tzinfo=IST)
        if to_dt.tzinfo is None:
            to_dt = to_dt.replace(tzinfo=IST)
        body: dict[str, Any] = {
            "securityId": str(instrument["security_id"]),
            "exchangeSegment": str(instrument["exchange_segment"]),
            "instrument": str(instrument.get("instrument", "FUTSTK")),
            "oi": bool(include_oi),
            "fromDate": from_dt.astimezone(IST).strftime("%Y-%m-%d"),
            "toDate": to_dt.astimezone(IST).strftime("%Y-%m-%d"),
        }
        if instrument.get("expiry_code") is not None:
            body["expiryCode"] = int(instrument["expiry_code"])
        response = self._post("/charts/historical", body, throttle_seconds=1.05)
        return self._candles_from_response(response)

    @staticmethod
    def _candles_from_response(response: dict[str, Any]) -> pd.DataFrame:
        data = response.get("data", response)
        if not isinstance(data, dict):
            raise ProviderError("Dhan candle response has an unexpected format", "CANDLE_SCHEMA_ERROR")
        timestamp_values = data.get("timestamp") or data.get("timestamps") or []
        if not timestamp_values:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        first = timestamp_values[0]
        if isinstance(first, (int, float)) or str(first).strip().isdigit():
            first_num = int(float(first))
            unit = "ms" if first_num > 10**12 else "s"
            index = pd.to_datetime(timestamp_values, unit=unit, utc=True).tz_convert(IST)
        else:
            index = pd.to_datetime(timestamp_values, errors="coerce")
            index = pd.DatetimeIndex(index)
            index = index.tz_localize(IST) if index.tz is None else index.tz_convert(IST)

        frame = pd.DataFrame(index=index)
        for column in ("open", "high", "low", "close", "volume", "oi"):
            values = data.get(column)
            if values is not None and len(values) == len(index):
                frame[column] = pd.to_numeric(values, errors="coerce")
        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(frame.columns):
            raise ProviderError("Dhan candle response is missing OHLCV fields", "CANDLE_SCHEMA_ERROR")
        frame = frame.sort_index().loc[~frame.index.duplicated(keep="last")]
        frame["volume"] = frame["volume"].fillna(0.0)
        return frame.dropna(subset=["open", "high", "low", "close"])

    def get_option_expiries(self, underlying_security_id: str, underlying_segment: str = "NSE_EQ") -> list[str]:
        body = {"UnderlyingScrip": int(underlying_security_id), "UnderlyingSeg": underlying_segment}
        response = self._post("/optionchain/expirylist", body, throttle_seconds=3.05)
        values = response.get("data") or []
        return [str(value) for value in values]

    def get_option_chain(self, underlying_security_id: str, expiry: str,
                         underlying_segment: str = "NSE_EQ") -> dict[str, Any]:
        body = {
            "UnderlyingScrip": int(underlying_security_id),
            "UnderlyingSeg": underlying_segment,
            "Expiry": expiry,
        }
        return self._post("/optionchain", body, throttle_seconds=3.05)

    def observe_oi(self, contract_key: str, oi: float, observed_at: datetime | None = None) -> dict[str, Any]:
        """Calculate change only between actual quote observations in this process."""
        timestamp = observed_at or datetime.now(IST)
        with self._snapshot_lock:
            previous = self._oi_snapshots.get(contract_key)
            self._oi_snapshots[contract_key] = (float(oi), timestamp)
        if previous is None:
            return {"oi": float(oi), "oi_change": None, "oi_change_pct": None,
                    "observation_interval_seconds": None, "status": "BASELINE_PENDING"}
        old_oi, old_ts = previous
        change = float(oi) - old_oi
        pct = (change / old_oi * 100.0) if old_oi else None
        interval = max(0.0, (timestamp - old_ts).total_seconds())
        return {"oi": float(oi), "oi_change": change, "oi_change_pct": pct,
                "observation_interval_seconds": interval, "status": "OBSERVATION_DELTA"}
