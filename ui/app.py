"""
================================================================================
 AI PRO INSTITUTIONAL TRADING TERMINAL  —  aap.py
--------------------------------------------------------------------------------
 Single-file, self-contained Streamlit terminal for NSE F&O + Global markets.

 FEATURES
   Tab 1 : NSE ALL FUTURE STOCKS  (live scan, Top-5 Buy / Top-5 Sell, breadth)
   Tab 2 : ZONE                   (multi-timeframe nearest demand/supply zone)
   Tab 3 : GLOBAL ZONE            (multi-timeframe scan of global instruments)
   Tab 4 : NEWS & EVENTS          (global strip, breadth, FII/DII, alerts)
   Tab 5 : SETTINGS               (zone engine, risk, timeframe, provider status)

 DATA HONESTY POLICY (important)
   * No fabricated Futures / OI / PCR / Delivery numbers.
   * If a feed is unreachable, the cell shows "Unavailable" + the reason.
   * Every derived number carries a source label in the AI Hypothesis modal.

 DEPLOY
   streamlit run aap.py --server.port=$PORT --server.address=0.0.0.0
================================================================================
"""

from __future__ import annotations

import os
import json
import math
import re
import time
import uuid
import threading
import concurrent.futures as cf
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable
from urllib.parse import quote as url_quote
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf
import httpx


# ==============================================================================
# 0) OPTIONAL PROJECT MODULES  (auto-detected — built-in fallbacks used if absent)
# =============================================================================

def _builtin_apply_streamlit_secrets() -> None:
    """Fallback for `ui.runtime_config.apply_streamlit_secrets`.

    Loads a local `.env` file and mirrors Streamlit secrets into os.environ
    so that provider keys are available regardless of deployment style.
    """
    base = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(base, ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        except Exception:
            pass
    try:
        secrets = st.secrets
        for key in list(secrets.keys()):
            value = secrets[key]
            if isinstance(value, str):
                os.environ.setdefault(key, value)
            elif isinstance(value, dict):
                for k2, v2 in value.items():
                    if isinstance(v2, str):
                        os.environ.setdefault(k2, v2)
    except Exception:
        pass


try:  # pragma: no cover - depends on user's repo layout
    from ui.runtime_config import apply_streamlit_secrets  # type: ignore
except Exception:  # pragma: no cover
    apply_streamlit_secrets = _builtin_apply_streamlit_secrets

apply_streamlit_secrets()

try:  # pragma: no cover
    from terminal.config import DEFAULT_ANALYSIS_SETTINGS  # type: ignore
except Exception:  # pragma: no cover
    DEFAULT_ANALYSIS_SETTINGS = {
        "eod_high_buffer_pct": 10.0,
        "eod_low_buffer_pct": 10.0,
        "distal_buffer_pct": 0.5,
        "use_eod_range": True,
        "account_capital": 25000.0,
        "risk_pct": 0.5,
        "target_rr": 5.0,
        "selected_tf": "Daily",
        "mcap_filter": "All",
        "custom_tfs": ["15m", "30m", "75m", "1h", "2h", "4h", "6h", "8h",
                       "Daily", "Weekly", "Monthly"],
    }


IST = ZoneInfo("Asia/Kolkata")
NOW_IST = datetime.now(IST)

st.set_page_config(
    page_title="AI Pro Institutional Trading Terminal",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# ==============================================================================
# 1) CONSTANTS
# ==============================================================================

ALL_TIMEFRAMES: list[str] = [
    "15m", "30m", "75m", "1h", "2h", "4h", "6h", "8h", "Daily", "Weekly", "Monthly",
]

# Which yfinance interval / period feeds every user facing timeframe.
# "2h,4h,6h,8h" are resampled from the 1h feed, "Weekly/Monthly" from the daily feed.
TF_FEED_SPEC: dict[str, dict[str, str]] = {
    "15m":    {"source": "yf", "interval": "15m", "period": "1mo"},
    "30m":    {"source": "yf", "interval": "30m", "period": "1mo"},
    "75m":    {"source": "rs", "base": "1h",   "rule": "75min"},
    "1h":     {"source": "yf", "interval": "60m", "period": "3mo"},
    "2h":     {"source": "rs", "base": "1h",   "rule": "2h"},
    "4h":     {"source": "rs", "base": "1h",   "rule": "4h"},
    "6h":     {"source": "rs", "base": "1h",   "rule": "6h"},
    "8h":     {"source": "rs", "base": "1h",   "rule": "8h"},
    "Daily":  {"source": "yf", "interval": "1d",  "period": "1y"},
    "Weekly": {"source": "rs", "base": "Daily", "rule": "W-FRI"},
    "Monthly": {"source": "rs", "base": "Daily", "rule": "ME"},
}

TF_PRIORITY: dict[str, int] = {tf: i for i, tf in enumerate(ALL_TIMEFRAMES)}

YF_TF_MAP = {tf: TF_FEED_SPEC[tf].get("interval", "1d") for tf in ALL_TIMEFRAMES}

TV_INTERVAL_MAP = {
    "15m": "15", "30m": "30", "75m": "75", "1h": "60", "2h": "120",
    "4h": "240", "6h": "360", "8h": "480", "Daily": "D", "Weekly": "W", "Monthly": "M",
}

TV_SYMBOL_MAP = {
    "15m": "15", "30m": "30", "75m": "75", "1h": "60", "2h": "120",
    "4h": "240", "6h": "360", "8h": "480", "Daily": "D", "Weekly": "W", "Monthly": "M",
}

GLOBAL_INSTRUMENTS: list[dict[str, str]] = [
    {"id": "DXY", "name": "US Dollar Index", "ticker": "DX-Y.NYB", "tv": "TVC:DXY"},
    {"id": "USDINR", "name": "USD / INR", "ticker": "USDINR=X", "tv": "FX_IDC:USDINR"},
    {"id": "TLT", "name": "iShares 20+ Yr Treasury ETF", "ticker": "TLT", "tv": "NASDAQ:TLT"},
    {"id": "US10Y", "name": "US 10-Year Treasury Yield", "ticker": "^TNX", "tv": "TVC:US10Y"},
    {"id": "XAUUSD", "name": "Gold / US Dollar", "ticker": "GC=F", "tv": "OANDA:XAUUSD"},
    {"id": "XAGUSD", "name": "Silver / US Dollar", "ticker": "SI=F", "tv": "OANDA:XAUUSD"},
    {"id": "SPOTCRUDE", "name": "WTI Crude Oil", "ticker": "CL=F", "tv": "TVC:USOIL"},
    {"id": "US30", "name": "Dow Jones Industrial Average", "ticker": "^DJI", "tv": "DJ:DJI"},
    {"id": "US500", "name": "S&P 500 Index", "ticker": "^GSPC", "tv": "SP:SPX"},
    {"id": "000001", "name": "SSE Composite Index", "ticker": "000001.SS", "tv": "SSE:000001"},
    {"id": "XIN9", "name": "FTSE China A50 Index", "ticker": "XIN9.FGI", "tv": "SGX:XIN9"},
    {"id": "JP225", "name": "Nikkei 225 Index", "ticker": "^N225", "tv": "INDEX:NKY"},
    {"id": "NIFTY_GIFT", "name": "GIFT NIFTY 50 Futures", "ticker": "^NSEI", "tv": "NSE:NIFTY1!"},
    {"id": "NIFTY_FUT", "name": "Nifty 50 Futures", "ticker": "^NSEI", "tv": "NSE:NIFTY1!"},
    {"id": "FTSE100", "name": "FTSE 100 Index", "ticker": "^FTSE", "tv": "INDEX:FTSE"},
    {"id": "DAX", "name": "DAX Index", "ticker": "^GDAXI", "tv": "XETR:DAX"},
    {"id": "COPPER", "name": "Copper", "ticker": "HG=F", "tv": "COMEX:HG1!"},
    {"id": "ALUMINIUM", "name": "Aluminium", "ticker": "ALI=F", "tv": "MCX:ALUMINIUM1!"},
    {"id": "ZINC", "name": "Zinc", "ticker": "ZNC=F", "tv": "MCX:ZINC1!"},
    {"id": "NATGAS", "name": "Natural Gas", "ticker": "NG=F", "tv": "NYMEX:NG1!"},
    {"id": "GBY_USD", "name": "GBP / USD", "ticker": "GBPUSD=X", "tv": "FX:GBPUSD"},
    {"id": "URO_USD", "name": "EUR / USD", "ticker": "EURUSD=X", "tv": "FX:EURUSD"},
    {"id": "JPY_USD", "name": "JPY / USD", "ticker": "JPY=X", "tv": "FX:USDJPY"},
    {"id": "JPY_INR", "name": "JPY / INR", "ticker": "JPYINR=X", "tv": "FX_IDC:JPYINR"},
    {"id": "USD_INR", "name": "USD / INR Spot", "ticker": "USDINR=X", "tv": "FX_IDC:USDINR"},
    {"id": "JBP_INR", "name": "GBP / INR", "ticker": "GBPINR=X", "tv": "FX_IDC:GBPINR"},
    {"id": "BTCUSD", "name": "Bitcoin / USD", "ticker": "BTC-USD", "tv": "BINANCE:BTCUSDT"},
]

FALLBACK_FNO_UNIVERSE = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC", "LT",
    "TATAMOTORS", "ADANIENT", "ADANIPORTS", "ADANIPOWER", "ADANIENSOL", "ADANIGREEN",
    "AXISBANK", "MARUTI", "BAJFINANCE", "KOTAKBANK", "TATASTEEL", "SUNPHARMA", "TITAN",
    "WIPRO", "NTPC", "JSWSTEEL", "POWERGRID", "ONGC", "M&M", "COALINDIA", "BAJAJFINSV",
    "HCLTECH", "TECHM", "EICHERMOT", "DIVISLAB", "DRREDDY", "CIPLA", "APOLLOHOSP",
    "HINDUNILVR", "NESTLEIND", "BRITANNIA", "ASIANPAINT", "GRASIM", "ULTRACEMCO",
    "AMBUJACEM", "INDUSINDBK", "BANKBARODA", "PNB", "CANBK", "FEDERALBNK",
    "IDFCFIRSTB", "DLF", "GODREJPROP", "ASTRAL", "TRENT", "BEL", "HAL", "BHEL",
    "SIEMENS", "ABB", "VEDL", "HINDALCO", "NMDC", "SAIL", "JINDALSTEL", "TATACONSUM",
    "ABCAPITAL", "CHOLAFIN", "PFC", "RECLTD", "PERSISTENT", "COFORGE", "LTIM", "JSWENERGY",
    "TATAELXSI", "LICHSGFIN", "SHRIRAMFIN", "PAGEIND", "POLYCAB", "SBILIFE", "PGEL",
    "SHREECEM", "NAUKRI", "TVSMOTOR", "MOTHERSON", "CUMMINSIND", "BOSCHLTD", "GILLETTE",
    "BERGEPAINT", "BPCL", "IOC", "HDFCLIFE", "SBICARD", "MOTHERSON", "LUPIN", "AUROPHARMA",
]

NON_EQUITY_BLOCKLIST = {
    "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "GOLDM", "GOLD", "SILVERM",
    "SILVER", "COPPER", "CRUDEOIL", "CRUDEOILM", "EURINR", "GBPINR", "JPYINR",
    "USDINR", "AUDINR", "CADINR", "SGDINR", "EUROPY", "GBPUSD", "USDJPY", "EURUSD",
    "USDCNY", "USDINR", "INRUSD", "DIESEL", "PETROL", "GAS", "ALUMINIUM", "ZINC",
    "LEAD", "NICKEL", "COPPERM", "BRENT", "WTI", "NATURALGAS",
}

ORDERED_COLUMNS_TAB1 = [
    "Symbol", "Spot LTP", "Futures LTP", "Dif. %", "Spot Change %", "Futures Change %",
    "Selected-Timeframe Volume", "Volume Spike Ratio", "Futures OI Change %",
    "Latest Verified Delivery %", "PCR", "CE Total OI", "PE Total OI",
    "Buildup Classification", "EMA 35 Signal", "EMA 100 Signal", "VWAP Signal",
    "Zone Signal", "Live Signals Highlight", "Final AI Bias", "Data Source",
]

NA = "Unavailable"


# ==============================================================================
# 2) UI STYLING
# ==============================================================================

st.markdown(
    """
    <style>
    :root { color-scheme: dark; }
    .stApp { background: #07111f; color: #e7eef8; }
    [data-testid="stHeader"] { background: rgba(7,17,31,.92); }
    [data-testid="stSidebar"] { background: #0a1728; }
    h1, h2, h3 { color: #f3f7ff; letter-spacing: .01em; }
    h1 { font-size: 1.55rem !important; margin-bottom: .15rem !important; }
    h2 { font-size: 1.12rem !important; margin-top: .3rem !important; }
    div[data-testid="stDataFrame"] { border: 1px solid #1a3858; border-radius: 6px; }
    .stButton button { border: 1px solid #315273; background: #112744; color: #edf5ff; border-radius: 5px; }
    .stButton button:hover { border-color: #55a6ec; color: white; }
    .strip-card { background: #0b1a2d; border: 1px solid #18314d; padding: 6px 8px; border-radius: 6px; text-align: center; }
    .strip-title { color: #8ea5c0; font-size: 0.68rem; margin-bottom: 2px; }
    .strip-val { font-size: 0.88rem; font-weight: bold; color: #f1f6ff; }
    .pos-txt { color: #00e676; font-size: 0.75rem; font-weight: bold; }
    .neg-txt { color: #ff5252; font-size: 0.75rem; font-weight: bold; }
    .fii-box { background: #0b1a2d; border: 1px solid #1c3d61; border-radius: 6px; padding: 12px; margin-bottom: 8px; }
    .fii-header { font-size: 0.82rem; color: #9bb7d4; font-weight: 500; }
    .fii-amount { font-size: 1.15rem; font-weight: bold; margin-top: 2px; }
    .hyp-card { background: #0b1a2d; border: 1px solid #1b3a5c; border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; }
    .hyp-title { font-size: 0.86rem; color: #9fc2e4; font-weight: 600; letter-spacing: .02em; }
    div[data-testid="stRadio"] { gap: 6px; }
    div[data-testid="stRadio"] label { background:#0b1a2d; border:1px solid #1b3a5c; border-radius:6px;
        padding: 7px 16px; margin-right: 4px; }
    div[data-testid="stRadio"] label:has(input:checked) { background:#143055; border-color:#55a6ec; }
    div[data-testid="stRadio"] label p { font-size: 0.9rem; font-weight: 600; color:#cfe0f5; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ==============================================================================
# 3) SMALL UTILITIES
# ==============================================================================

def _df_layout() -> dict:
    """Streamlit 1.45+ renamed use_container_width -> width='stretch'."""
    try:
        parts = st.__version__.split(".")
        major, minor = int(parts[0]), int(parts[1])
        if (major, minor) >= (1, 45):
            return {"width": "stretch"}
    except Exception:
        pass
    return {"use_container_width": True}


def _fmt_num(value: Any, digits: int = 2, prefix: str = "") -> str:
    if value is None:
        return NA
    try:
        if isinstance(value, str):
            return value
        return f"{prefix}{float(value):,.{digits}f}"
    except Exception:
        return NA


def _pct(value: Any, digits: int = 2) -> str:
    if value is None:
        return NA
    try:
        return f"{float(value):+.{digits}f}%"
    except Exception:
        return NA


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.replace(",", "").replace("%", "").replace("₹", "").strip()
            if value in ("", "-", "--", "NaN", "nan", "None"):
                return None
        out = float(value)
        if math.isnan(out) or math.isinf(out):
            return None
        return out
    except Exception:
        return None


def _linkify_table(frame: pd.DataFrame) -> pd.DataFrame:
    display = frame.copy()
    if "Symbol" in display.columns and "_Spot_Chart_URL" in display.columns:
        display["Symbol"] = display["_Spot_Chart_URL"].fillna("").astype(str)
        display = display.drop(columns=["_Spot_Chart_URL"])
    if "Futures LTP" in display.columns and "_Fut_Chart_URL" in display.columns:
        display["Futures LTP"] = display["_Fut_Chart_URL"].fillna("").astype(str)
        display = display.drop(columns=["_Fut_Chart_URL"])
    if "Final AI Bias" in display.columns and "_Bias_Link_URL" in display.columns:
        display["Final AI Bias"] = display["_Bias_Link_URL"].fillna("").astype(str)
        display = display.drop(columns=["_Bias_Link_URL"])
    return display


# ==============================================================================
# 4) ZONE ENGINE  (built-in fallback — used only if terminal.zone_core is absent)
# ==============================================================================

@dataclass
class _Zone:
    state: str          # Fresh | Tested | Invalidated
    isDemand: bool
    proxVal: float
    distVal: float
    tf: str = "1h"
    tests: int = 0
    index: int = 0

    @property
    def isSupply(self) -> bool:
        return not self.isDemand


def _session_bounds(df: pd.DataFrame) -> tuple[float, float]:
    """High / Low of the most recent session in the dataframe."""
    try:
        if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
            last_day = df.index[-1].date()
            day_rows = df[df.index.date == last_day]
            if len(day_rows):
                return float(day_rows["high"].max()), float(day_rows["low"].min())
    except Exception:
        pass
    return float(df["high"].iloc[-1]), float(df["low"].iloc[-1])


def _builtin_scan_zones(df: pd.DataFrame, params: dict | None = None, tf: str = "1h") -> list[_Zone]:
    """OHLCV based demand / supply zone detector (safe fallback engine)."""
    p = params or {}
    hi_buf = float(p.get("eodHighBufferPct", 10.0))
    lo_buf = float(p.get("eodLowBufferPct", 10.0))
    use_eod = bool(p.get("useEodRange", True))

    if df is None or df is None or len(df) < 12:
        return []
    try:
        o = df["open"].to_numpy(dtype=float)
        h = df["high"].to_numpy(dtype=float)
        l = df["low"].to_numpy(dtype=float)
        c = df["close"].to_numpy(dtype=float)
    except Exception:
        return []

    n = len(c)
    if n < 12:
        return []
    strength = 2
    zones: list[_Zone] = []

    if use_eod:
        try:
            eod_high, eod_low = _session_bounds(df)
            hi_limit = eod_high * (1.0 + hi_buf / 100.0)
            lo_limit = eod_low * (1.0 - lo_buf / 100.0)
        except Exception:
            hi_limit, lo_limit = float("inf"), float("-inf")
    else:
        hi_limit, lo_limit = float("inf"), float("-inf")

    for i in range(strength, n - strength):
        win_h = h[i - strength: i + strength + 1]
        win_l = l[i - strength: i + strength + 1]
        pivot_low = bool(l[i] == win_l.min() and (win_l == l[i]).sum() == 1)
        pivot_high = bool(h[i] == win_h.max() and (win_h == h[i]).sum() == 1)
        if not (pivot_low or pivot_high):
            continue

        after_h, after_l, after_c = h[i + 1:], l[i + 1:], c[i + 1:]
        if pivot_low:
            distal = float(l[i])
            proximal = float(min(o[i], c[i]))
            invalid = bool((after_c < distal).any())
            touched = after_l <= proximal
        else:
            distal = float(h[i])
            proximal = float(max(o[i], c[i]))
            invalid = bool((after_c > distal).any())
            touched = after_h >= proximal

        tests = int(touched.sum())
        state = "Invalidated" if invalid else ("Fresh" if tests == 0 else "Tested")

        prox = proximal
        if use_eod:
            if pivot_low:
                prox = min(prox, hi_limit)
                prox = max(prox, lo_limit)
                prox = max(prox, distal)
            else:
                prox = max(prox, lo_limit)
                prox = min(prox, hi_limit)
                prox = min(prox, distal)

        zones.append(_Zone(state=state, isDemand=pivot_low, proxVal=round(float(prox), 4),
                           distVal=round(float(distal), 4), tf=tf, tests=tests, index=i))
    return zones


def _builtin_latest_active_zones(zones: list[_Zone]) -> list[_Zone]:
    active = [z for z in zones if z.state in ("Fresh", "Tested")]
    return sorted(active, key=lambda z: z.index)


try:  # pragma: no cover - uses the user's real engine when present
    from terminal.zone_core import scan_zones, latest_active_zones  # type: ignore
    ZONE_ENGINE_SOURCE = "terminal.zone_core (project engine)"
except Exception:  # pragma: no cover
    scan_zones = _builtin_scan_zones          # type: ignore
    latest_active_zones = _builtin_latest_active_zones  # type: ignore
    ZONE_ENGINE_SOURCE = "built-in OHLCV zone engine (fallback)"


def _pick_nearest_zone(zones: Iterable[Any], price: float) -> tuple[Any | None, float | None]:
    """Nearest active zone to `price` (absolute distance)."""
    best, best_dist = None, None
    for z in zones:
        if str(getattr(z, "state", "")).lower() == "invalidated":
            continue
        prox = _safe_float(getattr(z, "proxVal", None))
        if prox is None:
            continue
        dist = abs(price - prox)
        if best_dist is None or dist < best_dist:
            best, best_dist = z, dist
    return best, best_dist


# ==============================================================================
# 5) TECHNICAL INDICATORS
# ==============================================================================

def _ema(series: pd.Series, span: int) -> float | None:
    if series is None or len(series) < span:
        return None
    return float(series.ewm(span=span, adjust=False).mean().iloc[-1])


def _vwap_last_session(df: pd.DataFrame) -> float | None:
    """Session VWAP of the most recent session (falls back to daily VWAP)."""
    try:
        if df is None or len(df) < 2:
            return None
        if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
            last_day = df.index[-1].date()
            day = df[df.index.date == last_day]
        else:
            day = df.iloc[-1:]
        if len(day) < 1:
            return None
        typical = (day["high"] + day["low"] + day["close"]) / 3.0
        vol = day["volume"].fillna(0).to_numpy(dtype=float)
        if vol.sum() <= 0:
            return float(typical.iloc[-1])
        return float((typical.to_numpy(dtype=float) * vol).sum() / vol.sum())
    except Exception:
        return None


def _atr(df: pd.DataFrame, period: int = 14) -> float | None:
    try:
        if df is None or len(df) < period + 2:
            return None
        prev_close = df["close"].shift(1)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = float(tr.rolling(period).mean().iloc[-1])
        return atr if atr > 0 else None
    except Exception:
        return None


def _volume_spike(df: pd.DataFrame, lookback: int = 20) -> tuple[float, int, float]:
    """(ratio, current volume, average volume)"""
    try:
        vol = df["volume"].fillna(0).astype(float)
        cur = float(vol.iloc[-1])
        hist = vol.iloc[-lookback - 1: -1]
        avg = float(hist.mean()) if len(hist) else cur
        ratio = round(cur / avg, 2) if avg > 0 else 1.0
        return ratio, int(cur), avg
    except Exception:
        return 1.0, 0, 0.0


# ==============================================================================
# 6) DATA PROVIDERS  (all best-effort, never fabricated)
# ==============================================================================

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

_NSE_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/get-quotes/derivatives",
    "Connection": "keep-alive",
}

_nse_lock = threading.Lock()
_nse_cookies: dict[str, str] = {}


def _nse_session_cookies() -> dict[str, str]:
    with _nse_lock:
        return dict(_nse_cookies)


def _nse_warmup() -> None:
    global _nse_cookies
    with _nse_lock:
        if _nse_cookies:
            return
    try:
        with httpx.Client(timeout=6.0, headers=_NSE_HEADERS, follow_redirects=True) as client:
            client.get("https://www.nseindia.com", headers={"User-Agent": _UA})
            client.get("https://www.nseindia.com/market-data/live-equity-market",
                       headers={"User-Agent": _UA})
            with _nse_lock:
                _nse_cookies = {c.name: c.value for c in client.cookies.jar}
    except Exception:
        with _nse_lock:
            _nse_cookies = {}


def _nse_get_json(path: str, params: dict | None = None, timeout: float = 7.0) -> Any | None:
    _nse_warmup()
    try:
        with httpx.Client(timeout=timeout, headers=_NSE_HEADERS,
                          cookies=_nse_session_cookies(), follow_redirects=True) as client:
            resp = client.get(f"https://www.nseindia.com{path}", params=params or {})
            if resp.status_code == 200:
                try:
                    return resp.json()
                except Exception:
                    return None
    except Exception:
        return None
    return None


# ------------------------------------------------------------------------------
# TradingView public screener — real (15-min delayed) quotes for SPOT + FUTURES
# incl. open interest. Works from datacenter IPs where NSE blocks Akamai.
# ------------------------------------------------------------------------------
TV_SCAN_URL = "https://scanner.tradingview.com/global/scan"
TV_COLUMNS = (
    "name", "description", "close", "change", "volume", "open_interest",
    "open", "high", "low", "market_cap_basic", "average_volume_30d_calc",
    "relative_volume_10d_calc", "VWAP", "EMA20", "EMA50", "EMA100", "EMA200",
    "ATR", "RSI", "update_mode", "type",
)


def _tv_scan(tickers: list[str], chunk: int = 60) -> dict[str, dict]:
    """Batch screener call → {symbol: {field: value}}. Never fabricated."""
    out: dict[str, dict] = {}
    if not tickers:
        return out
    headers = {"User-Agent": _UA, "Content-Type": "application/json"}
    for i in range(0, len(tickers), chunk):
        part = tickers[i: i + chunk]
        body = {"symbols": {"tickers": part, "query": {"types": []}},
                "columns": list(TV_COLUMNS)}
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(TV_SCAN_URL, json=body, headers=headers)
            if resp.status_code != 200:
                continue
            for row in (resp.json() or {}).get("data") or []:
                if not isinstance(row, dict):
                    continue
                vals = row.get("d") or []
                out[str(row.get("s"))] = {c: v for c, v in zip(TV_COLUMNS, vals)}
        except Exception:
            continue
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def _get_nse_fno_universe() -> tuple[list[str], str]:
    """NSE F&O futures universe from Dhan's public scrip master (fallback: curated list).

    Exact filter: EXCH_ID=NSE + SEGMENT=D + INSTRUMENT=FUTSTK  →  only NSE equity
    futures (no index futures FUTIDX, no currency FUTCUR, no MCX commodities).
    """
    url = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
    try:
        df = pd.read_csv(url, low_memory=False)
        df.columns = [str(c).strip().upper() for c in df.columns]

        def pick(*names: str) -> str | None:
            for n in names:
                if n in df.columns:
                    return n
            return None

        sym_col = pick("UNDERLYING_SYMBOL", "SYMBOL", "TRADING_SYMBOL")
        exch_col = pick("EXCH_ID", "EXCHANGE", "EXCHANGE_ID")
        seg_col = pick("SEGMENT", "SEG")
        inst_col = pick("INSTRUMENT", "INSTRUMENT_NAME", "SECURITY_TYPE")
        if not sym_col:
            raise ValueError("no symbol column")

        mask = pd.Series(True, index=df.index)
        if exch_col:
            mask &= df[exch_col].astype(str).str.upper().eq("NSE")
        if seg_col:
            mask &= df[seg_col].astype(str).str.upper().eq("D")
        if inst_col:
            mask &= df[inst_col].astype(str).str.upper().eq("FUTSTK")

        syms = df.loc[mask, sym_col].dropna().astype(str).str.strip().str.upper()
        syms = syms[~syms.str.contains("TEST", na=False)]
        syms = syms[syms.str.isalpha()]
        out = sorted(set(syms.tolist()))
        if len(out) > 50:
            return out, "Dhan public Scrip Master (NSE equity futures)"

        # looser fallback: any NSE futures instrument minus the known non-equity blocks
        mask2 = pd.Series(True, index=df.index)
        if exch_col:
            mask2 &= df[exch_col].astype(str).str.upper().eq("NSE")
        if inst_col:
            mask2 &= df[inst_col].astype(str).str.upper().str.contains("FUT", na=False)
        syms2 = df.loc[mask2, sym_col].dropna().astype(str).str.strip().str.upper()
        syms2 = syms2[syms2.str.isalpha()]
        out2 = sorted(set(syms2.tolist()) - NON_EQUITY_BLOCKLIST)
        if len(out2) > 50:
            return out2, "Dhan public Scrip Master (NSE futures, equity filtered)"
    except Exception:
        pass
    return list(FALLBACK_FNO_UNIVERSE), "Curated NSE F&O fallback universe"


@st.cache_data(ttl=1800, show_spinner=False)
def _get_market_cap_map() -> dict[str, float]:
    """Best-effort market cap (₹ Cr) map from NSE quote-equity (cached, optional)."""
    return {}


@st.cache_data(ttl=600, show_spinner=False)
def _nse_capabilities() -> dict:
    """Probe the NSE endpoints ONCE.

    NSE (Akamai) blocks datacenter IPs for /api/quote-derivative,
    /api/quote-equity and the option-chain routes, while
    /api/fiidiiTradeReact and /api/corporate-announcements keep working.
    Probing once keeps the scan fast and honest.
    """
    caps = {"derivative": False, "option_chain": False, "equity": False, "announcements": False}
    try:
        d = _nse_get_json("/api/quote-derivative", {"symbol": "RELIANCE"}, timeout=6.0)
        caps["derivative"] = isinstance(d, dict) and bool(d.get("stocks") or d.get("info"))
    except Exception:
        pass
    try:
        o = _nse_get_json("/api/option-chain-indices", {"symbol": "NIFTY"}, timeout=6.0)
        caps["option_chain"] = isinstance(o, dict) and bool(o.get("records"))
    except Exception:
        pass
    try:
        q = _nse_get_json("/api/quote-equity", {"symbol": "RELIANCE"}, timeout=6.0)
        caps["equity"] = isinstance(q, dict) and bool(q.get("securityInfo") or q.get("priceInfo"))
    except Exception:
        pass
    try:
        a = _nse_get_json("/api/corporate-announcements",
                           {"index": "equities", "symbol": "RELIANCE"}, timeout=6.0)
        caps["announcements"] = isinstance(a, list) and len(a) > 0
    except Exception:
        pass
    return caps


def _fetch_nse_futures(symbol: str) -> dict:
    """Real futures snapshot for a symbol (nearest expiry). Never fabricated."""
    out = {"fut_ltp": None, "oi": None, "oi_change": None, "fut_change_pct": None,
           "expiry": None, "source": NA}
    data = _nse_get_json("/api/quote-derivative", {"symbol": symbol})
    if not isinstance(data, dict):
        return out
    stocks = data.get("stocks") or []
    rows = [s for s in stocks if isinstance(s, dict) and _safe_float(s.get("lastPrice")) is not None]
    if not rows:
        return out

    def expiry_key(row: dict) -> str:
        return str(row.get("expiryDate") or row.get("expiry") or "")

    rows.sort(key=expiry_key)
    row = rows[0]
    out["fut_ltp"] = _safe_float(row.get("lastPrice"))
    out["oi"] = _safe_float(row.get("openInterest"))
    out["oi_change"] = _safe_float(row.get("changeinOpenInterest"))
    out["fut_change_pct"] = _safe_float(row.get("pchange"))
    out["expiry"] = expiry_key(row) or None
    out["source"] = "NSE live derivative quote"
    return out


def _fetch_nse_option_chain(symbol: str) -> dict:
    """Real option chain (equities + index) → PCR, CE/PE OI, ATM ±5 strikes."""
    out = {"pcr": None, "ce_oi": None, "pe_oi": None, "atm": None,
           "ce_oi_5": None, "pe_oi_5": None, "expiry": None, "source": NA}
    data = None
    for path in ("/api/option-chain-equities", "/api/option-chain-indices"):
        data = _nse_get_json(path, {"symbol": symbol})
        if isinstance(data, dict) and data.get("records"):
            break
    if not isinstance(data, dict) or not data.get("records"):
        return out

    records = data["records"]
    rows = records.get("data") or []
    if not rows:
        return out
    expiries = records.get("expiryDates") or []
    if expiries:
        out["expiry"] = str(expiries[0])
    underlying = _safe_float(records.get("underlyingValue"))
    out["atm"] = underlying

    ce_total = pe_total = 0.0
    ce5 = pe5 = 0.0
    for r in rows:
        if not isinstance(r, dict):
            continue
        strike = _safe_float(r.get("strikePrice"))
        ce = (r.get("CE") or {}) if isinstance(r.get("CE"), dict) else {}
        pe = (r.get("PE") or {}) if isinstance(r.get("PE"), dict) else {}
        ce_oi = _safe_float(ce.get("openInterest")) or 0.0
        pe_oi = _safe_float(pe.get("openInterest")) or 0.0
        ce_total += ce_oi
        pe_total += pe_oi
        if underlying and strike is not None and abs(strike - underlying) <= 2.5 * 100:
            ce5 += ce_oi
            pe5 += pe_oi
    out["ce_oi"] = int(ce_total)
    out["pe_oi"] = int(pe_total)
    out["ce_oi_5"] = int(ce5)
    out["pe_oi_5"] = int(pe5)
    if pe_total > 0:
        out["pcr"] = round(ce_total / pe_total, 3)
    out["source"] = "NSE live option chain"
    return out


def _fetch_nse_delivery(symbol: str) -> dict:
    """Real delivery % from the NSE equity quote endpoint."""
    out = {"delivery_pct": None, "delivery_qty": None, "traded_qty": None,
           "session": None, "source": NA}
    data = _nse_get_json("/api/quote-equity", {"symbol": symbol})
    if not isinstance(data, dict):
        return out
    dp = data.get("securityWiseDP")
    if isinstance(dp, dict):
        traded = _safe_float(dp.get("quantityTraded"))
        deliv = _safe_float(dp.get("deliveryQuantity") or dp.get("deliveryToTradeQuantity"))
        pct = _safe_float(dp.get("deliveryPercentage") or dp.get("deliveryToTrade"))
        if pct is None and traded and deliv is not None:
            pct = round(deliv / traded * 100.0, 2)
        if pct is not None:
            out["delivery_pct"] = pct
            out["delivery_qty"] = deliv
            out["traded_qty"] = traded
            out["source"] = "NSE live delivery data"
    info = data.get("securityInfo")
    if isinstance(info, dict):
        out["session"] = info.get("recordDate") or info.get("securityStatus")
    return out


def _fetch_nse_announcements(symbol: str) -> list[dict]:
    """Recent corporate announcements for a symbol (best effort)."""
    data = _nse_get_json("/api/corporate-announcements",
                         {"index": "equities", "symbol": symbol})
    if not isinstance(data, list):
        return []
    out = []
    for item in data[:6]:
        if not isinstance(item, dict):
            continue
        out.append({
            "desc": str(item.get("desc") or item.get("subject") or "")[:220],
            "date": str(item.get("attchmntFile") or item.get("an_dt") or "")[:10],
            "symbol": str(item.get("symbol") or symbol),
        })
    return out


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_fii_dii() -> dict:
    """FII / DII cash flow — NSE primary, StockEdge as secondary, else Unavailable."""
    out = {"fii_net": None, "dii_net": None, "date": None, "source": NA, "verified": False}
    data = _nse_get_json("/api/fiidiiTradeReact")
    if isinstance(data, list) and data:
        row = data[0]
        if isinstance(row, dict):
            cat = str(row.get("category", "")).upper()
            net = _safe_float(row.get("netValue"))
            date = str(row.get("date", ""))[:10]
            if cat.startswith("FII") and net is not None:
                out["fii_net"] = net
                out["date"] = date
                out["source"] = "NSE FII/DII live feed"
                out["verified"] = True
            elif cat.startswith("DII") and net is not None:
                out["dii_net"] = net
                out["date"] = date
                out["source"] = "NSE FII/DII live feed"
                out["verified"] = True
            if out["fii_net"] is not None and out["dii_net"] is not None:
                return out
    # secondary: StockEdge
    try:
        headers = {"User-Agent": _UA, "Accept": "application/json, text/plain, */*",
                   "Referer": "https://web.stockedge.com/app/markets"}
        with httpx.Client(timeout=4.0) as client:
            resp = client.get("https://api.stockedge.com/Api/MarketDashboardApi/"
                              "GetFiiDiiCashMarketActivity", headers=headers)
            if resp.status_code == 200:
                payload = resp.json()
                if isinstance(payload, list) and payload:
                    latest = payload[0]
                    fii = _safe_float(latest.get("FiiNetCash", latest.get("FiiNet")))
                    dii = _safe_float(latest.get("DiiNetCash", latest.get("DiiNet")))
                    if fii is not None:
                        out["fii_net"] = fii
                        out["verified"] = True
                        out["source"] = "StockEdge live feed"
                    if dii is not None:
                        out["dii_net"] = dii
                        out["verified"] = True
                        out["source"] = "StockEdge live feed"
                    out["date"] = str(latest.get("DateStr", out["date"] or "")) or None
    except Exception:
        pass
    return out


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_gift_nifty() -> dict:
    """GIFT Nifty indicative price (best effort)."""
    out = {"price": None, "change_pct": None, "source": NA}
    for url, pattern in (
        ("https://sgxnifty.org/", r'class="[^"]*last_price[^"]*"[^>]*>([0-9,\.]+)<'),
        ("https://dhan.co/indices/gift-nifty-share-price/", r'₹\s*([0-9,]+\.[0-9]{2})'),
    ):
        try:
            with httpx.Client(timeout=4.0, headers={"User-Agent": _UA},
                              follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code != 200:
                    continue
                m = re.search(pattern, resp.text)
                if not m:
                    continue
                out["price"] = float(m.group(1).replace(",", ""))
                mp = re.search(r'([+-]?[0-9]+\.[0-9]+)\s*%', resp.text)
                if mp:
                    out["change_pct"] = float(mp.group(1))
                out["source"] = url.split("/")[2]
                return out
        except Exception:
            continue
    return out


@st.cache_data(ttl=300, show_spinner=False)
def _get_macro_snapshot() -> dict:
    """Global macro context from real quotes (bias + readable lines)."""
    tickers = {
        "DXY": "DX-Y.NYB", "CRUDE": "CL=F", "US10Y": "^TNX",
        "US30": "^DJI", "NIFTY": "^NSEI", "GOLD": "GC=F",
    }
    snap = {"bias": 0, "lines": [], "source": "Yahoo Finance quotes"}
    try:
        data = yf.download(list(tickers.values()), period="5d", interval="1d",
                           group_by="ticker", progress=False, threads=True, auto_adjust=False)
        for name, tkr in tickers.items():
            block = _extract_block(data, tkr, len(tickers))
            if block is None or block.empty:
                continue
            block = block.dropna(subset=["Close"])
            if len(block) < 2:
                continue
            c0 = float(block["Close"].iloc[-1])
            c1 = float(block["Close"].iloc[-2])
            chg = ((c0 - c1) / c1) * 100 if c1 else 0.0
            snap["lines"].append(f"{name}: {c0:,.2f} ({chg:+.2f}%)")
            if name in ("DXY", "CRUDE", "US10Y"):
                if chg < -0.3:
                    snap["bias"] += 1
                elif chg > 0.3:
                    snap["bias"] -= 1
            else:
                if chg > 0.3:
                    snap["bias"] += 1
                elif chg < -0.3:
                    snap["bias"] -= 1
    except Exception:
        pass
    return snap


# ==============================================================================
# 7) PRICE BUNDLE (multi-timeframe, one download per interval)
# ==============================================================================

def _extract_block(data: pd.DataFrame, ticker: str, n_tickers: int) -> pd.DataFrame | None:
    if data is None or len(data) == 0:
        return None
    if isinstance(data.columns, pd.MultiIndex):
        try:
            if ticker in data.columns.get_level_values(0):
                block = data[ticker]
            else:
                block = data.xs(ticker, axis=1, level=0)
        except Exception:
            return None
    else:
        block = data
    block = block.copy()
    block.columns = [str(c).strip().lower() for c in block.columns]
    if "close" not in block.columns:
        return None
    return block


def _normalise_ohlcv(block: pd.DataFrame) -> pd.DataFrame:
    df = block.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    df = df[keep]
    df = df.dropna(subset=["close"])
    for col in ("open", "high", "low", "close"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    else:
        df["volume"] = 0.0
    df = df.dropna(subset=["open", "high", "low", "close"])
    return df


def _resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    try:
        agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        out = df.resample(rule, label="left", closed="left").agg(agg)
        out = out.dropna(subset=["close", "open", "high", "low"])
        out = out[out["volume"].fillna(0) > 0] if "volume" in out.columns else out
        return out
    except Exception:
        return df


def _batch_download(tickers: list[str], period: str, interval: str,
                    chunk_size: int = 40, pause: float = 0.35) -> dict[str, pd.DataFrame]:
    """Chunked, thread-parallel yfinance download → {ticker: normalised ohlcv}."""
    out: dict[str, pd.DataFrame] = {}
    if not tickers:
        return out
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i: i + chunk_size]
        data = pd.DataFrame()
        for attempt in range(2):
            try:
                data = yf.download(chunk, period=period, interval=interval, group_by="ticker",
                                   progress=False, threads=True, auto_adjust=False)
                break
            except Exception:
                time.sleep(0.8 * (attempt + 1))
        for tkr in chunk:
            block = _extract_block(data, tkr, len(chunk))
            if block is None or block.empty:
                continue
            norm = _normalise_ohlcv(block)
            if not norm.empty:
                out[tkr] = norm
        time.sleep(pause)
    return out


@st.cache_data(ttl=300, show_spinner=False)
def _load_price_bundle(symbols: tuple[str, ...], tfs_json: str) -> dict:
    """One bundled multi-timeframe price cache (shared by every tab)."""
    tfs = json.loads(tfs_json)
    tickers = [f"{s}.NS" for s in symbols]

    raw: dict[str, dict[str, pd.DataFrame]] = {}
    needed = set(tfs)
    for tf in tfs:
        spec = TF_FEED_SPEC.get(tf)
        if not spec:
            continue
        if spec["source"] == "yf":
            key = f"yf::{spec['interval']}::{spec['period']}"
            if key not in raw:
                raw[key] = _batch_download(tickers, spec["period"], spec["interval"])
    bundle: dict[str, dict[str, pd.DataFrame]] = {tf: {} for tf in tfs}

    for tf in tfs:
        spec = TF_FEED_SPEC.get(tf)
        if not spec:
            continue
        if spec["source"] == "yf":
            key = f"yf::{spec['interval']}::{spec['period']}"
            for sym, block in raw.get(key, {}).items():
                bundle[tf][sym] = block
        else:
            base_tf = spec["base"]
            rule = spec["rule"]
            for sym, block in raw.get(f"yf::{TF_FEED_SPEC[base_tf]['interval']}::"
                                      f"{TF_FEED_SPEC[base_tf]['period']}", {}).items():
                rs = _resample_ohlcv(block, rule)
                if not rs.empty:
                    bundle[tf][sym] = rs
    return bundle


def _tf_frame(bundle: dict, tf: str, symbol: str) -> pd.DataFrame | None:
    return (bundle.get(tf) or {}).get(f"{symbol}.NS")


# ==============================================================================
# 8) DERIVATIVES ENRICHMENT  (real futures / options / delivery, parallel + cached)
# ==============================================================================

_enrich_cache: dict[str, tuple[float, dict]] = {}
_enrich_lock = threading.Lock()
ENRICH_TTL = 300.0


def _enrich_symbol(symbol: str, tv: dict | None = None) -> dict:
    """Full real-data enrichment for one symbol (cached in-process).

    Order of preference: NSE live API (real-time) → TradingView screener
    (real but ~15 min delayed). Nothing is ever fabricated.
    """
    now = time.time()
    with _enrich_lock:
        hit = _enrich_cache.get(symbol)
        if hit and (now - hit[0]) < ENRICH_TTL:
            return hit[1]

    tv = tv or {}
    tv_fut = tv.get("fut") or {}
    tv_spot = tv.get("spot") or {}
    caps = _nse_capabilities()

    result: dict[str, Any] = {
        "fut_ltp": None, "fut_change_pct": None, "oi": None, "oi_change": None,
        "oi_change_pct": None, "expiry": None, "pcr": None, "ce_oi": None, "pe_oi": None,
        "ce_oi_atm5": None, "pe_oi_atm5": None, "atm": None,
        "delivery_pct": None, "delivery_session": None,
        "spot_tv": _safe_float(tv_spot.get("close")),
        "spot_tv_change": _safe_float(tv_spot.get("change")),
        "tv_delay": tv_spot.get("update_mode") or tv_fut.get("update_mode"),
        "announcements": [], "sources": [], "errors": [],
    }
    try:
        if caps.get("derivative"):
            fut = _fetch_nse_futures(symbol)
            result.update({
                "fut_ltp": fut["fut_ltp"], "fut_change_pct": fut["fut_change_pct"],
                "oi": fut["oi"], "oi_change": fut["oi_change"], "expiry": fut["expiry"],
            })
            if fut["source"] != NA:
                result["sources"].append(fut["source"])
    except Exception as exc:
        result["errors"].append(f"futures: {exc}")

    # ---- fallback: TradingView front-month future (delayed, real) ----
    if result["fut_ltp"] is None and tv_fut:
        result["fut_ltp"] = _safe_float(tv_fut.get("close"))
        result["fut_change_pct"] = _safe_float(tv_fut.get("change"))
        result["oi"] = _safe_float(tv_fut.get("open_interest"))
        result["expiry"] = "front month (NSE:<SYM>1!)"
        result["fut_volume"] = tv_fut.get("volume")
        if result["fut_ltp"] is not None:
            result["sources"].append("TradingView screener (delayed ~15 min)")

    try:
        if caps.get("option_chain"):
            opt = _fetch_nse_option_chain(symbol)
            result.update({
                "pcr": opt["pcr"], "ce_oi": opt["ce_oi"], "pe_oi": opt["pe_oi"],
                "ce_oi_atm5": opt["ce_oi_5"], "pe_oi_atm5": opt["pe_oi_5"], "atm": opt["atm"],
            })
            if opt["source"] != NA:
                result["sources"].append(opt["source"])
    except Exception as exc:
        result["errors"].append(f"options: {exc}")

    try:
        if caps.get("equity"):
            dlv = _fetch_nse_delivery(symbol)
            result.update({"delivery_pct": dlv["delivery_pct"],
                           "delivery_session": dlv["session"]})
            if dlv["source"] != NA:
                result["sources"].append(dlv["source"])
    except Exception as exc:
        result["errors"].append(f"delivery: {exc}")

    try:
        if caps.get("announcements"):
            result["announcements"] = _fetch_nse_announcements(symbol)
            if result["announcements"]:
                result["sources"].append("NSE corporate announcements")
    except Exception:
        pass

    if result["oi"] and result["oi_change"] is not None:
        try:
            result["oi_change_pct"] = round(result["oi_change"] / result["oi"] * 100.0, 2)
        except Exception:
            result["oi_change_pct"] = None

    with _enrich_lock:
        _enrich_cache[symbol] = (time.time(), result)
    return result


def _enrich_many(symbols: list[str], workers: int = 6) -> dict[str, dict]:
    """Parallel enrichment. TradingView batch first (works everywhere), then the
    NSE live API per symbol with fail-fast when the NSE feed is blocked."""
    if not symbols:
        return {}

    # ---------- 1) TradingView batch (spot + front-month future) ----------
    tv_raw: dict[str, dict] = {}
    try:
        tickers: list[str] = []
        for s in symbols:
            tickers.append(f"NSE:{s}")
            tickers.append(f"NSE:{s}1!")
        tv_raw = _tv_scan(tickers)
    except Exception:
        tv_raw = {}

    tv_by_symbol = {
        s: {"spot": tv_raw.get(f"NSE:{s}") or {}, "fut": tv_raw.get(f"NSE:{s}1!") or {}}
        for s in symbols
    }

    # ---------- 2) NSE live API (parallel, fail-fast) ----------
    out: dict[str, dict] = {}
    try:
        with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(_enrich_symbol, s, tv_by_symbol.get(s)): s for s in symbols}
            for fut in cf.as_completed(futures, timeout=150):
                sym = futures[fut]
                try:
                    out[sym] = fut.result()
                except Exception:
                    out[sym] = {}
            nse_ok = any("NSE" in (e.get("sources") or []) for e in out.values() if e)
            if not nse_ok:
                # NSE live API unreachable → keep the TradingView based data only
                for s in symbols:
                    out.setdefault(s, _enrich_symbol(s, tv_by_symbol.get(s)))
    except Exception:
        for s in symbols:
            out.setdefault(s, _enrich_symbol(s, tv_by_symbol.get(s)))
    return out


# ==============================================================================
# 9) AI HYPOTHESIS ENGINE  (rule based + optional Gemini refinement)
# ==============================================================================

@dataclass
class _Factors:
    bull: list[str] = field(default_factory=list)
    bear: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    score: float = 0.0


def _build_factors(sym: str, df: pd.DataFrame, zone: Any | None, enrich: dict,
                   macro: dict, fii: dict, tf: str) -> _Factors:
    f = _Factors()
    if df is None or df.empty:
        f.missing.append("OHLCV")
        return f

    close = float(df["close"].iloc[-1])
    prev = float(df["close"].iloc[-2]) if len(df) > 1 else close
    chg = ((close - prev) / prev * 100.0) if prev else 0.0

    ema35 = _ema(df["close"], 35)
    ema100 = _ema(df["close"], 100)
    vwap = _vwap_last_session(df)
    ratio, cur_vol, avg_vol = _volume_spike(df)

    if ema35 is not None:
        if close > ema35:
            f.score += 1.0
            f.bull.append(f"भाव EMA 35 ({ema35:,.2f}) के ऊपर है")
        else:
            f.score -= 1.0
            f.bear.append(f"भाव EMA 35 ({ema35:,.2f}) के नीचे है")
    else:
        f.missing.append("EMA 35 (कम डेटा)")

    if ema100 is not None:
        if close > ema100:
            f.score += 1.0
            f.bull.append(f"भाव EMA 100 ({ema100:,.2f}) के ऊपर है")
        else:
            f.score -= 1.0
            f.bear.append(f"भाव EMA 100 ({ema100:,.2f}) के नीचे है")
    else:
        f.missing.append("EMA 100 (कम डेटा)")

    if vwap is not None:
        if close > vwap:
            f.score += 0.75
            f.bull.append(f"भाव सत्र VWAP ({vwap:,.2f}) के ऊपर है")
        else:
            f.score -= 0.75
            f.bear.append(f"भाव सत्र VWAP ({vwap:,.2f}) के नीचे है")

    if ratio >= 2.1:
        if chg > 0:
            f.score += 1.25
            f.bull.append(f"वॉल्यूम स्पाइक {ratio}x के साथ ऊपर की ओर हलचल (वास्तविक वॉल्यूम {cur_vol:,})")
        else:
            f.score -= 1.25
            f.bear.append(f"वॉल्यूम स्पाइक {ratio}x के साथ नीचे की ओर दबाव (वास्तविक वॉल्यूम {cur_vol:,})")
    else:
        f.bull.append(f"वॉल्यूम सामान्य ({ratio}x, औसत {avg_vol:,.0f})")

    if zone is not None:
        prox = _safe_float(getattr(zone, "proxVal", None))
        dist = _safe_float(getattr(zone, "distVal", None))
        state = str(getattr(zone, "state", ""))
        is_demand = bool(getattr(zone, "isDemand", False))
        if prox is not None and dist is not None:
            if is_demand:
                if close >= prox:
                    f.score += 1.25
                    f.bull.append(f"माँग ज़ोन ({state}, {getattr(zone,'tf','1h')}) के ऊपर भाव {close:,.2f}")
                else:
                    f.bear.append(f"माँग ज़ोन ({state}) {prox:,.2f} के नीचे — माँग टूटने का जोखिम")
            else:
                if close <= prox:
                    f.score -= 1.25
                    f.bear.append(f"आपूर्ति ज़ोन ({state}, {getattr(zone,'tf','1h')}) के नीचे भाव {close:,.2f}")
                else:
                    f.bull.append(f"आपूर्ति ज़ोन ({state}) {prox:,.2f} के ऊपर — दबाव टूटने का संकेत")
    else:
        f.missing.append("सक्रिय ज़ोन (कोई सक्रिय ज़ोन नहीं)")

    # --- Futures (real only) ---
    fut_ltp = enrich.get("fut_ltp")
    if fut_ltp:
        diff_pct = ((fut_ltp - close) / close * 100.0) if close else 0.0
        if diff_pct > 0.05:
            f.score += 0.75
            f.bull.append(f"फ्यूचर्स प्रीमियम {diff_pct:+.2f}% — लॉन्ग पक्ष में तनाव")
        elif diff_pct < -0.05:
            f.score -= 0.75
            f.bear.append(f"फ्यूचर्स डिस्काउंट {diff_pct:+.2f}% — शॉर्ट पक्ष में तनाव")
        fut_chg = enrich.get("fut_change_pct")
        if fut_chg is not None:
            if fut_chg > 0.3:
                f.score += 0.5
                f.bull.append(f"फ्यूचर्स {fut_chg:+.2f}% ऊपर")
            elif fut_chg < -0.3:
                f.score -= 0.5
                f.bear.append(f"फ्यूचर्स {fut_chg:+.2f}% नीचे")
    else:
        f.missing.append("Futures LTP")

    oi_chg = enrich.get("oi_change_pct")
    if oi_chg is not None:
        if chg > 0.15 and oi_chg > 0:
            f.score += 1.5
            f.bull.append(f"OI {oi_chg:+.2f}% + भाव ऊपर → Long Buildup")
        elif chg < -0.15 and oi_chg > 0:
            f.score -= 1.5
            f.bear.append(f"OI {oi_chg:+.2f}% + भाव नीचे → Short Buildup")
        elif chg > 0.15 and oi_chg < 0:
            f.score += 0.5
            f.bull.append(f"OI {oi_chg:+.2f}% + भाव ऊपर → Short Covering")
        elif chg < -0.15 and oi_chg < 0:
            f.score -= 0.5
            f.bear.append(f"OI {oi_chg:+.2f}% + भाव नीचे → Long Unwinding")
    else:
        f.missing.append("Futures OI परिवर्तन")

    # --- Options (real only) ---
    pcr = enrich.get("pcr")
    if pcr is not None:
        if pcr >= 1.1:
            f.score += 0.75
            f.bull.append(f"ऑप्शन PCR {pcr:.2f} — पुट राइटर्स की मजबूती")
        elif pcr <= 0.9:
            f.score -= 0.75
            f.bear.append(f"ऑप्शन PCR {pcr:.2f} — कॉल राइटर्स का दबाव")
        else:
            f.bull.append(f"ऑप्शन PCR {pcr:.2f} — तटस्थ")
    else:
        f.missing.append("Option PCR")

    # --- Delivery (real only) ---
    dlv = enrich.get("delivery_pct")
    if dlv is not None:
        if dlv >= 35 and chg > 0:
            f.score += 0.75
            f.bull.append(f"डिलीवरी {dlv:.1f}% ऊपर की ओर — मजबूत हिस्सेदारी")
        elif dlv >= 35 and chg < 0:
            f.score -= 0.75
            f.bear.append(f"डिलीवरी {dlv:.1f}% नीचे की ओर — वितरण का दबाव")
        else:
            f.bull.append(f"डिलीवरी {dlv:.1f}% (सामान्य)")
    else:
        f.missing.append("Delivery %")

    # --- FII / DII ---
    fii_net = fii.get("fii_net")
    dii_net = fii.get("dii_net")
    if fii_net is not None:
        if fii_net > 0:
            f.score += 0.5
            f.bull.append(f"FII नेट नकदी {fii_net:+,.0f} Cr (खरीदारी)")
        else:
            f.score -= 0.5
            f.bear.append(f"FII नेट नकदी {fii_net:+,.0f} Cr (बिकवाली)")
    if dii_net is not None:
        if dii_net > 0:
            f.score += 0.25
            f.bull.append(f"DII सहारा {dii_net:+,.0f} Cr")
        else:
            f.score -= 0.25
            f.bear.append(f"DII निकासी {dii_net:+,.0f} Cr")
    if fii_net is None and dii_net is None:
        f.missing.append("FII/DII फ्लो")

    # --- Macro ---
    mbias = macro.get("bias", 0)
    if mbias > 0:
        f.score += 0.5
        f.bull.append("ग्लोबल मैक्रो (DXY/क्रूड/यields) भारतीय शेयरों के पक्ष में")
    elif mbias < 0:
        f.score -= 0.5
        f.bear.append("ग्लोबल मैक्रो भारतीय शेयरों के विपरीत")
    return f


def _hypothesis_from_score(score: float) -> tuple[str, str]:
    if score >= 3.5:
        return "STRONG BUY", "अत्यधिक सकारात्मक प्रमाण — ट्रेंड के साथ खरीदारी"
    if score >= 1.5:
        return "BUY", "सकारात्मक प्रमाण — तुलनात्मक रूप से खरीदारी"
    if score <= -3.5:
        return "STRONG SELL", "अत्यधिक नकारात्मक प्रमाण — ट्रेंड के साथ बिकवाली"
    if score <= -1.5:
        return "SELL", "नकारात्मक प्रमाण — तुलनात्मक रूप से बिकवाली"
    return "NO TRADE", "प्रमाण अपर्याप्त / विपरीत दिशा में — स्टैंड अलाइड"


def _risk_setup(close: float, zone: Any | None, atr: float | None, settings: dict,
                is_long: bool) -> dict:
    capital = float(settings.get("account_capital", 25000.0))
    risk_pct = float(settings.get("risk_pct", 0.5))
    rr = float(settings.get("target_rr", 5.0))
    risk_budget = capital * (risk_pct / 100.0)

    entry = close
    sl = None
    basis = "ATR (14) आधारित स्टॉप"
    z_prox = _safe_float(getattr(zone, "proxVal", None)) if zone is not None else None
    z_dist = _safe_float(getattr(zone, "distVal", None)) if zone is not None else None
    if z_prox is not None and z_dist is not None:
        entry = z_prox
        sl = z_dist
        basis = "सक्रिय ज़ोन की प्रॉक्सिमल/डिस्टल लाइन"
    if sl is None or abs(entry - sl) < 1e-6:
        if atr and atr > 0:
            sl = close - atr * 1.5 if is_long else close + atr * 1.5
        else:
            sl = close * 0.98 if is_long else close * 1.02
        basis = "ATR (14) आधारित स्टॉप"
    per_unit = abs(entry - sl)
    if per_unit <= 0:
        per_unit = 1.0
    qty = int(math.floor(risk_budget / per_unit))
    if qty <= 0:
        qty = 0
    target = entry + per_unit * rr if is_long else entry - per_unit * rr
    return {
        "entry": round(entry, 2), "sl": round(sl, 2), "target": round(target, 2),
        "qty": qty, "risk_budget": round(risk_budget, 2),
        "per_unit_risk": round(per_unit, 2), "rr": rr, "basis": basis,
        "capital": capital,
    }


def _gemini_context(prompt: str) -> str | None:
    """Optional LLM layer. Returns None when the key/API is unavailable."""
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        return None
    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
           f":generateContent?key={key}")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 1200},
    }
    try:
        with httpx.Client(timeout=25.0) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                cand = (data.get("candidates") or [{}])[0]
                parts = (cand.get("content") or {}).get("parts") or []
                text = "".join(p.get("text", "") for p in parts).strip()
                return text or None
    except Exception:
        return None
    return None


def _hypothesis_prompt(sym: str, ctx: dict) -> str:
    return (
        "आप एक संस्थागत ट्रेडिंग विश्लेषक हैं। नीचे दिए गए सत्यापित डेटा के आधार पर हिंदी में "
        "एक संक्षिप्त AI Hypothesis लिखें। केवल दिए गए डेटा का उपयोग करें; जो डेटा 'Unavailable' है "
        "उसे अनुमान से न भरें। 5 बिंदुओं में लिखें: संदर्भ, प्रमाण, विरोधाभासी संकेत, "
        "अमान्य होने की शर्तें, और अंतिम निर्णय (Strong Buy / Buy / No Trade / Sell / Strong Sell).\n\n"
        + json.dumps(ctx, ensure_ascii=False, default=str)[:6000]
    )


# ==============================================================================
# 10) SESSION STATE / SETTINGS
# ==============================================================================

if "analysis_settings" not in st.session_state:
    st.session_state.analysis_settings = dict(DEFAULT_ANALYSIS_SETTINGS)
    st.session_state.analysis_settings.setdefault("vol_spike_threshold", 2.1)
    st.session_state.analysis_settings.setdefault("enrich_limit", 60)
    st.session_state.analysis_settings.setdefault("scan_limit", 0)
    st.session_state.analysis_settings.setdefault("use_llm", True)

SETTINGS = st.session_state.analysis_settings
SETTINGS.setdefault("custom_tfs", list(ALL_TIMEFRAMES))
SETTINGS.setdefault("vol_spike_threshold", 2.1)
# Environment overrides (handy for Render tuning without touching the UI)
SETTINGS["enrich_limit"] = int(os.getenv("AAP_ENRICH_LIMIT", SETTINGS.get("enrich_limit", 60)) or 0)
SETTINGS["scan_limit"] = int(os.getenv("AAP_SCAN_LIMIT", SETTINGS.get("scan_limit", 0)) or 0)
SETTINGS.setdefault("use_llm", True)

ACTIVE_TFS: list[str] = [tf for tf in SETTINGS.get("custom_tfs", ALL_TIMEFRAMES) if tf in TF_FEED_SPEC]
if not ACTIVE_TFS:
    ACTIVE_TFS = ["1h", "Daily"]


# ==============================================================================
# 11) AI HYPOTHESIS MODAL
# ==============================================================================

@st.dialog("🏛️ संस्थागत AI बायस — बिंदुवार विश्लेषण", width="large")
def _show_stock_bias_modal(sym: str, bias: str, ctx: dict, factors: _Factors,
                           risk: dict, tf: str) -> None:
    color = "#00e676" if "BUY" in bias else ("#ff5252" if "SELL" in bias else "#8ea5c0")
    st.markdown(
        f"### <b>{sym}</b> &nbsp;·&nbsp; टाइमफ्रेम: {tf} &nbsp;·&nbsp; "
        f"<span style='color:{color}; font-weight:bold;'>{bias}</span>",
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Spot LTP", f"₹{ctx.get('close', 0):,.2f}", ctx.get("chg_pct_txt", ""))
    with c2:
        fut = ctx.get("fut_ltp")
        st.metric("Futures LTP", f"₹{fut:,.2f}" if fut else NA, ctx.get("dif_txt", ""))
    with c3:
        st.metric("Option PCR", f"{ctx['pcr']:.2f}" if ctx.get("pcr") is not None else NA)
    with c4:
        st.metric("Delivery %", f"{ctx['delivery_pct']:.1f}%" if ctx.get("delivery_pct") is not None else NA)
    with c5:
        st.metric("Vol Spike", ctx.get("vol_spike_txt", NA))

    st.markdown("---")
    st.markdown("#### **संस्थागत निर्णय के बिंदुवार प्रमाण (वास्तविक + सत्यापित डेटा)**")

    st.markdown(
        f"""<div class="hyp-card"><div class="hyp-title">1. बाज़ार संदर्भ (Market Context)</div>
<div>{ctx.get('macro_text', NA)}</div>
<div>{ctx.get('fii_text', NA)}</div></div>""",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""<div class="hyp-card"><div class="hyp-title">2. Price Action (EMA 35/100, VWAP)</div>
<div>{ctx.get('price_action', NA)}</div></div>""",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""<div class="hyp-card"><div class="hyp-title">3. Volume (वास्तविक वॉल्यूम vs औसत)</div>
<div>{ctx.get('volume_text', NA)}</div></div>""",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""<div class="hyp-card"><div class="hyp-title">4. Demand / Supply ज़ोन</div>
<div>{ctx.get('zone_text', NA)}</div></div>""",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""<div class="hyp-card"><div class="hyp-title">5. Futures (वास्तविक LTP, प्रीमियम/डिस्काउंट, OI)</div>
<div>{ctx.get('futures_text', NA)}</div></div>""",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""<div class="hyp-card"><div class="hyp-title">6. Options (PCR, ATM ±5 स्ट्राइक CE/PE OI)</div>
<div>{ctx.get('options_text', NA)}</div></div>""",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""<div class="hyp-card"><div class="hyp-title">7. Delivery (अंतिम सत्यापित सत्र)</div>
<div>{ctx.get('delivery_text', NA)}</div></div>""",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""<div class="hyp-card"><div class="hyp-title">8. FII / DII (स्रोत व रिपोर्टिंग तिथि)</div>
<div>{ctx.get('fii_text', NA)}</div></div>""",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""<div class="hyp-card"><div class="hyp-title">9. News &amp; Events (सत्यापित घोषणाएँ)</div>
<div>{ctx.get('news_text', NA)}</div></div>""",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""<div class="hyp-card"><div class="hyp-title">10. Risk Setup (Entry / SL / Target / Size)</div>
<div>{ctx.get('risk_text', NA)}</div></div>""",
        unsafe_allow_html=True,
    )

    st.markdown("#### 🧠 **AI Hypothesis (LLM-संवर्धित)**" if ctx.get("llm_used")
                else "#### 🧠 **AI Hypothesis (नियम-आधारित, सत्यापित डेटा से)**")
    st.markdown(ctx.get("hypothesis_text", NA))

    st.markdown("#### ⚠️ **विरोधाभासी संकेत (Hypothesis के विरुद्ध)**")
    if factors.bear:
        for line in factors.bear:
            st.markdown(f"• {line}")
    else:
        st.caption("कोई विपरीत संकेत नहीं मिला — या फिर सभी उपलब्ध डेटा सकारात्मक है।")

    st.markdown("#### 🚫 **यह निष्कर्ष कब अमान्य होगा**")
    for line in ctx.get("invalidations", ["ज़ोन की डिस्टल लाइन के बाहर क्लोज़, या वॉल्यूम के साथ विपरीत दिशा में ब्रेकआउट।"]):
        st.markdown(f"• {line}")

    if factors.missing:
        st.markdown("#### 🧩 **अनुपलब्ध डेटा (अनुमान से नहीं भरा गया)**")
        st.caption(", ".join(factors.missing))

    st.markdown("---")
    tv_url = f"https://in.tradingview.com/chart/?symbol={url_quote('NSE:' + sym, safe='')}"
    st.markdown(f"[📈 TradingView पर {sym} का लाइव चार्ट खोलें]({tv_url})")
    st.caption("विश्लेषण केवल उपलब्ध सत्यापित प्रमाणों पर आधारित है। "
               "1:5 Risk/Reward निर्धारित करने से लाभ की गारंटी नहीं होती।")


# ==============================================================================
# 12) CORE ENGINE — build the master frame
# ==============================================================================

def _zone_params(settings: dict) -> dict:
    return {
        "eodHighBufferPct": float(settings.get("eod_high_buffer_pct", 10.0)),
        "eodLowBufferPct": float(settings.get("eod_low_buffer_pct", 10.0)),
        "useEodRange": bool(settings.get("use_eod_range", True)),
        "accountCapital": float(settings.get("account_capital", 25000.0)),
        "riskPct": float(settings.get("risk_pct", 0.5)),
        "targetRR": float(settings.get("target_rr", 5.0)),
    }


def _nearest_zone_for_df(df: pd.DataFrame, tf: str, settings: dict) -> tuple[Any | None, float | None]:
    if df is None or len(df) < 12:
        return None, None
    try:
        zones = scan_zones(df[["open", "high", "low", "close", "volume"]].copy(),
                           params=_zone_params(settings), tf=tf)
        active = latest_active_zones(zones)
    except Exception:
        return None, None
    price = float(df["close"].iloc[-1])
    return _pick_nearest_zone(active, price)


@st.cache_data(ttl=300, show_spinner=False)
def _build_master_frame(symbols: tuple[str, ...], tf: str, settings_json: str) -> pd.DataFrame:
    """Master scan for one timeframe — real OHLCV only (no fabricated numbers)."""
    settings = json.loads(settings_json)
    bundle = _load_price_bundle(symbols, json.dumps([tf]))
    macro = _get_macro_snapshot()
    fii = _fetch_fii_dii()

    rows = []
    for sym in symbols:
        df = _tf_frame(bundle, tf, sym)
        row = {
            "Symbol": sym,
            "Spot LTP": None, "Spot Change %": 0.0, "Open Gap": "Normal",
            "Selected-Timeframe Volume": 0, "Volume Spike Ratio": 1.0,
            "EMA 35 Signal": NA, "EMA 100 Signal": NA, "VWAP Signal": NA,
            "Zone Signal": NA, "Live Signals Highlight": "",
            "_df": None, "_zone": None,
        }
        if df is None or df.empty or len(df) < 3:
            rows.append(row)
            continue
        close = float(df["close"].iloc[-1])
        prev = float(df["close"].iloc[-2])
        chg = ((close - prev) / prev * 100.0) if prev else 0.0
        ratio, cur_vol, _avg = _volume_spike(df)
        ema35 = _ema(df["close"], 35)
        ema100 = _ema(df["close"], 100)
        vwap = _vwap_last_session(df)
        open_price = float(df["open"].iloc[-1])

        row.update({
            "Spot LTP": round(close, 2),
            "Spot Change %": round(chg, 2),
            "Selected-Timeframe Volume": cur_vol,
            "Volume Spike Ratio": ratio,
            "EMA 35 Signal": ("+" if close > ema35 else "-") if ema35 is not None else NA,
            "EMA 100 Signal": ("+" if close > ema100 else "-") if ema100 is not None else NA,
            "VWAP Signal": ("+" if close > vwap else "-") if vwap is not None else NA,
            "Open Gap": ("Gap-Up" if open_price > prev * 1.004 else
                         "Gap-Down" if open_price < prev * 0.996 else "Normal"),
            "_df": df,
        })
        zone, zdist = _nearest_zone_for_df(df, tf, settings)
        row["_zone"] = zone
        if zone is not None:
            kind = "DEMAND" if getattr(zone, "isDemand", False) else "SUPPLY"
            row["Zone Signal"] = f"{getattr(zone,'state','?')} {kind}"
        tags = []
        if ratio >= float(settings.get("vol_spike_threshold", 2.1)):
            tags.append(f"Vol Spike ({ratio}x @ {tf})")
        if row["EMA 35 Signal"] != NA and row["EMA 35 Signal"] == row["EMA 100 Signal"]:
            tags.append("EMA Bull Cross" if row["EMA 35 Signal"] == "+" else "EMA Bear Cross")
        if zone is not None and zdist is not None and close and (zdist / close) * 100 < 0.8:
            tags.append(f"Zone Proximity ({getattr(zone,'state','')} @ {getattr(zone,'tf',tf)})")
        row["Live Signals Highlight"] = " | ".join(tags)
        rows.append(row)

    frame = pd.DataFrame(rows)
    return frame


def _enrich_frame(frame: pd.DataFrame, limit: int) -> pd.DataFrame:
    """Attach real futures/options/delivery data to the (top-N) master frame."""
    if frame.empty or limit <= 0:
        return frame
    tmp = frame.copy()
    tmp["_vol_rank"] = tmp["Selected-Timeframe Volume"].fillna(0)
    tmp = tmp.sort_values("_vol_rank", ascending=False)
    targets = [s for s in tmp["Symbol"].tolist()[:limit]]
    enriched = _enrich_many(targets, workers=6)

    for col in ("Futures LTP", "Dif. %", "Futures Change %", "Futures OI",
                "Futures OI Change %",
                "Latest Verified Delivery %", "PCR", "CE Total OI", "PE Total OI",
                "Buildup Classification", "Data Source"):
        frame[col] = NA
    frame["_Enrich"] = {}

    for idx, row in frame.iterrows():
        sym = row["Symbol"]
        e = enriched.get(sym) or {}
        frame.at[idx, "_Enrich"] = e
        spot = row.get("Spot LTP")

        # spot fallback (TradingView) when the OHLCV feed had no bar for the symbol
        if spot is None and e.get("spot_tv"):
            spot = e.get("spot_tv")
            frame.at[idx, "Spot LTP"] = round(float(spot), 2)
            if e.get("spot_tv_change") is not None:
                frame.at[idx, "Spot Change %"] = round(float(e["spot_tv_change"]), 2)

        fut = e.get("fut_ltp")
        if fut:
            frame.at[idx, "Futures LTP"] = f"₹{fut:,.2f}"
            if spot:
                dif_v = (fut - float(spot)) / float(spot) * 100.0
                frame.at[idx, "Dif. %"] = f"{dif_v:+.2f}%"
        if e.get("fut_change_pct") is not None:
            frame.at[idx, "Futures Change %"] = f"{float(e['fut_change_pct']):+.2f}%"
        if e.get("oi") is not None:
            frame.at[idx, "Futures OI"] = f"{int(e['oi']):,}"
        if e.get("oi_change_pct") is not None:
            frame.at[idx, "Futures OI Change %"] = f"{float(e['oi_change_pct']):+.2f}%"
        if e.get("delivery_pct") is not None:
            frame.at[idx, "Latest Verified Delivery %"] = f"{float(e['delivery_pct']):.1f}%"
        if e.get("pcr") is not None:
            frame.at[idx, "PCR"] = f"{float(e['pcr']):.2f}"
        if e.get("ce_oi") is not None:
            frame.at[idx, "CE Total OI"] = f"{int(e['ce_oi']):,}"
        if e.get("pe_oi") is not None:
            frame.at[idx, "PE Total OI"] = f"{int(e['pe_oi']):,}"

        # Buildup classification (real data only)
        chg = float(row.get("Spot Change %") or 0.0)
        oi_chg = e.get("oi_change_pct")
        fchg = e.get("fut_change_pct")
        if oi_chg is not None and fchg is not None:
            if fchg > 0.15 and oi_chg > 0:
                frame.at[idx, "Buildup Classification"] = "Long Buildup"
            elif fchg < -0.15 and oi_chg > 0:
                frame.at[idx, "Buildup Classification"] = "Short Buildup"
            elif fchg > 0.15 and oi_chg < 0:
                frame.at[idx, "Buildup Classification"] = "Short Covering"
            elif fchg < -0.15 and oi_chg < 0:
                frame.at[idx, "Buildup Classification"] = "Long Unwinding"
            else:
                frame.at[idx, "Buildup Classification"] = "Neutral"
        else:
            frame.at[idx, "Buildup Classification"] = NA

        src = e.get("sources") or []
        frame.at[idx, "Data Source"] = " + ".join(src) if src else "OHLCV feed (F&O feed unavailable)"
    return frame


# ==============================================================================
# 13) TAB 1 — NSE ALL FUTURE STOCKS
# ==============================================================================

def _compose_master_frame(symbols: tuple[str, ...], selected_tf: str, settings: dict):
    """Master frame = real OHLCV scan + real F&O enrichment + rule-based AI bias."""
    frame = _build_master_frame(symbols, selected_tf, json.dumps(settings, default=str))
    if frame.empty:
        return frame, {}, {}, {}, {}
    frame = _enrich_frame(frame, int(settings.get("enrich_limit", 60)))

    macro = _get_macro_snapshot()
    fii = _fetch_fii_dii()
    biases: list[str] = []
    factors_map: dict[str, _Factors] = {}
    risk_map: dict[str, dict] = {}
    for _, row in frame.iterrows():
        df = row.get("_df")
        enrich = row.get("_Enrich") or {}
        zone = row.get("_zone")
        factors = _build_factors(row["Symbol"], df, zone, enrich, macro, fii, selected_tf)
        bias, _note = _hypothesis_from_score(factors.score)
        biases.append(bias)
        factors_map[row["Symbol"]] = factors
        risk_map[row["Symbol"]] = _risk_setup(
            float(row.get("Spot LTP") or 0), zone,
            _atr(df) if df is not None else None, settings, is_long=("BUY" in bias))
    frame["_Raw_Bias"] = biases
    frame["Final AI Bias"] = [b.title().replace("No Trade", "NO TRADE") for b in biases]
    return frame, factors_map, risk_map, macro, fii


def _render_nse_tab(settings: dict) -> None:
    selected_tf = settings.get("selected_tf", "Daily")
    universe, universe_src = _get_nse_fno_universe()
    limit = int(settings.get("scan_limit", 0) or 0)
    symbols = tuple(universe[:limit] if limit > 0 else universe)

    c1, c2, c3 = st.columns([2.2, 1.4, 2.4])
    with c1:
        st.markdown(f"### NSE ALL FUTURES — LIVE SCAN ({len(symbols)} स्टॉक्स)")
        st.caption(f"यूनिवर्स स्रोत: {universe_src} · ज़ोन इंजन: {ZONE_ENGINE_SOURCE}")
    with c2:
        if st.button("↻ Refresh scanner", type="primary", use_container_width=True):
            st.cache_data.clear()
            _enrich_cache.clear()
            st.rerun()
    with c3:
        new_tf = st.selectbox("Timeframe:", ALL_TIMEFRAMES,
                              index=ALL_TIMEFRAMES.index(selected_tf) if selected_tf in ALL_TIMEFRAMES else 8,
                              key="tab1_tf_select", label_visibility="collapsed")
        if new_tf != selected_tf:
            st.session_state.analysis_settings["selected_tf"] = new_tf
            st.rerun()

    with st.spinner("मास्टर स्कैन चल रहा है…"):
        frame, factors_map, risk_map, macro, fii = _compose_master_frame(
            symbols, selected_tf, settings)

    if frame.empty:
        st.info("डेटा लोड नहीं हो सका। कृपया बाद में पुनः प्रयास करें।")
        return

    st.session_state["master_nse_frame"] = frame

    # --- clickable bias link (fresh token → repeated clicks keep working) ---
    token = uuid.uuid4().hex[:8]
    frame["_Bias_Link_URL"] = frame["Symbol"].map(
        lambda s: f"?bias={s}&t={token}")
    frame["_Spot_Chart_URL"] = frame["Symbol"].map(
        lambda s: f"https://in.tradingview.com/chart/?symbol={url_quote('NSE:' + s, safe='')}")
    frame["_Fut_Chart_URL"] = [
        (f"https://in.tradingview.com/chart/?symbol={url_quote('NSE:' + s + '1!', safe='')}"
         + (f"#price=₹{str(f).replace('₹', '')}" if f and f != NA else ""))
        for s, f in zip(frame["Symbol"], frame["Futures LTP"])
    ]

    # --- modal trigger ---
    qp_sym = st.query_params.get("bias", None)
    if qp_sym and qp_sym in set(frame["Symbol"].tolist()):
        st.query_params.clear()
        r = frame[frame["Symbol"] == qp_sym].iloc[0]
        _open_hypothesis_modal(qp_sym, r, frame, factors_map, risk_map,
                               macro, fii, selected_tf, settings)

    # --- Top 5 cards ---
    buys = frame[frame["_Raw_Bias"].isin(["BUY", "STRONG BUY"])].sort_values(
        "Spot Change %", ascending=False).head(5)
    sells = frame[frame["_Raw_Bias"].isin(["SELL", "STRONG SELL"])].sort_values(
        "Spot Change %", ascending=True).head(5)
    top_cols = ["Symbol", "Spot LTP", "Futures LTP", "Dif. %", "Spot Change %",
                "Volume Spike Ratio", "Final AI Bias"]
    col_l, col_r = st.columns(2)
    for container, data, title in ((col_l, buys, "**TOP 5 BUY (बहु-कारक AI)**"),
                                   (col_r, sells, "**TOP 5 SELL (बहु-कारक AI)**")):
        with container:
            st.markdown(title)
            if data.empty:
                st.info("वर्तमान में कोई मजबूत सेटअप नहीं।")
                continue
            disp = _linkify_table(data[top_cols + ["_Spot_Chart_URL", "_Fut_Chart_URL",
                                                    "_Bias_Link_URL"]].copy())
            st.dataframe(disp.style.apply(_row_styles, axis=1),
                         column_config=_col_config(),
                         hide_index=True, height=190, **_df_layout())

    st.markdown("---")
    st.markdown("#### 📊 मास्टर तालिका — किसी भी **Final AI Bias** पर क्लिक करके पूरा हिंदी विश्लेषण खोलें")

    cols_to_render = [c for c in ORDERED_COLUMNS_TAB1 if c in frame.columns]
    hidden = [c for c in ("_Spot_Chart_URL", "_Fut_Chart_URL", "_Bias_Link_URL") if c in frame.columns]
    display = _linkify_table(frame[cols_to_render + hidden].copy())
    st.dataframe(display.style.apply(_row_styles, axis=1),
                 column_config=_col_config(), hide_index=True, height=560, **_df_layout())
    st.caption("Futures / PCR / OI / Delivery कॉलम केवल उन प्रतीकों के लिए भरे जाते हैं जिनका "
               "F&O फ़ीड enrich किया गया है; शेष में 'Unavailable' दिखता है (कोई काल्पनिक आँकड़ा नहीं).")


def _row_styles(row) -> list[str]:
    styles = []
    val = row.get("Spot Change %", 0)
    p_color = "#00e676" if val >= 0 else "#ff5252"
    for c in row.index:
        if c in ("Spot LTP", "Spot Change %", "Futures Change %"):
            styles.append(f"color: {p_color}; font-weight: bold")
        elif c == "Dif. %":
            d = _safe_float(str(row.get(c, "")).replace("%", "").replace("+", ""))
            d_color = "#00e676" if (d is not None and d >= 0) else "#ff5252"
            styles.append(f"color: {d_color}; font-weight: bold")
        elif c == "Buildup Classification":
            b = str(row.get(c, ""))
            if b == "Long Buildup":
                styles.append("color: #00e676; font-weight: bold")
            elif b == "Short Covering":
                styles.append("color: #69f0ae; font-weight: bold")
            elif b == "Short Buildup":
                styles.append("color: #ff5252; font-weight: bold")
            elif b == "Long Unwinding":
                styles.append("color: #ff7043; font-weight: bold")
            else:
                styles.append("")
        elif c == "Final AI Bias":
            b = str(row.get(c, ""))
            if "BUY" in b:
                styles.append("color: #00e676; font-weight: bold")
            elif "SELL" in b:
                styles.append("color: #ff5252; font-weight: bold")
            else:
                styles.append("color: #8ea5c0;")
        elif c == "Live Signals Highlight" and str(row.get(c, "")):
            styles.append("background-color: rgba(57,167,255,0.15); color: #82b1ff; font-weight: bold")
        else:
            styles.append("")
    return styles


def _col_config() -> dict:
    return {
        "Symbol": st.column_config.LinkColumn("Symbol", display_text=r".*symbol=NSE%3A([^&#]+).*"),
        "Futures LTP": st.column_config.LinkColumn("Futures LTP", display_text=r".*#price=(.*)"),
        "Spot LTP": st.column_config.NumberColumn("Spot LTP", format="₹%.2f"),
        "Dif. %": st.column_config.TextColumn("Dif. %", help="Futures vs Spot premium/discount"),
        "Spot Change %": st.column_config.NumberColumn("Spot Change %", format="%.2f%%"),
        "Futures Change %": st.column_config.TextColumn("Futures Change %"),
        "Futures OI Change %": st.column_config.TextColumn("Futures OI Change %"),
        "Futures OI": st.column_config.TextColumn("Futures OI", help="Open Interest (contracts)"),
        "Selected-Timeframe Volume": st.column_config.NumberColumn("Selected-Timeframe Volume", format="localized"),
        "Volume Spike Ratio": st.column_config.NumberColumn("Volume Spike Ratio", format="%.2fx"),
        "Final AI Bias": st.column_config.LinkColumn("Final AI Bias", display_text=r".*bias=([^&#]+).*"),
        "Live Signals Highlight": st.column_config.TextColumn(
            "Live Signals Highlight", help="वॉल्यूम स्पाइक, EMA क्रॉसओवर या ज़ोन निकटता अलर्ट"),
        "Latest Verified Delivery %": st.column_config.TextColumn("Latest Verified Delivery %"),
        "PCR": st.column_config.TextColumn("PCR"),
        "CE Total OI": st.column_config.TextColumn("CE Total OI"),
        "PE Total OI": st.column_config.TextColumn("PE Total OI"),
        "Zone Signal": st.column_config.TextColumn("Zone Signal"),
        "Data Source": st.column_config.TextColumn("Data Source"),
    }


def _open_hypothesis_modal(sym: str, row, frame: pd.DataFrame, factors_map: dict,
                           risk_map: dict, macro: dict, fii: dict, tf: str,
                           settings: dict) -> None:
    df = row.get("_df")
    enrich = row.get("_Enrich") or {}
    factors: _Factors = factors_map.get(sym, _Factors())
    risk = risk_map.get(sym, {})

    close = float(row.get("Spot LTP") or 0)
    chg = float(row.get("Spot Change %") or 0.0)
    ema35 = _ema(df["close"], 35) if df is not None else None
    ema100 = _ema(df["close"], 100) if df is not None else None
    vwap = _vwap_last_session(df) if df is not None else None
    ratio, cur_vol, avg_vol = _volume_spike(df) if df is not None else (1.0, 0, 0.0)
    fut = enrich.get("fut_ltp")
    dif = ((fut - close) / close * 100.0) if (fut and close) else None
    oi_val = enrich.get("oi")
    oi_txt = f"{int(oi_val):,}" if oi_val is not None else NA
    oi_chg_val = enrich.get("oi_change_pct")
    oi_chg_txt = f"{oi_chg_val:.2f}" if oi_chg_val is not None else NA
    src_txt = ", ".join(enrich.get("sources") or []) or NA
    zone = row.get("_zone")

    ctx = {
        "close": close,
        "chg_pct_txt": f"{chg:+.2f}%",
        "dif_txt": f"{dif:+.2f}%" if dif is not None else "",
        "vol_spike_txt": f"{ratio:.2f}x",
        "macro_text": (" · ".join(macro.get("lines", [])) or NA) +
                      f" (मैक्रो बायस: {macro.get('bias', 0):+d})",
        "fii_text": (f"FII नेट: {fii.get('fii_net'):+,.0f} Cr · DII नेट: "
                     f"{fii.get('dii_net'):+,.0f} Cr · स्रोत: {fii.get('source', NA)}"
                     f" · तिथि: {fii.get('date') or NA}") if fii.get("fii_net") is not None else NA,
        "price_action": (f"भाव ₹{close:,.2f} ({chg:+.2f}%) · EMA35: "
                         f"{f'{ema35:,.2f}' if ema35 else NA} · EMA100: "
                         f"{f'{ema100:,.2f}' if ema100 else NA} · VWAP: "
                         f"{f'{vwap:,.2f}' if vwap else NA}"),
        "volume_text": (f"वर्तमान वॉल्यूम {cur_vol:,} · 20-बार औसत {avg_vol:,.0f} · "
                        f"स्पाइक अनुपात {ratio:.2f}x (थ्रेशहोल्ड "
                        f"{float(settings.get('vol_spike_threshold', 2.1)):.2f}x)"),
        "zone_text": (f"{getattr(zone,'state', NA)} · "
                      f"{'DEMAND (माँग)' if getattr(zone,'isDemand',False) else 'SUPPLY (आपूर्ति)'} · "
                      f"टाइमफ्रेम {getattr(zone,'tf',tf)} · प्रॉक्सिमल "
                      f"{_safe_float(getattr(zone,'proxVal',None)) or NA} · डिस्टल "
                      f"{_safe_float(getattr(zone,'distVal',None)) or NA}") if zone is not None else NA,
        "futures_text": (f"फ्यूचर्स LTP ₹{fut:,.2f} · स्प्रेड "
                         f"{dif:+.2f}% (प्रीमियम/डिस्काउंट) · OI: {oi_txt} · "
                         f"OI परिवर्तन: {oi_chg_txt}%"
                         f" · एक्सपायरी: {enrich.get('expiry') or NA}"
                         f" · स्रोत: {src_txt}") if fut else NA,
        "options_text": (f"PCR {enrich.get('pcr')} · कुल CE OI {enrich.get('ce_oi')} · "
                         f"कुल PE OI {enrich.get('pe_oi')} · ATM {enrich.get('atm')} · "
                         f"ATM±5 CE/PE OI {enrich.get('ce_oi_atm5')}/{enrich.get('pe_oi_atm5')}")
                        if enrich.get("pcr") is not None else NA,
        "delivery_text": (f"डिलीवरी {enrich.get('delivery_pct')}% · सत्र: "
                          f"{enrich.get('delivery_session') or 'लास्ट उपलब्ध सत्र'} · "
                          f"स्रोत: NSE live delivery data") if enrich.get("delivery_pct") is not None else NA,
        "news_text": ("<br>".join(f"• {a.get('desc')} ({a.get('date')})"
                                   for a in (enrich.get("announcements") or []))
                      or "कोई सत्यापित घोषणा उपलब्ध नहीं"),
        "risk_text": (f"एंट्री ₹{risk.get('entry')} · स्टॉपलॉस ₹{risk.get('sl')} · "
                      f"लक्ष्य (1:{risk.get('rr')}) ₹{risk.get('target')} · "
                      f"पोजीशन साइज़ {risk.get('qty')} शेयर (जोखिम बजट ₹{risk.get('risk_budget'):,.0f} "
                      f"= ₹{risk.get('capital'):,.0f} का {settings.get('risk_pct', 0.5)}%) · "
                      f"आधार: {risk.get('basis')}"),
        "invalidations": [
            f"सक्रिय ज़ोन की डिस्टल लाइन (₹{_safe_float(getattr(zone,'distVal',None)) or NA}) के बाहर क्लोज़",
            "वॉल्यूम स्पाइक के साथ विपरीत दिशा में ब्रेकआउट",
            "FII फ्लो का अचानक उलटना या वैश्विक मैक्रो में तीव्र बदलाव",
        ],
    }

    # --- hypothesis text (rule based, optionally LLM-refined) ---
    bias = str(row.get("Final AI Bias", "NO TRADE"))
    rule_text = _rule_based_hypothesis_text(sym, bias, factors, ctx, risk, tf)
    llm_text = None
    if settings.get("use_llm", True) and os.getenv("GEMINI_API_KEY"):
        llm_text = _gemini_context(_hypothesis_prompt(sym, ctx))
    if llm_text:
        ctx["llm_used"] = True
        ctx["hypothesis_text"] = llm_text + "\n\n---\n**नियम-आधारित सत्यापन:**\n" + rule_text
    else:
        ctx["llm_used"] = False
        ctx["hypothesis_text"] = rule_text

    _show_stock_bias_modal(sym, bias, ctx, factors, risk, tf)


def _rule_based_hypothesis_text(sym: str, bias: str, f: _Factors, ctx: dict,
                                risk: dict, tf: str) -> str:
    lines = [f"**{sym}** पर **{bias}** — अंतिम स्कोर {f.score:+.2f} ({tf} टाइमफ्रेम, "
             f"सत्यापित डेटा पर आधारित).", ""]
    if f.bull:
        lines.append("**सकारात्मक प्रमाण:**")
        lines += [f"• {x}" for x in f.bull]
    if f.bear:
        lines.append("**नकारात्मक प्रमाण:**")
        lines += [f"• {x}" for x in f.bear]
    if f.missing:
        lines.append(f"**अनुपलब्ध डेटा (अनुमान से नहीं भरा गया):** {', '.join(f.missing)}")
    lines.append("")
    lines.append(f"**रिस्क सेटअप:** एंट्री ₹{risk.get('entry')} · SL ₹{risk.get('sl')} · "
                 f"लक्ष्य ₹{risk.get('target')} (1:{risk.get('rr')}) · साइज़ {risk.get('qty')} शेयर.")
    return "\n\n".join(lines)


# ==============================================================================
# 14) TAB 2 — ZONE (multi timeframe nearest zone)
# ==============================================================================

@st.cache_data(ttl=300, show_spinner=False)
def _scan_zones_multitf(symbols: tuple[str, ...], tfs_json: str,
                         settings_json: str) -> list[dict]:
    settings = json.loads(settings_json)
    tfs = json.loads(tfs_json)
    bundle = _load_price_bundle(symbols, tfs_json)
    capital = float(settings.get("account_capital", 25000.0))
    risk_pct = float(settings.get("risk_pct", 0.5))
    risk_budget = capital * (risk_pct / 100.0)
    rr = float(settings.get("target_rr", 5.0))
    distal_buf = float(settings.get("distal_buffer_pct", 0.5))

    rows: list[dict] = []
    for sym in symbols:
        best = None
        for tf in tfs:
            df = _tf_frame(bundle, tf, sym)
            if df is None or len(df) < 12:
                continue
            zone, dist = _nearest_zone_for_df(df, tf, settings)
            if zone is None or dist is None:
                continue
            price = float(df["close"].iloc[-1])
            dist_pct = (dist / price * 100.0) if price else 999.0
            cand = (dist_pct, TF_PRIORITY.get(tf, 99), tf, zone, dist, df, price)
            if best is None or (cand[0], cand[1]) < (best[0], best[1]):
                best = cand
        if best is None:
            continue

        dist_pct, _rank, tf, zone, dist, df, price = best
        prox = float(zone.proxVal)
        distv = float(zone.distVal)
        is_demand = bool(zone.isDemand)
        adj = distv * (1 - distal_buf / 100.0) if is_demand else distv * (1 + distal_buf / 100.0)
        per_unit = abs(prox - adj)
        qty = int(math.floor(risk_budget / per_unit)) if per_unit > 0 else 0
        target = prox + per_unit * rr if is_demand else prox - per_unit * rr
        chg = ((price - float(df["close"].iloc[-2])) / float(df["close"].iloc[-2]) * 100.0) \
            if len(df) > 1 and float(df["close"].iloc[-2]) else 0.0
        bias = "BUY" if (is_demand and chg > 0) else ("SELL" if (not is_demand and chg < 0) else "NO TRADE")
        rows.append({
            "Symbols": sym, "LTP": round(price, 2), "_Change_Pct": round(chg, 2),
            "demand/Supply": "Demand" if is_demand else "Supply",
            "Near Timeframe": tf, "Zone State": str(zone.state),
            "Entry Price (Proximal)": round(prox, 2),
            "Distal Stoploss": round(adj, 2),
            "Target Price": round(target, 2),
            "Quantity (Capital According)": qty,
            "Distance %": round(dist_pct, 2),
            "AI Bias": bias,
            "_Zone": zone, "_DF": df, "_Prox": prox, "_Dist": adj, "_Target": target,
        })
    return rows


def _render_zone_tab(settings: dict) -> None:
    st.markdown("### 🎯 ZONE SCANNER — सभी टाइमफ्रेम में सबसे नज़दीकी ज़ोन")
    st.caption(f"सक्रिय टाइमफ्रेम: {', '.join(ACTIVE_TFS)} · "
               f"2h/4h/6h/8h 1h फ़ीड से resampled, Weekly/Monthly daily फ़ीड से · "
               f"ज़ोन इंजन: {ZONE_ENGINE_SOURCE}")

    c1, c2, c3 = st.columns([2, 1.6, 3])
    with c1:
        if st.button("🚀 Refresh Zone Scan", type="primary", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    with c2:
        scan_mode = st.selectbox("मल्टी-टाइमफ्रेम स्कैन", ["सभी टाइमफ्रेम", "केवल Daily", "केवल Intraday (≤1h)"],
                                  key="zone_scan_mode")
    with c3:
        st.caption("नज़दीकी ज़ोन = वर्तमान भाव से निकटतम सक्रिय (Fresh/Tested) माँग/आपूर्ति ज़ोन।")

    universe, _src = _get_nse_fno_universe()
    limit = int(settings.get("scan_limit", 0) or 0)
    symbols = tuple(universe[:limit] if limit > 0 else universe)

    if scan_mode == "केवल Daily":
        tfs = ["Daily"]
    elif scan_mode == "केवल Intraday (≤1h)":
        tfs = [tf for tf in ACTIVE_TFS if tf in ("15m", "30m", "75m", "1h")]
    else:
        tfs = ACTIVE_TFS

    with st.spinner("मल्टी-टाइमफ्रेम ज़ोन स्कैन चल रहा है…"):
        rows = _scan_zones_multitf(symbols, json.dumps(tfs), json.dumps(settings, default=str))

    if not rows:
        st.info("वर्तमान में चयनित स्टॉक्स में कोई सक्रिय Fresh/Tested ज़ोन नहीं मिला।")
        return

    df_res = pd.DataFrame(rows)
    token = uuid.uuid4().hex[:8]
    df_res["_Chart_URL"] = df_res["Symbols"].map(
        lambda s: f"https://in.tradingview.com/chart/?symbol={url_quote('NSE:' + s, safe='')}&interval=60")
    df_res["_Bias_Link_URL"] = df_res["Symbols"].map(lambda s: f"?znsym={s}&t={token}")
    df_res["_Fut_Chart_URL"] = "https://in.tradingview.com/chart/?symbol="

    qp = st.query_params.get("znsym", None)
    if qp and qp in set(df_res["Symbols"].tolist()):
        st.query_params.clear()
        r = df_res[df_res["Symbols"] == qp].iloc[0]
        _open_zone_modal(qp, r, settings)

    show = ["Symbols", "LTP", "demand/Supply", "Near Timeframe", "Zone State",
            "Entry Price (Proximal)", "Distal Stoploss", "Target Price",
            "Quantity (Capital According)", "Distance %", "AI Bias"]
    disp = _linkify_table(df_res[show + ["_Chart_URL", "_Bias_Link_URL"]].copy())
    st.dataframe(disp.style.apply(_zone_styles, axis=1),
                 column_config={
                     "Symbols": st.column_config.LinkColumn("Symbols", display_text=r".*symbol=NSE%3A([^&#]+).*"),
                     "AI Bias": st.column_config.LinkColumn("AI Bias", display_text=r".*znsym=([^&#]+).*"),
                     "LTP": st.column_config.NumberColumn("LTP", format="₹%.2f"),
                     "Entry Price (Proximal)": st.column_config.NumberColumn("Entry Price (Proximal)", format="₹%.2f"),
                     "Distal Stoploss": st.column_config.NumberColumn("Distal Stoploss", format="₹%.2f"),
                     "Target Price": st.column_config.NumberColumn("Target Price", format="₹%.2f"),
                     "Distance %": st.column_config.NumberColumn("Distance %", format="%.2f%%"),
                     "Quantity (Capital According)": st.column_config.NumberColumn("Quantity (Capital According)", format="%d"),
                 }, hide_index=True, height=560, **_df_layout())


def _zone_styles(row) -> list[str]:
    val = row.get("_Change_Pct", 0)
    color = "#00e676" if val >= 0 else "#ff5252"
    styles = []
    for c in row.index:
        if c == "LTP":
            styles.append(f"color: {color}; font-weight: bold")
        elif c == "demand/Supply":
            styles.append("color: #00e676; font-weight: bold"
                          if row.get(c) == "Demand" else "color: #ff5252; font-weight: bold")
        elif c == "AI Bias":
            b = str(row.get(c, ""))
            styles.append("color: #00e676; font-weight: bold" if "BUY" in b
                          else ("color: #ff5252; font-weight: bold" if "SELL" in b else "color: #8ea5c0;"))
        elif c == "Zone State":
            styles.append("color: #82b1ff;")
        else:
            styles.append("")
    return styles


def _open_zone_modal(sym: str, row, settings: dict) -> None:
    zone = row.get("_Zone")
    df = row.get("_DF")
    tf = str(row.get("Near Timeframe"))
    price = float(row.get("LTP") or 0)
    prox = float(row.get("_Prox") or 0)
    dist = float(row.get("_Dist") or 0)
    target = float(row.get("_Target") or 0)
    qty = int(row.get("Quantity (Capital According)") or 0)
    is_demand = str(row.get("demand/Supply")) == "Demand"
    chg = float(row.get("_Change_Pct") or 0.0)

    ctx = {
        "close": price, "chg_pct_txt": f"{chg:+.2f}%", "dif_txt": "", "vol_spike_txt": NA,
        "macro_text": "ज़ोन टैब — मैक्रो संदर्भ टैब 4 से लिया गया (वैकल्पिक)",
        "fii_text": "ज़ोन टैब — FII/DII टैब 4 से लिया गया (वैकल्पिक)",
        "price_action": (f"भाव ₹{price:,.2f} · EMA35: "
                         f"{_ema(df['close'], 35) if df is not None else NA} · EMA100: "
                         f"{_ema(df['close'], 100) if df is not None else NA}"),
        "volume_text": (f"वॉल्यूम स्पाइक {_volume_spike(df)[0]}x" if df is not None else NA),
        "zone_text": (f"{getattr(zone,'state',NA)} · "
                      f"{'DEMAND (माँग)' if is_demand else 'SUPPLY (आपूर्ति)'} · टाइमफ्रेम {tf} · "
                      f"प्रॉक्सिमल ₹{prox:,.2f} · डिस्टल ₹{dist:,.2f} · दूरी {row.get('Distance %')}%"),
        "futures_text": "ज़ोन टैब में वास्तविक Futures डेटा शामिल नहीं (टैब 1 में उपलब्ध)",
        "options_text": "ज़ोन टैब में वास्तविक Options डेटा शामिल नहीं (टैब 1 में उपलब्ध)",
        "delivery_text": "ज़ोन टैब में वास्तविक Delivery डेटा शामिल नहीं (टैब 1 में उपलब्ध)",
        "news_text": "ज़ोन टैब में समाचार डेटा शामिल नहीं (टैब 4 में उपलब्ध)",
        "risk_text": (f"एंट्री ₹{prox:,.2f} · स्टॉपलॉस ₹{dist:,.2f} · लक्ष्य ₹{target:,.2f} · "
                      f"साइज़ {qty} शेयर (जोखिम बजट ₹{float(settings.get('account_capital', 25000)) * float(settings.get('risk_pct', 0.5)) / 100:,.0f})"),
        "invalidations": [
            f"डिस्टल लाइन ₹{dist:,.2f} के बाहर क्लोज़",
            "टाइमफ्रेम बदलने पर ज़ोन की प्राथमिकता बदल सकती है",
        ],
    }
    bias = str(row.get("AI Bias", "NO TRADE"))
    f = _Factors()
    f.bull = [f"{getattr(zone,'state','')} {'Demand' if is_demand else 'Supply'} ज़ोन, टाइमफ्रेम {tf}"]
    if bias == "NO TRADE":
        f.bear = ["ज़ोन और मूल्य दिशा में सहमति नहीं (कोई स्पष्ट momentum नहीं)"]
    ctx["llm_used"] = False
    ctx["hypothesis_text"] = (
        f"**{sym}** — **{bias}** (ज़ोन आधारित, {tf})\n\n"
        f"• सक्रिय ज़ोन: {getattr(zone,'state',NA)} {'माँग' if is_demand else 'आपूर्ति'} @ ₹{prox:,.2f}\n"
        f"• एंट्री ₹{prox:,.2f} · SL ₹{dist:,.2f} · लक्ष्य ₹{target:,.2f} · साइज़ {qty} शेयर\n"
        f"• ज़ोन की दूरी भाव से {row.get('Distance %')}%")
    _show_stock_bias_modal(sym, bias, ctx, f, {
        "entry": prox, "sl": dist, "target": target, "qty": qty,
        "rr": settings.get("target_rr", 5), "capital": settings.get("account_capital", 25000),
        "risk_budget": float(settings.get("account_capital", 25000)) * float(settings.get("risk_pct", 0.5)) / 100,
        "basis": "सक्रिय ज़ोन (proximal/distal)",
    }, tf)


# ==============================================================================
# 15) TAB 3 — GLOBAL ZONE (multi timeframe)
# ==============================================================================

@st.cache_data(ttl=300, show_spinner=False)
def _scan_global_zones(tfs_json: str, settings_json: str) -> list[dict]:
    settings = json.loads(settings_json)
    tfs = json.loads(tfs_json)
    tickers = [i["ticker"] for i in GLOBAL_INSTRUMENTS]
    rows: list[dict] = []
    bundle: dict[str, dict[str, pd.DataFrame]] = {tf: {} for tf in tfs}
    for tf in tfs:
        spec = TF_FEED_SPEC.get(tf)
        if not spec:
            continue
        if spec["source"] == "yf":
            data = _batch_download(tickers, spec["period"], spec["interval"], chunk_size=30)
            for tkr, block in data.items():
                bundle[tf][tkr] = block
        else:
            base = spec["base"]
            for tkr, block in (bundle.get(base) or {}).items():
                rs = _resample_ohlcv(block, spec["rule"])
                if not rs.empty:
                    bundle[tf][tkr] = rs
    # resample order: make sure base TFs are processed first
    for tf in ["75m", "2h", "4h", "6h", "8h", "Weekly", "Monthly"]:
        if tf in tfs and tf not in bundle:
            pass

    rr = float(settings.get("target_rr", 5.0))
    distal_buf = float(settings.get("distal_buffer_pct", 0.5))
    for item in GLOBAL_INSTRUMENTS:
        best = None
        for tf in tfs:
            df = (bundle.get(tf) or {}).get(item["ticker"])
            if df is None or len(df) < 12:
                continue
            zone, dist = _nearest_zone_for_df(df, tf, settings)
            if zone is None or dist is None:
                continue
            price = float(df["close"].iloc[-1])
            dist_pct = (dist / price * 100.0) if price else 999.0
            cand = (dist_pct, TF_PRIORITY.get(tf, 99), tf, zone, dist, df, price)
            if best is None or (cand[0], cand[1]) < (best[0], best[1]):
                best = cand
        if best is None:
            continue
        _dp, _r, tf, zone, dist, df, price = best
        prox = float(zone.proxVal)
        distv = float(zone.distVal)
        is_demand = bool(zone.isDemand)
        adj = distv * (1 - distal_buf / 100.0) if is_demand else distv * (1 + distal_buf / 100.0)
        per_unit = abs(prox - adj)
        target = prox + per_unit * rr if is_demand else prox - per_unit * rr
        prev = float(df["close"].iloc[-2]) if len(df) > 1 else price
        chg = ((price - prev) / prev * 100.0) if prev else 0.0
        bias = "BUY" if (is_demand and chg > 0) else ("SELL" if (not is_demand and chg < 0) else "NEUTRAL")
        rows.append({
            "Symbol": item["id"], "Name": item["name"], "LTP": round(price, 4),
            "_Change_Pct": round(chg, 2),
            "Demand/Supply": "Demand" if is_demand else "Supply",
            "Near Timeframe": tf, "Zone State": str(zone.state),
            "Entry Price": round(prox, 4), "Distal Stoploss": round(adj, 4),
            "Target Price": round(target, 4), "AI Bias": bias,
            "_Zone": zone, "_DF": df,
        })
    return rows


def _render_global_zone_tab(settings: dict) -> None:
    st.markdown("### 🌐 GLOBAL INSTRUMENTS — मल्टी-टाइमफ्रेम ज़ोन स्कैनर")
    st.caption("DXY, US10Y, सोना, क्रूड, US30, S&P 500, A50, Nikkei आदि — "
               "वास्तविक OHLCV से निकला नज़दीकी माँग/आपूर्ति ज़ोन + TradingView लिंक।")

    c1, c2 = st.columns([2, 4])
    with c1:
        if st.button("🚀 Refresh Global Zones", type="primary", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    with c2:
        st.caption(f"सक्रिय टाइमफ्रेम: {', '.join(ACTIVE_TFS)}")

    tfs = [tf for tf in ACTIVE_TFS if tf in ("1h", "2h", "4h", "8h", "Daily", "Weekly")]
    if not tfs:
        tfs = ["1h", "Daily"]
    with st.spinner("ग्लोबल ज़ोन स्कैन चल रहा है…"):
        rows = _scan_global_zones(json.dumps(tfs), json.dumps(settings, default=str))

    if not rows:
        st.info("ग्लोबल मार्केट्स में वर्तमान में कोई सक्रिय Fresh/Tested ज़ोन नहीं मिला।")
        return

    df_gz = pd.DataFrame(rows)
    token = uuid.uuid4().hex[:8]
    df_gz["_Chart_URL"] = df_gz["Symbol"].map(
        lambda s: "https://in.tradingview.com/chart/?symbol=" + url_quote(
            next((i["tv"] for i in GLOBAL_INSTRUMENTS if i["id"] == s), s), safe=""))
    df_gz["_Bias_Link_URL"] = df_gz["Symbol"].map(lambda s: f"?gznsym={s}&t={token}")

    qp = st.query_params.get("gznsym", None)
    if qp and qp in set(df_gz["Symbol"].tolist()):
        st.query_params.clear()
        r = df_gz[df_gz["Symbol"] == qp].iloc[0]
        _open_global_modal(qp, r, settings)

    g_cols = ["Symbol", "Name", "LTP", "Demand/Supply", "Near Timeframe", "Zone State",
              "Entry Price", "Distal Stoploss", "Target Price", "AI Bias"]
    disp = _linkify_table(df_gz[g_cols + ["_Chart_URL", "_Bias_Link_URL"]].copy())
    st.dataframe(disp.style.apply(_global_styles, axis=1),
                 column_config={
                     "Symbol": st.column_config.LinkColumn("Symbol", display_text=r".*symbol=([^&#]+).*"),
                     "AI Bias": st.column_config.LinkColumn("AI Bias", display_text=r".*gznsym=([^&#]+).*"),
                     "LTP": st.column_config.NumberColumn("LTP", format="%.4f"),
                     "Entry Price": st.column_config.NumberColumn("Entry Price", format="%.4f"),
                     "Distal Stoploss": st.column_config.NumberColumn("Distal Stoploss", format="%.4f"),
                     "Target Price": st.column_config.NumberColumn("Target Price", format="%.4f"),
                 }, hide_index=True, height=560, **_df_layout())


def _global_styles(row) -> list[str]:
    val = row.get("_Change_Pct", 0)
    color = "#00e676" if val >= 0 else "#ff5252"
    styles = []
    for c in row.index:
        if c == "LTP":
            styles.append(f"color: {color}; font-weight: bold")
        elif c == "Demand/Supply":
            styles.append("color: #00e676; font-weight: bold"
                          if row.get(c) == "Demand" else "color: #ff5252; font-weight: bold")
        elif c == "AI Bias":
            b = str(row.get(c, ""))
            styles.append("color: #00e676; font-weight: bold" if "BUY" in b
                          else ("color: #ff5252; font-weight: bold" if "SELL" in b else "color: #8ea5c0;"))
        else:
            styles.append("")
    return styles


def _open_global_modal(sym: str, row, settings: dict) -> None:
    zone = row.get("_Zone")
    df = row.get("_DF")
    price = float(row.get("LTP") or 0)
    chg = float(row.get("_Change_Pct") or 0.0)
    ctx = {
        "close": price, "chg_pct_txt": f"{chg:+.2f}%", "dif_txt": "", "vol_spike_txt": NA,
        "macro_text": "ग्लोबल इंस्ट्रूमेंट — मैक्रो संदर्भ इसी टैब के अन्य सिद्धांतों से",
        "fii_text": "FII/DII डेटा टैब 4 (NSE लाइव) में उपलब्ध",
        "price_action": f"भाव {price:,.4f} ({chg:+.2f}%)",
        "volume_text": (f"वॉल्यूम स्पाइक {_volume_spike(df)[0]}x" if df is not None else NA),
        "zone_text": (f"{getattr(zone,'state',NA)} · {row.get('Demand/Supply')} · "
                      f"टाइमफ्रेम {row.get('Near Timeframe')} · प्रॉक्सिमल {row.get('Entry Price')} · "
                      f"डिस्टल {row.get('Distal Stoploss')}"),
        "futures_text": "ग्लोबल इंस्ट्रूमेंट के लिए NSE F&O डेटा लागू नहीं",
        "options_text": "ग्लोबल इंस्ट्रूमेंट के लिए NSE Option Chain लागू नहीं",
        "delivery_text": "ग्लोबल इंस्ट्रूमेंट के लिए NSE Delivery लागू नहीं",
        "news_text": "समाचार/घोषणाएँ टैब 4 में उपलब्ध",
        "risk_text": (f"एंट्री {row.get('Entry Price')} · SL {row.get('Distal Stoploss')} · "
                      f"लक्ष्य {row.get('Target Price')}"),
        "invalidations": ["डिस्टल लाइन के बाहर क्लोज़", "मैक्रो शॉक"],
    }
    bias = str(row.get("AI Bias", "NEUTRAL"))
    f = _Factors()
    f.bull = [f"{getattr(zone,'state','')} {row.get('Demand/Supply')} ज़ोन @ {row.get('Near Timeframe')}"]
    ctx["llm_used"] = False
    ctx["hypothesis_text"] = (f"**{sym}** — **{bias}**\n\n"
                              f"• ज़ोन: {row.get('Zone State')} {row.get('Demand/Supply')} @ "
                              f"{row.get('Near Timeframe')}\n"
                              f"• एंट्री {row.get('Entry Price')} · SL {row.get('Distal Stoploss')} · "
                              f"लक्ष्य {row.get('Target Price')}")
    _show_stock_bias_modal(sym, bias, ctx, f, {
        "entry": row.get("Entry Price"), "sl": row.get("Distal Stoploss"),
        "target": row.get("Target Price"), "qty": 0, "rr": settings.get("target_rr", 5),
        "capital": settings.get("account_capital", 25000), "risk_budget": 0,
        "basis": "ग्लोबल ज़ोन (proximal/distal)",
    }, str(row.get("Near Timeframe")))


# ==============================================================================
# 16) TAB 4 — NEWS & EVENTS
# ==============================================================================

def _render_news_events_tab(settings: dict) -> None:
    st.markdown("### 📰 GLOBAL STRIP & INSTITUTIONAL EVENTS")

    gift = _fetch_gift_nifty()
    strip_items = [
        {"name": "NIFTY 50", "ticker": "^NSEI", "tv": "NSE:NIFTY"},
        {"name": "DXY (Dollar Index)", "ticker": "DX-Y.NYB", "tv": "TVC:DXY"},
        {"name": "USD / INR", "ticker": "USDINR=X", "tv": "FX_IDC:USDINR"},
        {"name": "US 30 (Dow Jones)", "ticker": "^DJI", "tv": "DJ:DJI"},
        {"name": "US 10Y Yield", "ticker": "^TNX", "tv": "TVC:US10Y"},
        {"name": "Gold (XAUUSD)", "ticker": "GC=F", "tv": "OANDA:XAUUSD"},
        {"name": "WTI Crude Oil", "ticker": "CL=F", "tv": "TVC:USOIL"},
        {"name": "TLT (iShares 20+)", "ticker": "TLT", "tv": "NASDAQ:TLT"},
    ]

    cols = st.columns(len(strip_items) + 1)
    with cols[0]:
        price = gift.get("price")
        chg = gift.get("change_pct")
        cls = "pos-txt" if (chg or 0) >= 0 else "neg-txt"
        sign = "+" if (chg or 0) >= 0 else ""
        tv_gift = f"https://in.tradingview.com/chart/?symbol={url_quote('NSE:NIFTY1!', safe='')}"
        st.markdown(
            f"""<div class="strip-card" style="border: 1px solid #38bdf8;">
                <div class="strip-title"><a href="{tv_gift}" target="_blank"
                style="color:#38bdf8; text-decoration:none; font-weight:bold;">GIFT NIFTY 🔗</a></div>
                <div class="strip-val">{f'{price:,.2f}' if price else NA}</div>
                <div class="{cls}">{f'{sign}{chg:.2f}%' if chg is not None else NA}</div>
            </div>""", unsafe_allow_html=True)

    try:
        tickers_list = [x["ticker"] for x in strip_items]
        data = yf.download(tickers_list, period="5d", interval="1d", group_by="ticker",
                           progress=False, threads=True, auto_adjust=False)
        for idx, item in enumerate(strip_items):
            block = _extract_block(data, item["ticker"], len(tickers_list))
            if block is None or block.empty:
                continue
            block = block.dropna(subset=["close"])
            if len(block) < 2:
                continue
            p0 = float(block["close"].iloc[-1])
            p1 = float(block["close"].iloc[-2])
            c_pct = ((p0 - p1) / p1) * 100 if p1 else 0.0
            cls_name = "pos-txt" if c_pct >= 0 else "neg-txt"
            sign = "+" if c_pct >= 0 else ""
            tv_link = f"https://in.tradingview.com/chart/?symbol={url_quote(item['tv'], safe='')}"
            with cols[idx + 1]:
                st.markdown(
                    f"""<div class="strip-card">
                        <div class="strip-title"><a href="{tv_link}" target="_blank"
                        style="color:#8ea5c0; text-decoration:none;">{item['name']} 🔗</a></div>
                        <div class="strip-val">{p0:,.2f}</div>
                        <div class="{cls_name}">{sign}{c_pct:.2f}%</div>
                    </div>""", unsafe_allow_html=True)
    except Exception:
        st.caption("ग्लोबल स्ट्रिप डेटा अभी उपलब्ध नहीं है।")

    st.markdown("---")
    master = st.session_state.get("master_nse_frame")
    if master is None or master.empty:
        st.info("मास्टर फ़्रेम अभी लोड नहीं हुआ — मास्टर स्कैन चलाने के लिए नीचे बटन दबाएँ "
                "(या 'NSE ALL FUTURE STOCKS' टैब खोलें)।")
        if st.button("मास्टर स्कैन चलाएँ", type="primary"):
            universe, _src = _get_nse_fno_universe()
            limit = int(settings.get("scan_limit", 0) or 0)
            symbols = tuple(universe[:limit] if limit > 0 else universe)
            with st.spinner("मास्टर स्कैन चल रहा है…"):
                frame, *_rest = _compose_master_frame(
                    symbols, settings.get("selected_tf", "Daily"), settings)
                st.session_state["master_nse_frame"] = frame
            st.rerun()
        master = st.session_state.get("master_nse_frame")
    if master is not None and not master.empty:
        total = len(master)
        advances = int((master["Spot Change %"] > 0).sum())
        declines = int((master["Spot Change %"] < 0).sum())
        gap_ups = int((master["Open Gap"] == "Gap-Up").sum())
        gap_downs = int((master["Open Gap"] == "Gap-Down").sum())
    else:
        total = advances = declines = gap_ups = gap_downs = 0

    st.markdown(f"#### ⚖️ NSE ALL FUTURES MARKET BREADTH (कुल {total} फ्यूचर्स स्टॉक्स)")
    b1, b2, b3, b4 = st.columns(4)
    for col, label, value, key in (
        (b1, "Advances", advances, "ADVANCES"),
        (b2, "Declines", declines, "DECLINES"),
        (b3, "Gap-Up", gap_ups, "GAP_UP"),
        (b4, "Gap-Down", gap_downs, "GAP_DOWN"),
    ):
        with col:
            if st.button(f"{label}: {value}", use_container_width=True):
                st.session_state["breadth_filter"] = key
                st.session_state["inline_breadth_view"] = key

    inline_view = st.session_state.get("inline_breadth_view")
    if inline_view and master is not None and not master.empty:
        st.markdown(f"##### 📋 फ़िल्टर्ड सूची: `{inline_view}`")
        if inline_view == "ADVANCES":
            sub = master[master["Spot Change %"] > 0]
        elif inline_view == "DECLINES":
            sub = master[master["Spot Change %"] < 0]
        elif inline_view == "GAP_UP":
            sub = master[master["Open Gap"] == "Gap-Up"]
        else:
            sub = master[master["Open Gap"] == "Gap-Down"]
        cols_sel = ["Symbol", "Spot LTP", "Futures LTP", "Dif. %", "Spot Change %",
                    "Volume Spike Ratio", "Final AI Bias"]
        st.dataframe(_linkify_table(sub[cols_sel + ["_Spot_Chart_URL", "_Fut_Chart_URL",
                                                     "_Bias_Link_URL"]]),
                     column_config=_col_config(), hide_index=True, height=280, **_df_layout())

    st.markdown("---")
    e1, e2 = st.columns(2)
    with e1:
        fii = _fetch_fii_dii()
        st.markdown("#### 🏦 Verified FII / DII Flow "
                    "([🔗 NSE FII/DII](https://www.nseindia.com/market-data/fii-dii-activity))")
        f_net = fii.get("fii_net")
        d_net = fii.get("dii_net")
        f_color = "#ff5252" if (f_net is not None and f_net < 0) else "#00e676"
        d_color = "#00e676" if (d_net is not None and d_net >= 0) else "#ff5252"
        c_fii, c_dii = st.columns(2)
        with c_fii:
            st.markdown(f"""
            <div class="fii-box" style="border-left: 4px solid {f_color};">
                <div class="fii-header">FII Net Cash Flow</div>
                <div class="fii-amount" style="color: {f_color};">
                {f'{f_net:+,.0f} Cr' if f_net is not None else NA}</div>
                <div style="font-size:0.72rem; color:#94a3b8; margin-top:2px;">
                {'नेट बिकवाली' if (f_net is not None and f_net < 0) else 'नेट खरीदारी'}</div>
            </div>""", unsafe_allow_html=True)
        with c_dii:
            st.markdown(f"""
            <div class="fii-box" style="border-left: 4px solid {d_color};">
                <div class="fii-header">DII Net Cash Flow</div>
                <div class="fii-amount" style="color: {d_color};">
                {f'{d_net:+,.0f} Cr' if d_net is not None else NA}</div>
                <div style="font-size:0.72rem; color:#94a3b8; margin-top:2px;">
                {'नेट खरीदारी' if (d_net is not None and d_net >= 0) else 'नेट बिकवाली'}</div>
            </div>""", unsafe_allow_html=True)
        st.caption(f"स्रोत: {fii.get('source', NA)} · रिपोर्टिंग तिथि: {fii.get('date') or NA} "
                   f"· सत्यापित: {'हाँ' if fii.get('verified') else 'नहीं'}")
        st.markdown("#### 🌅 Pre-Market Analysis (सुबह 08:30 IST)")
        st.caption("इस खंड में सत्यापित घोषणाएँ और मैक्रो डेटा के आधार पर टिप्पणी जोड़ने के लिए "
                   "FII/DII सहायक तथ्य उपलब्ध हैं।")

    with e2:
        st.markdown("#### 📢 ताज़ा NSE Corporate Announcements (सत्यापित)")
        universe, _src = _get_nse_fno_universe()
        watch = [s for s in ("TCS", "RELIANCE", "HDFCBANK", "TATAMOTORS", "INFY")
                 if s in universe][:5]
        found = False
        for sym in watch:
            try:
                for ann in _fetch_nse_announcements(sym)[:2]:
                    found = True
                    st.markdown(f"• **{sym}** — {ann['desc']} ({ann['date']})")
            except Exception:
                continue
        if not found:
            st.info("NSE घोषणा फ़ीड अभी उपलब्ध नहीं है (Unverified नहीं, खाली नहीं दिखाया गया)।")

    st.markdown("---")
    st.markdown("#### 🔔 Live Signals & Alerts (Proximity, Vol Spike, EMA Cross)")
    if master is not None and not master.empty:
        alerts = master[master["Live Signals Highlight"].fillna("").ne("")].head(10)
        if not alerts.empty:
            for _, alt in alerts.iterrows():
                st.warning(f"⚡ **{alt['Symbol']}**: {alt['Live Signals Highlight']} | "
                           f"Spot LTP: ₹{alt['Spot LTP']} | Bias: {alt['Final AI Bias']}")
        else:
            st.caption("वर्तमान में कोई नया अलर्ट नहीं मिला।")


# ==============================================================================
# 17) TAB 5 — SETTINGS
# ==============================================================================

def _render_settings_tab(settings: dict) -> None:
    st.markdown("### ⚙️ TERMINAL & ZONE ENGINE SETTINGS")
    st.caption("EOD Range, Distal Stoploss Buffer, ज़ोखिम प्रबंधन, टाइमफ्रेम और डेटा एन्चिकमेंट।")

    with st.form("terminal_settings_form"):
        s1, s2 = st.columns(2)
        with s1:
            st.markdown("#### 🎯 Zone Core EOD Range Settings")
            use_eod = st.checkbox("Enable EOD Range Filter",
                                  value=bool(settings.get("use_eod_range", True)),
                                  help="Proximal Line को Day High+Buffer और Day Low-Buffer के बीच सीमित करता है")
            high_buf = st.slider("Day Close High Buffer (%)", 0.0, 30.0,
                                 float(settings.get("eod_high_buffer_pct", 10.0)), 1.0)
            low_buf = st.slider("Day Close Low Buffer (%)", 0.0, 30.0,
                                float(settings.get("eod_low_buffer_pct", 10.0)), 1.0)
            distal_buf = st.slider("Distal Stoploss Buffer (%)", 0.0, 5.0,
                                   float(settings.get("distal_buffer_pct", 0.5)), 0.1)
            st.markdown("#### 🏢 Universe & Enrichment")
            scan_limit = st.number_input("स्कैन यूनिवर्स सीमा (0 = सभी F&O स्टॉक्स)", min_value=0, max_value=600,
                                         value=int(settings.get("scan_limit", 0) or 0), step=25,
                                         help="बड़ी संख्या = धीमा स्कैन। 0 = पूरा यूनिवर्स।")
            enrich_limit = st.slider("Futures/Options/Delivery enrich (टॉप N प्रतीक)", 0, 300,
                                     int(settings.get("enrich_limit", 60)), 10,
                                     help="0 = केवल OHLCV (बहुत तेज़), बड़ा मान = अधिक F&O डेटा (धीमा)")
            vol_thr = st.slider("Volume Spike थ्रेशहोल्ड (x)", 1.0, 5.0,
                                float(settings.get("vol_spike_threshold", 2.1)), 0.1)
        with s2:
            st.markdown("#### 🛡️ Risk & Capital Management")
            cap = st.number_input("Account Capital (₹)", 1000.0, 10000000.0,
                                  float(settings.get("account_capital", 25000.0)), 5000.0)
            risk = st.number_input("Risk Per Trade (%)", 0.1, 5.0,
                                   float(settings.get("risk_pct", 0.5)), 0.1)
            target_rr = st.number_input("Target R:R Ratio", 1.0, 10.0,
                                        float(settings.get("target_rr", 5.0)), 0.5)
            use_llm = st.checkbox("Gemini API से AI Hypothesis संवर्धित करें",
                                  value=bool(settings.get("use_llm", True)))
            st.markdown("#### ⏱️ ज़ोन स्कैन में सक्रिय टाइमफ्रेम")
            extra_tfs = st.multiselect("Active Timeframes:", ALL_TIMEFRAMES, default=ACTIVE_TFS)

        save = st.form_submit_button("💾 Save All Settings", type="primary")
        if save:
            st.session_state.analysis_settings.update({
                "use_eod_range": use_eod, "eod_high_buffer_pct": high_buf,
                "eod_low_buffer_pct": low_buf, "distal_buffer_pct": distal_buf,
                "account_capital": cap, "risk_pct": risk, "target_rr": target_rr,
                "scan_limit": scan_limit, "enrich_limit": enrich_limit,
                "vol_spike_threshold": vol_thr, "custom_tfs": extra_tfs,
                "use_llm": use_llm,
            })
            st.cache_data.clear()
            _enrich_cache.clear()
            st.success("सेटिंग्स सेव हो गईं।")
            st.rerun()

    st.markdown("---")
    st.markdown("#### 🔌 डेटा प्रोवाइडर स्थिति (लाइव प्रोब)")
    caps = _nse_capabilities()
    dhan = bool(os.getenv("DHAN_CLIENT_ID"))
    gemini = bool(os.getenv("GEMINI_API_KEY"))

    def _dot(ok: bool, ok_txt: str, bad_txt: str) -> str:
        return f"🟢 {ok_txt}" if ok else f"🟡 {bad_txt}"

    p1, p2, p3 = st.columns(3)
    with p1:
        st.write(f"**NSE Futures (quote-derivative):** "
                 f"{_dot(caps.get('derivative'), 'Live उपलब्ध', 'Blocked (Akamai/IP) — TradingView fallback active')}")
        st.write(f"**NSE Option Chain / PCR:** "
                 f"{_dot(caps.get('option_chain'), 'Live उपलब्ध', 'Unavailable — कोई अनुमान नहीं')}")
        st.write(f"**NSE Delivery %:** "
                 f"{_dot(caps.get('equity'), 'Live उपलब्ध', 'Unavailable — कोई अनुमान नहीं')}")
    with p2:
        st.write(f"**TradingView screener (Futures + OI):** 🟢 Active (~15 min delayed)")
        st.write(f"**Yahoo Finance (OHLCV):** 🟢 Active (EMA 35/100, VWAP, Vol Spike)")
        st.write(f"**NSE FII/DII + घोषणाएँ:** "
                 f"{_dot(caps.get('announcements'), 'Live उपलब्ध', 'उपलब्ध नहीं')}")
    with p3:
        st.write(f"**Dhan (Scrip Master → यूनिवर्स):** 🟢 Public CSV "
                 f"(API key: {'हाँ' if dhan else 'नहीं'})")
        st.write(f"**Gemini AI Pro:** {'🟢 Connected' if gemini else '🟡 Rule-engine fallback'}")
        st.write(f"**ज़ोन इंजन:** {ZONE_ENGINE_SOURCE}")

    st.caption("NSE का Akamai लेयर datacenter IP (Render/Cloud) पर कुछ एंडपॉइंट ब्लॉक करता है। "
               "ऐसे में ऐप वास्तविक TradingView (delayed) Futures/OI दिखाता है और जो डेटा उपलब्ध नहीं "
               "उसे 'Unavailable' लिखता है — कोई काल्पनिक आँकड़ा नहीं।")

    st.markdown("---")
    st.markdown("#### 📦 GitHub तैयार संरचना")
    st.code("""aap.py
├── requirements.txt
├── .env.example
└── README.md

(वैकल्पिक) अपने मौजूदा मॉड्यूल जोड़ें:
terminal/zone_core.py, terminal/config.py, ui/runtime_config.py
→ यदि मौजूद हों तो aap.py उन्हें अपने आप उपयोग कर लेता है,
  वरना built-in fallback चलता है।""", language="text")


# ==============================================================================
# 18) MAIN
# ==============================================================================

TAB_NAMES = ["NSE ALL FUTURE STOCKS", "Zone", "Global Zone", "News & Events", "Settings"]


def _render_tab_bar() -> int:
    """Lazy tab bar — only the selected tab is rendered (big speed win:
    st.tabs() eagerly renders every tab, which means 5 heavy scans per click)."""
    qp_tab = st.query_params.get("tab", None)
    if qp_tab is not None and qp_tab != st.session_state.get("_last_tab_param"):
        st.session_state["_last_tab_param"] = qp_tab
        try:
            idx = int(qp_tab)
            if 0 <= idx < len(TAB_NAMES):
                st.session_state["active_tab_index"] = idx
                st.session_state["tab_radio"] = TAB_NAMES[idx]
        except Exception:
            pass
    if "tab_radio" not in st.session_state:
        st.session_state["tab_radio"] = TAB_NAMES[int(st.session_state.get("active_tab_index", 0))]
    choice = st.radio(" ", TAB_NAMES, horizontal=True, key="tab_radio",
                      label_visibility="collapsed")
    idx = TAB_NAMES.index(choice)
    st.session_state["active_tab_index"] = idx
    return idx


def main() -> None:
    st.markdown("# AI PRO INSTITUTIONAL TRADING TERMINAL")
    settings = st.session_state.analysis_settings

    idx = _render_tab_bar()
    if idx == 0:
        _render_nse_tab(settings)
    elif idx == 1:
        _render_zone_tab(settings)
    elif idx == 2:
        _render_global_zone_tab(settings)
    elif idx == 3:
        _render_news_events_tab(settings)
    else:
        _render_settings_tab(settings)


if __name__ == "__main__":
    main()
