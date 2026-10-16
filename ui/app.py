"""
================================================================================
 AI PRO INSTITUTIONAL TRADING TERMINAL  —  aap.py  (SINGLE-FILE EDITION)
================================================================================
 यह ONE FILE है। Repo root पर रखिए और चलाइए:

     streamlit run aap.py --server.port=$PORT --server.address=0.0.0.0
     (Streamlit Cloud → Main file path: aap.py)

 क्रम (priority) — "पहले आपका अपना repo, फिर inline fallback":
   1) यदि host repo में ये modules उपलब्ध हैं तो वही असली engine इस्तेमाल होता है:
        terminal.zone_core            → Pine-parity ज़ोन engine (Fresh/Tested/Broken)
        terminal.services.zones       → ज़ोन + pattern + score + position sizing
        terminal.services.indicators  → EMA 35/100, VWAP, ATR (deterministic)
        terminal.services.signals     → transparent weighted scoring + hard filters
        terminal.services.risk        → capital/risk/lot-based position sizing
        terminal.services.options     → वास्तविक option chain → PCR (PE OI / CE OI)
        terminal.services.candles     → DhanHQ candles (bounded cache)
        terminal.services.instruments → Dhan instrument master → NSE F&O universe
        terminal.services.market      → verified futures snapshot (DhanHQ)
        terminal.providers.dhan       → DhanHQ quotes / OI / option chain
        terminal.config               → DEFAULT_ANALYSIS_SETTINGS + global catalog
        terminal.timeframes           → resample / completed bars / volume spike
        ui.runtime_config             → Streamlit Cloud secrets → env
   2) कोई module न हो → इसी फ़ाइल का inline fallback चलता है
      (Yahoo OHLCV + ज़ोन + technicals) और F&O / OI / PCR / Delivery
      साफ़ "Unavailable" दिखाता है — कोई काल्पनिक आँकड़ा कभी नहीं।

 सुरक्षा: कोई API key कोड में नहीं — केवल .env / Render / Streamlit secrets.
================================================================================
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from importlib import import_module
from typing import Any
from urllib.parse import quote as url_quote
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import streamlit as st

IST = ZoneInfo("Asia/Kolkata")
NA = "Unavailable"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

st.set_page_config(
    page_title="AI Pro Institutional Trading Terminal",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

ALL_TIMEFRAMES: list[str] = [
    "15m", "30m", "75m", "1h", "2h", "4h", "6h", "8h", "Daily", "Weekly", "Monthly",
]


# ==============================================================================
# 0A) SECRETS  (env / .env / Streamlit secrets — कोड में key कभी नहीं)
# ==============================================================================

def _load_dotenv() -> None:
    """Fallback: repo root की .env फ़ाइल env में (existing env को override नहीं)।"""
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(base, ".env")
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception:
        pass


_load_dotenv()

# --- Streamlit Cloud secrets → env (flat keys + sectioned TOML, दोनों) --------
_SECRET_ENV_NAMES = (
    "DHAN_CLIENT_ID", "DHAN_ACCESS_TOKEN", "DHAN_BASE_URL", "DHAN_MASTER_URL",
    "GEMINI_API_KEY", "GEMINI_MODEL", "APP_ADMIN_TOKEN", "YAHOO_RESEARCH_ENABLED",
    "GITHUB_TOKEN", "YAHOO_API_KEY",
)
# section → {alias: ENV_NAME}
_SECTION_ALIASES: dict[str, dict[str, str]] = {
    "dhan": {"client_id": "DHAN_CLIENT_ID", "clientid": "DHAN_CLIENT_ID",
             "client": "DHAN_CLIENT_ID", "id": "DHAN_CLIENT_ID",
             "access_token": "DHAN_ACCESS_TOKEN", "accesstoken": "DHAN_ACCESS_TOKEN",
             "token": "DHAN_ACCESS_TOKEN", "base_url": "DHAN_BASE_URL",
             "master_url": "DHAN_MASTER_URL"},
    "gemini": {"api_key": "GEMINI_API_KEY", "apikey": "GEMINI_API_KEY",
               "key": "GEMINI_API_KEY", "token": "GEMINI_API_KEY",
               "model": "GEMINI_MODEL"},
}
_SECTION_ALIASES["dhanhq"] = _SECTION_ALIASES["dhan"]
_SECTION_ALIASES["dhan_hq"] = _SECTION_ALIASES["dhan"]
_SECTION_ALIASES["dhan_broker"] = _SECTION_ALIASES["dhan"]
_SECTION_ALIASES["broker"] = _SECTION_ALIASES["dhan"]
_SECTION_ALIASES["google"] = _SECTION_ALIASES["gemini"]
_SECTION_ALIASES["llm"] = _SECTION_ALIASES["gemini"]
_GENERIC_KEYS = {"api_key": "API_KEY", "access_token": "ACCESS_TOKEN",
                 "client_id": "CLIENT_ID", "token": "TOKEN", "model": "MODEL"}


def _apply_streamlit_secrets_robust() -> int:
    """Streamlit secrets → env (मान env में पहले मौजूद हो तो override नहीं)।
    दोनों styles support: flat keys (DHAN_CLIENT_ID = "...") और sectioned TOML
    ([dhan] client_id = "...")। key कभी render/log नहीं होती।"""
    applied = 0
    try:
        secrets = st.secrets
    except Exception:
        return 0
    # 1) flat keys
    for name in _SECRET_ENV_NAMES:
        if os.environ.get(name, "").strip():
            continue
        try:
            value = secrets.get(name, "")
        except Exception:
            value = ""
        if value is not None and str(value).strip():
            os.environ[name] = str(value).strip()
            applied += 1
    # 2) sectioned TOML
    try:
        keys = list(secrets.keys())
    except Exception:
        keys = []
    for section in keys:
        try:
            block = secrets[section]
        except Exception:
            continue
        if not hasattr(block, "keys"):
            continue
        sec = str(section).strip().lower()
        mapping = dict(_SECTION_ALIASES.get(sec, {}))
        try:
            for sub in list(block.keys()):
                mapping.setdefault(str(sub).strip().lower(),
                                   f"{sec.upper()}_{str(sub).strip().upper()}")
        except Exception:
            pass
        for sub in list(block.keys()):
            alias = str(sub).strip().lower()
            env_name = mapping.get(alias)
            if not env_name:
                continue
            try:
                value = block[sub]
            except Exception:
                continue
            if value is None or not isinstance(value, (str, int, float, bool)):
                continue
            text = str(value).strip()
            if not text or os.environ.get(env_name, "").strip():
                continue
            os.environ[env_name] = text
            applied += 1
    return applied


_SECRETS_APPLIED = _apply_streamlit_secrets_robust()

_HAS_HOST_SECRETS = False
try:  # host repo का secrets loader (Streamlit Cloud secrets → env)
    from ui.runtime_config import apply_streamlit_secrets as _host_apply_secrets

    _host_apply_secrets()
    _HAS_HOST_SECRETS = True
except Exception:
    pass


def _secret_status() -> dict:
    """कौन-से secrets मौजूद हैं — सिर्फ़ हाँ/नहीं (कभी key नहीं दिखती)।"""
    dhan = bool(os.environ.get("DHAN_CLIENT_ID", "").strip()
                and os.environ.get("DHAN_ACCESS_TOKEN", "").strip())
    gemini = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    return {"dhan": dhan, "gemini": gemini, "applied": _SECRETS_APPLIED,
            "host_loader": _HAS_HOST_SECRETS}


# ==============================================================================
# 0B) HOST REPO MODULES (optional) — "user's own repo first"
# ==============================================================================

def _mod(name: str):
    try:
        return import_module(name)
    except Exception:
        return None


_user_zone_core = _mod("terminal.zone_core")
_user_svc_zones = _mod("terminal.services.zones")
_user_svc_indicators = _mod("terminal.services.indicators")
_user_svc_signals = _mod("terminal.services.signals")
_user_svc_risk = _mod("terminal.services.risk")
_user_svc_options = _mod("terminal.services.options")
_user_svc_candles = _mod("terminal.services.candles")
_user_svc_instruments = _mod("terminal.services.instruments")
_user_svc_market = _mod("terminal.services.market")
_user_svc_gemini = _mod("terminal.services.gemini")
_user_provider_dhan = _mod("terminal.providers.dhan")
_user_config = _mod("terminal.config")
_user_timeframes = _mod("terminal.timeframes")

_HAS_USER_ZONE = _user_zone_core is not None
_HAS_USER_ZONES_SVC = _user_svc_zones is not None
_HAS_USER_IND = _user_svc_indicators is not None
_HAS_USER_SIGNALS = _user_svc_signals is not None
_HAS_USER_RISK = _user_svc_risk is not None
_HAS_USER_OPTIONS = _user_svc_options is not None
_HAS_USER_CANDLES = _user_svc_candles is not None
_HAS_USER_INSTRUMENTS = _user_svc_instruments is not None
_HAS_USER_MARKET = _user_svc_market is not None
_HAS_USER_GEMINI = _user_svc_gemini is not None
_HAS_USER_DHAN = _user_provider_dhan is not None
_HAS_USER_CONFIG = _user_config is not None
_HAS_USER_TIMEFRAMES = _user_timeframes is not None


def _f(value: Any) -> float | None:
    """Safe float — 'Unavailable'/None/NaN → None (कभी exception नहीं)।"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = (value.replace(",", "").replace("%", "").replace("₹", "")
                 .replace("+", "").strip())
        if value in ("", "-", "--", "NaN", "nan", "None", NA, "null"):
            return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(out) or math.isinf(out):
        return None
    return out


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return pd.Timestamp(value).isoformat()
    except Exception:
        return None


def _pct(value: Any) -> str:
    num = _f(value)
    return f"{num:+.2f}%" if num is not None else NA


def _num(value: Any, digits: int = 2, prefix: str = "") -> str:
    num = _f(value)
    return f"{prefix}{num:,.{digits}f}" if num is not None else NA


# ==============================================================================
# 0C) SETTINGS  (host repo की DEFAULT_ANALYSIS_SETTINGS + inline defaults)
# ==============================================================================

_BASE_SETTINGS: dict[str, Any] = {
    "eod_high_buffer_pct": 10.0, "eod_low_buffer_pct": 10.0,
    "distal_buffer_pct": 0.5, "use_eod_range": True,
    "account_capital": 25000.0, "risk_pct": 0.5, "target_rr": 5.0,
    "selected_tf": "Daily", "mcap_filter": "All",
    "custom_tfs": list(ALL_TIMEFRAMES),
    "scan_limit": 0, "enrich_limit": 60,
    "vol_spike_threshold": 2.1, "use_llm": True,
    "ema_fast": 35, "ema_slow": 100,
    "pcr_scope": "selected_atm_5_each_side",
    "futures_contract": "front", "exclude_test_instruments": True,
}

_HOST_KEY_MAP = {
    "eod_high_buffer_pct": "eod_high_buffer_pct",
    "eod_low_buffer_pct": "eod_low_buffer_pct",
    "use_eod_range": "use_eod_range",
    "account_capital": "account_capital",
    "risk_pct": "risk_pct",
    "target_rr": "target_rr",
    "volume_spike_threshold": "vol_spike_threshold",
    "ema_fast": "ema_fast",
    "ema_slow": "ema_slow",
    "pcr_scope": "pcr_scope",
    "futures_contract": "futures_contract",
    "exclude_test_instruments": "exclude_test_instruments",
}


def _default_settings() -> dict[str, Any]:
    settings = dict(_BASE_SETTINGS)
    if _HAS_USER_CONFIG:
        try:
            host = dict(getattr(_user_config, "DEFAULT_ANALYSIS_SETTINGS", {}) or {})
            for key, value in host.items():
                mapped = _HOST_KEY_MAP.get(key)
                if mapped and value is not None:
                    settings[mapped] = value
            zone_tf = host.get("zone_timeframe")
            if zone_tf in ALL_TIMEFRAMES:
                # ज़ोन इंजन की अपनी TF रखी जाती है; मास्टर टैब का default "Daily"
                # (ज़्यादा इतिहास → ज़्यादा सक्रिय ज़ोन) — टैब में selector से बदला जा सकता है।
                settings["zone_timeframe"] = zone_tf
            custom = host.get("custom_timeframes") or host.get("indicator_timeframes") or []
            tfs = [t for t in custom if isinstance(t, str) and t in ALL_TIMEFRAMES]
            if tfs:
                merged = list(dict.fromkeys(list(ALL_TIMEFRAMES) + tfs))
                settings["custom_tfs"] = merged
        except Exception:
            pass
    return settings


DEFAULT_ANALYSIS_SETTINGS = _default_settings()


# ==============================================================================
# 0D) FORMATTING HELPERS (body इन्हीं का उपयोग करता है)
# ==============================================================================

def fmt_num(value: Any, digits: int = 2, prefix: str = "") -> str:
    if isinstance(value, str):
        return value
    return _num(value, digits, prefix)


def fmt_pct(value: Any, digits: int = 2, signed: bool = True) -> str:
    if isinstance(value, str):
        return value
    num = _f(value)
    if num is None:
        return NA
    return f"{num:{'+' if signed else ''}.{digits}f}%"


def linkify_table(frame, spot_col="Symbol", spot_url="_Spot_Chart_URL",
                  fut_col="Futures LTP", fut_url="_Fut_Chart_URL",
                  bias_col="Final AI Bias", bias_url="_Bias_Link_URL"):
    display = frame.copy()
    if spot_col in display.columns and spot_url in display.columns:
        display[spot_col] = display[spot_url].fillna("").astype(str)
        display = display.drop(columns=[spot_url])
    if fut_col in display.columns and fut_url in display.columns:
        mask = display[fut_col].astype(str).str.strip() != NA
        display.loc[mask, fut_col] = display.loc[mask, fut_url].fillna("").astype(str)
        display = display.drop(columns=[fut_url])
    if bias_col in display.columns and bias_url in display.columns:
        display[bias_col] = display[bias_url].fillna("").astype(str)
        display = display.drop(columns=[bias_url])
    return display


def row_styles(row):
    styles = []
    change = _f(row.get("Spot Change %")) or 0.0
    p_color = "#00e676" if change >= 0 else "#ff5252"
    for col in row.index:
        if col in ("Spot LTP", "Spot Change %", "Futures Change %"):
            styles.append(f"color: {p_color}; font-weight: bold")
        elif col == "Dif. %":
            d = _f(str(row.get(col, "")).replace("%", "").replace("+", ""))
            styles.append(f"color: {'#00e676' if (d is not None and d >= 0) else '#ff5252'}; font-weight: bold")
        elif col == "Buildup Classification":
            b = str(row.get(col, ""))
            color = {"Long Buildup": "#00e676", "Short Covering": "#69f0ae",
                     "Short Buildup": "#ff5252", "Long Unwinding": "#ff7043"}.get(b, "")
            styles.append(f"color: {color}; font-weight: bold" if color else "")
        elif col == "Final AI Bias":
            b = str(row.get(col, ""))
            color = "#00e676" if "BUY" in b else ("#ff5252" if "SELL" in b else "#8ea5c0")
            styles.append(f"color: {color}; font-weight: bold")
        elif col == "Live Signals Highlight" and str(row.get(col, "")):
            styles.append("background-color: rgba(57,167,255,0.15); color: #82b1ff; font-weight: bold")
        else:
            styles.append("")
    return styles


def zone_styles(row):
    styles = []
    change = _f(row.get("_Change_Pct")) or 0.0
    color = "#00e676" if change >= 0 else "#ff5252"
    for col in row.index:
        if col == "LTP":
            styles.append(f"color: {color}; font-weight: bold")
        elif col in ("demand/Supply", "Demand/Supply"):
            styles.append(f"color: {'#00e676' if str(row.get(col)).lower().startswith('dem') else '#ff5252'}; font-weight: bold")
        elif col == "AI Bias":
            b = str(row.get(col, ""))
            styles.append(f"color: {'#00e676' if 'BUY' in b else ('#ff5252' if 'SELL' in b else '#8ea5c0')}; font-weight: bold")
        elif col == "Zone State":
            styles.append("color: #82b1ff;")
        else:
            styles.append("")
    return styles


def global_styles(row):
    return zone_styles(row)


def master_column_config(st):
    """मास्टर तालिका का column_config — clickable cells + साफ़ नंबर फ़ॉर्मैट।"""
    try:
        cfg = {
            "Symbol": st.column_config.LinkColumn(
                "Symbol", display_text=r".*symbol=NSE%3A([^&#]+).*",
                help="TradingView चार्ट (spot)"),
            "Futures LTP": st.column_config.LinkColumn(
                "Futures LTP", display_text=r".*#price=(.*)",
                help="TradingView चार्ट (futures)"),
            "Final AI Bias": st.column_config.LinkColumn(
                "Final AI Bias", display_text=r".*&b=([A-Z ]+)&t=.*",
                help="इस सेल पर क्लिक करें → पूरा हिंदी AI विश्लेषण (modal)"),
            "Spot LTP": st.column_config.NumberColumn("Spot LTP", format="₹%.2f"),
            "Dif. %": st.column_config.TextColumn("Dif. %", help="Futures vs Spot premium/discount"),
            "Spot Change %": st.column_config.NumberColumn("Spot Change %", format="%.2f%%"),
            "Futures Change %": st.column_config.TextColumn("Futures Change %"),
            "Futures OI": st.column_config.TextColumn("Futures OI", help="Open Interest (contracts)"),
            "Futures OI Change %": st.column_config.TextColumn("Futures OI Change %"),
            "Selected-Timeframe Volume": st.column_config.NumberColumn(
                "Selected-Timeframe Volume", format="localized"),
            "Volume Spike Ratio": st.column_config.NumberColumn("Volume Spike Ratio", format="%.2fx"),
            "Live Signals Highlight": st.column_config.TextColumn(
                "Live Signals Highlight", help="वॉल्यूम स्पाइक, EMA क्रॉसओवर या ज़ोन निकटता अलर्ट"),
            "Zone Signal": st.column_config.TextColumn("Zone Signal"),
            "Data Source": st.column_config.TextColumn("Data Source"),
        }
        return cfg
    except Exception:
        return {}


def df_layout(st):
    """Streamlit version के अनुसार dataframe width (1.4x → 1.5x+ में 'stretch')।"""
    try:
        version = tuple(int(x) for x in str(st.__version__).split(".")[:2])
        if version >= (1, 49):
            return {"width": "stretch"}
    except Exception:
        pass
    return {"use_container_width": True}


# ==============================================================================
# 1) ZONE MODEL  (host engine + inline fallback — एक ही shape)
# ==============================================================================

@dataclass
class _Zone:
    state: str
    isDemand: bool
    proxVal: float
    distVal: float
    tf: str = "1h"
    tests: int = 0
    index: int = 0
    # host-repo (zone_core) extra fields
    slVal: float | None = None
    tpVal: float | None = None
    pattern: str | None = None
    category: str | None = None
    score: float | None = None
    score10: float | None = None
    hq: bool = False
    created_at: str | None = None
    risk_pct: float | None = None
    source: str = "inline zone engine"


def _adapt_user_zone(zone: Any, tf: str) -> _Zone:
    return _Zone(
        state=str(getattr(zone, "state", NA)),
        isDemand=bool(getattr(zone, "isDemand", True)),
        proxVal=_f(getattr(zone, "proxVal", 0.0)) or 0.0,
        distVal=_f(getattr(zone, "distVal", 0.0)) or 0.0,
        tf=tf,
        tests=int(getattr(zone, "touchCount", 0) or 0),
        index=int(getattr(zone, "createdBarIndex", 0) or 0),
        slVal=_f(getattr(zone, "slVal", None)),
        tpVal=_f(getattr(zone, "tpVal", None)),
        pattern=getattr(zone, "patternType", None),
        category=getattr(zone, "zoneCategory", None),
        score=_f(getattr(zone, "densityScore", None)),
        score10=_f(getattr(zone, "score10", None)),
        hq=bool(getattr(zone, "isHQ", False)),
        created_at=_iso(getattr(zone, "timestamp", None)),
        risk_pct=_f(getattr(zone, "riskPct", None)),
        source="terminal/zone_core.py (Pine-parity)",
    )


def _fallback_scan_zones(df, params: dict | None = None, tf: str = "1h") -> list[_Zone]:
    """Inline OHLCV zone engine — तब चलता है जब host repo का zone_core न हो।"""
    params = params or {}
    if df is None or len(df) < 12:
        return []
    try:
        o = df["open"].to_numpy(float); h = df["high"].to_numpy(float)
        l = df["low"].to_numpy(float); c = df["close"].to_numpy(float)
    except Exception:
        return []
    n = len(c); s = 2
    zones: list[_Zone] = []
    hi_lim, lo_lim = float("inf"), float("-inf")
    if params.get("useEodRange", True):
        try:
            hi_lim = float(h[-1]) * (1 + float(params.get("eodHighBufferPct", 10.0)) / 100)
            lo_lim = float(l[-1]) * (1 - float(params.get("eodLowBufferPct", 10.0)) / 100)
        except Exception:
            pass
    for i in range(s, n - s):
        wh = h[i - s: i + s + 1]; wl = l[i - s: i + s + 1]
        pl = bool(l[i] == wl.min() and (wl == l[i]).sum() == 1)
        ph = bool(h[i] == wh.max() and (wh == h[i]).sum() == 1)
        if not (pl or ph):
            continue
        if pl:
            distal = float(l[i]); prox = float(min(o[i], c[i]))
            broken = bool((c[i + 1:] < distal).any()); touched = l[i + 1:] <= prox
        else:
            distal = float(h[i]); prox = float(max(o[i], c[i]))
            broken = bool((c[i + 1:] > distal).any()); touched = h[i + 1:] >= prox
        state = "Broken" if broken else ("Fresh" if touched.sum() == 0 else "Tested")
        if params.get("useEodRange", True):
            prox = (max(min(prox, hi_lim), lo_lim) if pl
                    else min(max(prox, lo_lim), hi_lim))
            prox = max(prox, distal) if pl else min(prox, distal)
        zones.append(_Zone(state=state, isDemand=pl, proxVal=round(prox, 4),
                           distVal=round(distal, 4), tf=tf, tests=int(touched.sum()),
                           index=i, source="inline OHLCV zone engine"))
    return zones


def zone_params(settings: dict) -> dict:
    """Settings → zone_core parameters (host repo के PINE_DEFAULTS keys)।"""
    return {
        "eodHighBufferPct": float(settings.get("eod_high_buffer_pct", 10.0)),
        "eodLowBufferPct": float(settings.get("eod_low_buffer_pct", 10.0)),
        "useEodRange": bool(settings.get("use_eod_range", True)),
        "accountCapital": float(settings.get("account_capital", 25000.0)),
        "riskPct": float(settings.get("risk_pct", 0.5)),
        "targetRR": float(settings.get("target_rr", 5.0)),
    }


def scan_zones(df, params: dict | None = None, tf: str = "1h") -> list[_Zone]:
    """Host repo का असली zone_core पहले, वरना inline engine।"""
    if _HAS_USER_ZONE:
        try:
            zones = _user_zone_core.scan_zones(df, params=params or {}, tf=tf)
            return [_adapt_user_zone(z, tf) for z in (zones or [])]
        except Exception:
            pass
    return _fallback_scan_zones(df, params, tf)


def latest_active_zones(zones) -> list[_Zone]:
    """Fresh/Tested = सक्रिय (दोनों engines में समान semantics)।"""
    out = []
    for z in zones or []:
        state = str(getattr(z, "state", "")).strip().lower()
        if state in ("fresh", "tested"):
            out.append(z)
    return out


def pick_nearest(zones, price):
    """सबसे नज़दीकी सक्रिय ज़ोन (proximal line के अनुसार)।"""
    best, best_dist = None, None
    for z in latest_active_zones(zones):
        prox = _f(getattr(z, "proxVal", None))
        if prox is None:
            continue
        dist = abs(float(price) - prox)
        if best_dist is None or dist < best_dist:
            best, best_dist = z, dist
    return best, best_dist


# ==============================================================================
# 2) INDICATORS  (host repo का deterministic engine पहले)
# ==============================================================================

def _fallback_compute_indicators(df, vol_threshold: float = 2.1) -> dict:
    out = {"close": None, "prev_close": None, "change_pct": None, "open": None,
           "ema35": None, "ema100": None, "vwap": None, "atr": None,
           "vol_ratio": 1.0, "volume": 0, "avg_volume": 0.0,
           "ema35_signal": None, "ema100_signal": None, "vwap_signal": None,
           "gap": "Normal", "vol_spike": False, "structure": NA}
    if df is None or df.empty or len(df) < 2:
        return out
    try:
        close = float(df["close"].iloc[-1])
        prev = float(df["close"].iloc[-2])
        out.update(close=close, prev_close=prev,
                   change_pct=((close - prev) / prev * 100.0) if prev else 0.0,
                   open=float(df["open"].iloc[-1]))
        out["ema35"] = _ema(df["close"], 35)
        out["ema100"] = _ema(df["close"], 100)
        out["vwap"] = _vwap_last_session(df)
        out["atr"] = _atr(df)
        ratio, cur_vol, avg_vol = _volume_spike(df)
        out.update(vol_ratio=ratio, volume=cur_vol, avg_volume=avg_vol,
                   vol_spike=ratio >= float(vol_threshold))
        if out["ema35"] is not None:
            out["ema35_signal"] = "+" if close > out["ema35"] else "-"
        if out["ema100"] is not None:
            out["ema100_signal"] = "+" if close > out["ema100"] else "-"
        if out["vwap"] is not None:
            out["vwap_signal"] = "+" if close > out["vwap"] else "-"
    except Exception:
        pass
    return out


def _ema(series, span: int) -> float | None:
    try:
        if series is None or len(series) < span:
            return None
        value = float(series.ewm(span=span, adjust=False).mean().iloc[-1])
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _vwap_last_session(df) -> float | None:
    try:
        if df is None or len(df) < 2:
            return None
        if isinstance(df.index, pd.DatetimeIndex):
            last = df.index[-1].date()
            day = df[[ts.date() == last for ts in df.index]]
        else:
            day = df.iloc[-1:]
        typ = (day["high"] + day["low"] + day["close"]) / 3.0
        vol = day["volume"].fillna(0).to_numpy(dtype=float)
        if vol.sum() <= 0:
            return float(typ.iloc[-1])
        return float((typ * vol).sum() / vol.sum())
    except Exception:
        return None


def _atr(df, period: int = 14) -> float | None:
    try:
        if df is None or len(df) < period + 2:
            return None
        prev = df["close"].shift(1)
        tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(),
                        (df["low"] - prev).abs()], axis=1).max(axis=1)
        value = float(tr.rolling(period).mean().iloc[-1])
        return value if value > 0 else None
    except Exception:
        return None


def _volume_spike(df, lookback: int = 20):
    try:
        vol = df["volume"].fillna(0).astype(float)
        cur = float(vol.iloc[-1])
        hist = vol.iloc[-lookback - 1: -1]
        avg = float(hist.mean()) if len(hist) else cur
        return (round(cur / avg, 2) if avg > 0 else 1.0), int(cur), avg
    except Exception:
        return 1.0, 0, 0.0


def compute_indicators(df, vol_threshold: float = 2.1, tf: str | None = None,
                       settings: dict | None = None) -> dict:
    """Host repo का deterministic indicator engine पहले, वरना inline।"""
    out = _fallback_compute_indicators(df, vol_threshold)
    if _HAS_USER_IND and tf and df is not None and not df.empty:
        try:
            user = _user_svc_indicators.compute_indicators(
                df, tf,
                ema_fast=int((settings or {}).get("ema_fast", 35)),
                ema_slow=int((settings or {}).get("ema_slow", 100)),
            )
            if isinstance(user, dict) and user.get("status") in ("AVAILABLE", "PARTIAL"):
                mapping = {
                    "ema_fast": "ema35", "ema_slow": "ema100", "vwap": "vwap",
                    "atr": "atr", "ema_fast_signal": "ema35_signal",
                    "ema_slow_signal": "ema100_signal", "vwap_signal": "vwap_signal",
                    "last_close": "close", "market_structure": "structure",
                }
                for src_key, dst_key in mapping.items():
                    value = user.get(src_key)
                    if value is None:
                        continue
                    if dst_key == "structure":
                        out[dst_key] = str(value)
                    else:
                        num = _f(value)
                        if num is not None:
                            out[dst_key] = num
                if out.get("ema35") is not None and out.get("close") is not None:
                    out["ema35_signal"] = "+" if out["close"] > out["ema35"] else "-"
                if out.get("ema100") is not None and out.get("close") is not None:
                    out["ema100_signal"] = "+" if out["close"] > out["ema100"] else "-"
        except Exception:
            pass
    return out


# ==============================================================================
# 3) RISK / POSITION SIZING  (host repo का lot-aware engine पहले)
# ==============================================================================

@dataclass
class _RiskSetup:
    entry: float
    sl: float
    target: float
    qty: int
    risk_budget: float
    per_unit_risk: float
    rr: float
    capital: float
    basis: str
    lots: int | None = None
    lot_size: float | None = None

    def to_dict(self) -> dict:
        return {"entry": self.entry, "sl": self.sl, "target": self.target,
                "qty": self.qty, "risk_budget": self.risk_budget,
                "per_unit_risk": self.per_unit_risk, "rr": self.rr,
                "capital": self.capital, "basis": self.basis,
                "lots": self.lots, "lot_size": self.lot_size}


def build_risk_setup(close, zone, atr_value, settings: dict, is_long: bool,
                     lot_size: float | None = None) -> _RiskSetup:
    """Entry/SL/Target + capital-risk based position size (host risk engine पहले)।"""
    capital = float(settings.get("account_capital", 25000.0))
    risk_pct = float(settings.get("risk_pct", 0.5))
    rr = float(settings.get("target_rr", 5.0))
    budget = capital * risk_pct / 100.0
    entry = _f(close) or 0.0
    sl: float | None = None
    target: float | None = None
    basis = "ATR (14) आधारित स्टॉप"

    prox = _f(getattr(zone, "proxVal", None)) if zone is not None else None
    distal = _f(getattr(zone, "distVal", None)) if zone is not None else None
    if prox is not None and distal is not None:
        entry = prox
        sl = distal
        basis = "सक्रिय ज़ोन की proximal/distal लाइन"
    zone_sl = _f(getattr(zone, "slVal", None)) if zone is not None else None
    zone_tp = _f(getattr(zone, "tpVal", None)) if zone is not None else None
    if zone_sl and zone_tp:
        sl, target = zone_sl, zone_tp
        basis = "zone_core slVal/tpVal (Pine-parity)"
    if sl is None or abs(entry - sl) < 1e-6:
        atr_num = _f(atr_value)
        if atr_num and atr_num > 0:
            sl = entry - atr_num * 1.5 if is_long else entry + atr_num * 1.5
        else:
            sl = entry * 0.98 if is_long else entry * 1.02
        basis = "ATR (14) आधारित स्टॉप"
    per_unit = abs(entry - sl) or 1.0
    if target is None:
        target = entry + per_unit * rr if is_long else entry - per_unit * rr
    qty = int(math.floor(budget / per_unit)) if per_unit > 0 else 0
    lots = None
    if _HAS_USER_RISK and lot_size:
        try:
            est = _user_svc_risk.estimate_futures_position(
                capital, risk_pct, entry, sl, lot_size)
            if isinstance(est, dict) and est.get("status") in ("ESTIMATE_ONLY", "RISK_BUDGET_BELOW_ONE_LOT"):
                lots = int(est.get("suggested_lots") or 0)
                quantity = est.get("suggested_quantity")
                if quantity:
                    qty = int(quantity)
                basis += " · lot-based sizing (terminal.services.risk)"
        except Exception:
            pass
    return _RiskSetup(round(entry, 2), round(sl, 2), round(target, 2), qty,
                      round(budget, 2), round(per_unit, 2), rr, capital, basis,
                      lots=lots, lot_size=lot_size)


# ==============================================================================
# 4) SCORING  (host repo का transparent weighted score पहले)
# ==============================================================================

def _host_score(features: dict, risk_reward_ratio: float | None = None) -> dict | None:
    """terminal.services.signals.score_setup → dict (या None)।"""
    if not _HAS_USER_SIGNALS:
        return None
    try:
        result = _user_svc_signals.score_setup(features, risk_reward_ratio=risk_reward_ratio)
        return result if isinstance(result, dict) else None
    except Exception:
        try:
            result = _user_svc_signals.score_setup(features)
            return result if isinstance(result, dict) else None
        except Exception:
            return None


def score_setup(features: dict, risk_reward_ratio: float | None = None) -> dict:
    """Evidence-based hypothesis + risk gate।

    * host repo का weighted score उपलब्ध हो → उसी के thresholds प्रयोग;
    * evidence न हो (data missing) → NO TRADE (fail-closed);
    * risk gate (जैसे F&O ban status unverified) bias नहीं बदलता, पर modal/table
      में साफ़ दिखाया जाता है।
    """
    host = _host_score(features, risk_reward_ratio)
    if host:
        blockers = [b for b in (host.get("hard_filters") or [])]
        evidence_blocked = any(
            str(b).startswith("Required evidence missing") or str(b).startswith("Risk/Reward")
            for b in blockers
        )
        bull = _f(host.get("bullish_score")) or 0.0
        bear = _f(host.get("bearish_score")) or 0.0
        weights = dict(getattr(_user_svc_signals, "FEATURE_WEIGHTS", {}) or {})
        available = sum(int(weights.get(key, 0)) for key, value in features.items()
                        if value in (-1, 0, 1)) or 1
        bull_ratio = bull / available
        bear_ratio = bear / available
        net_ratio = (bull - bear) / available
        if evidence_blocked:
            bias = "NO TRADE"
        elif bull_ratio >= 0.75 and net_ratio >= 0.25:
            bias = "STRONG BUY"
        elif bull_ratio >= 0.55 and net_ratio >= 0.15:
            bias = "BUY"
        elif bear_ratio >= 0.75 and net_ratio <= -0.25:
            bias = "STRONG SELL"
        elif bear_ratio >= 0.55 and net_ratio <= -0.15:
            bias = "SELL"
        else:
            bias = "NO TRADE"
        return {
            "bias": bias,
            "bullish_score": bull,
            "bearish_score": bear,
            "setup_quality_score": _f(host.get("setup_quality_score")),
            "data_quality_score": _f(host.get("data_quality_score")),
            "evidence_coverage_pct": round(available / max(1, sum(weights.values())) * 100),
            "bullish_ratio": round(bull_ratio * 100, 1),
            "bearish_ratio": round(bear_ratio * 100, 1),
            "net_ratio": round(net_ratio * 100, 1),
            "why_buy": list(host.get("why_buy") or []),
            "why_sell": list(host.get("why_sell") or []),
            "what_invalidates": host.get("what_invalidates"),
            "main_risk": host.get("main_risk"),
            "risk_gate": blockers,
            "missing_features": list(host.get("missing_features") or []),
            "explanation": host.get("signal_explanation"),
            "source": "terminal.services.signals.score_setup",
        }

    # --- inline weighted score (host repo बिना) ---------------------------
    weights = {
        "zone_quality": 12, "entry_confirmation": 10, "volume_expansion": 8,
        "futures_oi_behavior": 8, "ema_alignment": 7, "vwap_alignment": 5,
        "intraday_structure": 10, "risk_reward": 4, "higher_timeframe_structure": 12,
        "nifty_context": 4, "global_macro": 3,
    }
    bull = bear = 0.0
    available = 0
    why_buy: list[str] = []
    why_sell: list[str] = []
    missing: list[str] = []
    for name, weight in weights.items():
        value = features.get(name)
        if value not in (-1, 0, 1):
            missing.append(name)
            continue
        available += weight
        if value > 0:
            bull += weight
            why_buy.append(f"{name}: bullish evidence")
        elif value < 0:
            bear += weight
            why_sell.append(f"{name}: bearish evidence")
    available = available or 1
    bull_ratio, bear_ratio = bull / available, bear / available
    net_ratio = (bull - bear) / available
    if bull_ratio >= 0.75 and net_ratio >= 0.25:
        bias = "STRONG BUY"
    elif bull_ratio >= 0.55 and net_ratio >= 0.15:
        bias = "BUY"
    elif bear_ratio >= 0.75 and net_ratio <= -0.25:
        bias = "STRONG SELL"
    elif bear_ratio >= 0.55 and net_ratio <= -0.15:
        bias = "SELL"
    else:
        bias = "NO TRADE"
    return {
        "bias": bias, "bullish_score": bull, "bearish_score": bear,
        "setup_quality_score": max(bull, bear),
        "data_quality_score": round(100 * (len(weights) - len(missing)) / len(weights)),
        "evidence_coverage_pct": round(available / sum(weights.values()) * 100),
        "bullish_ratio": round(bull_ratio * 100, 1),
        "bearish_ratio": round(bear_ratio * 100, 1),
        "net_ratio": round(net_ratio * 100, 1),
        "why_buy": why_buy, "why_sell": why_sell,
        "what_invalidates": "Configured zone/structure invalidation.",
        "main_risk": "Market regime and gap risk remain; monitor source freshness.",
        "risk_gate": [], "missing_features": missing,
        "explanation": "Transparent weighted rule score (inline). Not a probability.",
        "source": "inline weighted score",
    }


# ==============================================================================
# 5) DATA SOURCES  (TradingView / NSE public / Yahoo — bounded, cached)
# ==============================================================================

class _TTLCache:
    def __init__(self, ttl: float = 300.0) -> None:
        self.ttl = ttl
        self._data: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: Any):
        with self._lock:
            item = self._data.get(key)
        if not item:
            return None
        stamp, value = item
        if (time.time() - stamp) > self.ttl:
            return None
        return value

    def set(self, key: Any, value: Any) -> None:
        with self._lock:
            self._data[key] = (time.time(), value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


TV_GLOBAL_SCAN = "https://scanner.tradingview.com/global/scan"
TV_INDIA_SCAN = "https://scanner.tradingview.com/india/scan"
TV_FUT_COLUMNS = ("name", "description", "close", "change", "volume", "open_interest",
                  "update_mode", "type")
TV_SPOT_COLUMNS = ("name", "close", "change", "volume", "EMA100", "VWAP",
                   "relative_volume_10d_calc", "market_cap_basic", "sector", "RSI", "ATR")

_NSE_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/get-quotes/derivatives",
}
_nse_cookies: dict[str, str] = {}
_nse_warm = False
_nse_lock = threading.Lock()


def _nse_warmup() -> dict[str, str]:
    global _nse_cookies, _nse_warm
    with _nse_lock:
        if _nse_warm:
            return dict(_nse_cookies)
    cookies: dict[str, str] = {}
    try:
        import httpx

        with httpx.Client(timeout=8.0, headers=_NSE_HEADERS, follow_redirects=True) as client:
            client.get("https://www.nseindia.com", headers={"User-Agent": UA})
            client.get("https://www.nseindia.com/market-data/live-equity-market",
                       headers={"User-Agent": UA})
            cookies = {c.name: c.value for c in client.cookies.jar}
    except Exception:
        cookies = {}
    with _nse_lock:
        _nse_cookies, _nse_warm = cookies, True
    return dict(cookies)


def _nse_get_json(path: str, params: dict | None = None, timeout: float = 7.0):
    """NSE public JSON — उपलब्ध नहीं तो None (कभी exception नहीं)।"""
    try:
        import httpx

        cookies = _nse_warmup()
        with httpx.Client(timeout=timeout, headers=_NSE_HEADERS, cookies=cookies,
                          follow_redirects=True) as client:
            resp = client.get(f"https://www.nseindia.com{path}", params=params or {})
        if resp.status_code == 200:
            try:
                return resp.json()
            except Exception:
                return None
    except Exception:
        return None
    return None


def _probe_caps() -> dict[str, bool]:
    """एक बार probe — कौन से NSE routes इस IP पर काम करते हैं (Settings tab)।"""
    caps = {"derivative": False, "option_chain": False, "equity": False,
            "announcements": False, "fiidii": False, "market_status": False}
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
    try:
        f = _nse_get_json("/api/fiidiiTradeReact", timeout=6.0)
        caps["fiidii"] = isinstance(f, list) and len(f) > 0
    except Exception:
        pass
    try:
        m = _nse_get_json("/api/marketStatus", timeout=6.0)
        caps["market_status"] = isinstance(m, dict) and bool(m.get("marketState"))
    except Exception:
        pass
    return caps


def _tv_scan(tickers: list[str], url: str = TV_GLOBAL_SCAN,
              columns: tuple[str, ...] = TV_FUT_COLUMNS, chunk: int = 60) -> dict[str, dict]:
    """TradingView public screener — datacenter IP पर भी चलने वाला वास्तविक फ़ीड।"""
    out: dict[str, dict] = {}
    if not tickers:
        return out
    try:
        import httpx
    except Exception:
        return out
    headers = {"User-Agent": UA, "Content-Type": "application/json"}
    t0 = time.time()
    for i in range(0, len(tickers), chunk):
        part = tickers[i: i + chunk]
        body = {"symbols": {"tickers": part, "query": {"types": []}}, "columns": list(columns)}
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(url, json=body, headers=headers)
            if resp.status_code != 200:
                continue
            for row in (resp.json() or {}).get("data") or []:
                if not isinstance(row, dict):
                    continue
                vals = row.get("d") or []
                out[str(row.get("s"))] = {c: v for c, v in zip(columns, vals)}
        except Exception:
            continue
    if tickers:
        _record_source("TradingView screener", bool(out),
                       f"{len(out)}/{len(tickers)} rows · {time.time() - t0:.1f}s",
                       time.time() - t0)
    return out


def _yf_download(tickers: list[str], period: str, interval: str):
    """Yahoo OHLCV (वास्तविक डेटा) — yfinance के बाद chart-API fallback।"""
    if not tickers:
        return pd.DataFrame()
    t0 = time.time()
    data = pd.DataFrame()
    try:
        import yfinance as yf

        data = yf.download(tickers, period=period, interval=interval, group_by="ticker",
                           progress=False, threads=True, auto_adjust=False)
    except Exception as exc:
        _record_source("Yahoo Finance (yfinance)", False, f"{type(exc).__name__}")
    if data is None or len(data) == 0:
        frames = _yf_chart_download([t.replace(".NS", "") for t in tickers],
                                    rng=period if period in ("1mo", "3mo", "6mo", "1y", "2y")
                                    else "1y", interval=interval, max_symbols=25)
        if frames:
            _record_source("Yahoo Finance (chart API fallback)", True,
                           f"{len(frames)} symbols · {time.time() - t0:.1f}s", time.time() - t0)
            return frames
        _record_source("Yahoo Finance", False, "कोई OHLCV नहीं मिला", time.time() - t0)
        return pd.DataFrame()
    _record_source("Yahoo Finance (yfinance)", True,
                   f"{len(tickers)} tickers · {period}/{interval} · {time.time() - t0:.1f}s",
                   time.time() - t0)
    return data


_TF_RULES = {
    "15m": "15min", "30m": "30min", "75m": "75min", "1h": "1h", "2h": "2h",
    "4h": "4h", "6h": "6h", "8h": "8h", "Daily": "1D", "Weekly": "W-FRI",
    "Monthly": "ME",
}
# हर timeframe किस base feed से derive होगा (कम downloads → तेज़ scan)
_BASE_FEED = {
    "15m": "15m", "30m": "15m", "75m": "15m",
    "1h": "60m", "2h": "60m", "4h": "60m", "6h": "60m", "8h": "60m",
    "Daily": "1d", "Weekly": "1d", "Monthly": "1d",
}
_YF_INTERVAL = {
    "15m": "15m", "30m": "30m", "75m": "60m", "1h": "60m", "2h": "60m",
    "4h": "60m", "6h": "60m", "8h": "60m", "Daily": "1d", "Weekly": "1wk",
    "Monthly": "1mo", "60m": "60m", "1d": "1d",
}
_YF_PERIOD = {
    "15m": "1mo", "30m": "2mo", "75m": "3mo", "1h": "6mo", "2h": "1y",
    "4h": "1y", "6h": "1y", "8h": "1y", "Daily": "2y", "Weekly": "5y", "Monthly": "10y",
    "60m": "6mo", "1d": "2y",
}


def _resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """OHLCV → timeframe (75m/2h/4h/6h/8h/Weekly/Monthly समर्थित)।"""
    if df is None or df.empty:
        return pd.DataFrame()
    if _HAS_USER_TIMEFRAMES:
        try:
            out = _user_timeframes.resample_ohlcv(df, tf)
            if out is not None and not out.empty:
                return out
        except Exception:
            pass
    rule = _TF_RULES.get(tf)
    if not rule:
        return df
    try:
        agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        out = df.resample(rule).agg(agg)
        if tf == "Daily":
            out = out.dropna(subset=["close"])
        else:
            out = out.dropna(subset=["close", "open", "high", "low"])
        return out
    except Exception:
        try:
            out = df.resample(rule.replace("ME", "M")).agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            return out.dropna(subset=["close"])
        except Exception:
            return df


def _extract_block(data, ticker: str):
    try:
        if data is None or len(data) == 0:
            return None
        if isinstance(data.columns, pd.MultiIndex):
            if ticker in data.columns.get_level_values(0):
                return data[ticker]
            return None
        return data
    except Exception:
        return None


def _normalise_ohlcv(block) -> pd.DataFrame | None:
    try:
        if block is None or len(block) == 0:
            return None
        blk = block.copy()
        blk.columns = [str(c).lower() for c in blk.columns]
        if "close" not in blk.columns:
            return None
        blk = blk.dropna(subset=["close"])
        for col in ("open", "high", "low"):
            if col not in blk.columns:
                blk[col] = blk["close"]
        if "volume" not in blk.columns:
            blk["volume"] = 0.0
        return blk[["open", "high", "low", "close", "volume"]]
    except Exception:
        return None



# ==============================================================================
# 4B) SOURCE HEALTH  (कौन-सा source ज़िंदा है, कितना समय लगा, क्या error हुआ)
# ==============================================================================

_SOURCE_STATUS: dict[str, dict] = {}
_ARTIFACTS: dict[str, Any] = {}      # भारी objects (DataFrame/Zone) — cache के बाहर


def _record_source(name: str, ok: bool, detail: str = "", seconds: float | None = None) -> bool:
    _SOURCE_STATUS[name] = {
        "ok": bool(ok),
        "detail": detail[:220],
        "seconds": round(seconds, 2) if seconds is not None else None,
        "at": datetime.now(IST).strftime("%H:%M:%S"),
    }
    return bool(ok)


def source_health_rows() -> list[dict]:
    rows = []
    try:
        _sec = _secret_status()
        rows.append({
            "Source": "DhanHQ v2 (live futures/OI)",
            "Status": "🟢 उपलब्ध" if _sec["dhan"] else "🔴 उपलब्ध नहीं",
            "Latency (s)": NA,
            "Detail": ("क्रेडेंशियल मौजूद — live quotes/candles/OI चालू"
                       if _sec["dhan"] else
                       "क्रेडेंशियल नहीं (DHAN_CLIENT_ID/DHAN_ACCESS_TOKEN) — "
                       "TradingView + NSE bhavcopy से चल रहा है"),
            "Checked": NA,
        })
    except Exception:
        pass
    for name, info in _SOURCE_STATUS.items():
        rows.append({
            "Source": name,
            "Status": "🟢 उपलब्ध" if info.get("ok") else "🔴 उपलब्ध नहीं",
            "Latency (s)": (f"{float(info.get('seconds')):.2f}s"
                             if info.get("seconds") is not None else NA),
            "Detail": info.get("detail") or NA,
            "Checked": info.get("at") or NA,
        })
    return rows


# ---- Yahoo chart API fallback (yfinance न चले तो, बिना crumb के) ------------
def _yf_chart_download(symbols: list[str], rng: str = "1y", interval: str = "1d",
                       max_symbols: int = 40) -> dict:
    """query1.finance.yahoo.com chart API — yfinance fail होने पर OHLCV fallback।"""
    out: dict[str, pd.DataFrame] = {}
    try:
        import httpx
    except Exception:
        return out
    for sym in list(symbols)[:max_symbols]:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}.NS"
               f"?range={rng}&interval={interval}")
        try:
            resp = httpx.get(url, headers={"User-Agent": UA}, timeout=12.0,
                             follow_redirects=True)
            if resp.status_code != 200:
                continue
            result = (resp.json().get("chart") or {}).get("result") or []
            if not result:
                continue
            data = result[0]
            ts = data.get("timestamp") or []
            quote = ((data.get("indicators") or {}).get("quote") or [{}])[0]
            if not ts or not quote:
                continue
            idx = pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Kolkata")
            frame = pd.DataFrame({
                "open": quote.get("open"), "high": quote.get("high"),
                "low": quote.get("low"), "close": quote.get("close"),
                "volume": quote.get("volume") or [0] * len(ts),
            }, index=idx)
            frame = frame.dropna(subset=["close"])
            if len(frame):
                out[sym] = frame
        except Exception:
            continue
    return out


# ---- FREE RSS NEWS (कोई API key नहीं, Cloud पर भी चलता है) ------------------
NEWS_FEEDS: list[tuple[str, str]] = [
    ("Moneycontrol", "https://www.moneycontrol.com/news/india/marketnews/rssfeed.xml"),
    ("ET Markets", "https://economictimes.indiatimes.com/rssfeedstopstories.cms"),
    ("Business Standard", "https://www.business-standard.com/finance/markets/rss"),
    ("Mint Markets", "https://www.livemint.com/rss/markets"),
    ("CNBC-TV18", "https://www.cnbctv18.com/rss/market"),
]


@st.cache_data(ttl=900, show_spinner=False)
def fetch_rss_news(limit: int = 15) -> list[dict]:
    """मुक्त RSS स्रोतों से बाज़ार समाचार (stdlib XML — कोई dependency नहीं)।"""
    import xml.etree.ElementTree as ET

    items: list[dict] = []
    ok_feeds = 0
    for name, url in NEWS_FEEDS:
        try:
            import httpx

            resp = httpx.get(url, headers={"User-Agent": UA}, timeout=8.0,
                             follow_redirects=True)
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.content)
            count = 0
            for item in root.iter("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                published = (item.findtext("pubDate") or "").strip()
                if not title:
                    continue
                items.append({"source": name, "title": title, "link": link,
                              "published": published})
                count += 1
                if count >= 6:
                    break
            if count:
                ok_feeds += 1
        except Exception:
            continue
    _record_source("RSS News (मुक्त स्रोत)", ok_feeds > 0,
                   f"{ok_feeds}/{len(NEWS_FEEDS)} फ़ीड उपलब्ध, {len(items)} headline")
    return items[:limit]


# ---- GitHub raw catalog fallback (आपके repo का data फ़ाइल) -------------------
GITHUB_RAW_CATALOG = ("https://raw.githubusercontent.com/balramchauhan2010-br/zone/"
                       "main/data/global_instruments.json")


def _catalog_from_github() -> list[dict]:
    """host repo की local catalog न हो तो GitHub raw से (free, कोई key नहीं)।"""
    try:
        import httpx

        resp = httpx.get(GITHUB_RAW_CATALOG, headers={"User-Agent": UA},
                         timeout=10.0, follow_redirects=True)
        if resp.status_code != 200:
            _record_source("GitHub raw catalog", False, f"HTTP {resp.status_code}")
            return []
        payload = resp.json()
        items = payload if isinstance(payload, list) else (payload.get("instruments") or [])
        _record_source("GitHub raw catalog", True,
                       f"{len(items)} instruments (raw.githubusercontent.com)")
        return [i for i in items if isinstance(i, dict)]
    except Exception as exc:
        _record_source("GitHub raw catalog", False, f"{type(exc).__name__}")
        return []

# ---- NSE F&O bhavcopy (NSE official EOD: futures close/OI, spot, option chain) --
_NSE_FO_BHAV = ("https://nsearchives.nseindia.com/content/fo/"
                "BhavCopy_NSE_FO_0_0_0_{ds}_F_0000.csv.zip")


@st.cache_data(ttl=21600, show_spinner=False)
def _nse_fo_bhavcopy() -> dict:
    """NSE की official F&O bhavcopy — front-month futures close, OI, OI change,
    underlying (spot) price और option-chain OI (PCR) — सब कुछ वास्तविक।

    TradingView जिस symbol पर futures नहीं देता (लगभग 40%), वहाँ यह काम आता है।
    """
    empty = {"date": "", "fut": {}, "spot": {}, "opt": {}}
    try:
        import httpx
    except Exception:
        return empty
    today = datetime.now().date()
    payload = None
    for back in range(0, 6):
        ds = (today - timedelta(days=back)).strftime("%Y%m%d")
        url = _NSE_FO_BHAV.format(ds=ds)
        try:
            resp = httpx.get(url, headers={"User-Agent": UA}, timeout=30.0,
                             follow_redirects=True)
            if resp.status_code == 200 and len(resp.content) > 20000:
                payload = (ds, resp.content)
                break
        except Exception:
            continue
    if not payload:
        _record_source("NSE F&O bhavcopy", False, "कोई फ़ाइल नहीं मिली")
        return empty
    ds, content = payload
    out = {"date": f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}", "fut": {}, "spot": {}, "opt": {}}
    try:
        import csv
        import io
        import zipfile

        zf = zipfile.ZipFile(io.BytesIO(content))
        name = zf.namelist()[0]
        text = zf.read(name).decode("utf-8", "replace")
        rows = list(csv.DictReader(io.StringIO(text)))
    except Exception as exc:
        _record_source("NSE F&O bhavcopy", False, f"parse: {type(exc).__name__}")
        return out

    groups: dict[tuple, list] = {}
    for r in rows:
        sym = str(r.get("TckrSymb") or "").strip().upper()
        if not sym:
            continue
        itype = str(r.get("FinInstrmTp") or "").strip().upper()
        optn = str(r.get("OptnTp") or "XX").strip().upper()
        if itype in ("STF", "IDF"):
            kind = "FUT"                      # stock / index futures
        elif itype in ("STO", "IDO"):
            kind = "FUT" if optn == "XX" else "OPT"   # equity / index options
        else:
            continue
        exp = str(r.get("XpryDt") or "").strip()
        groups.setdefault((sym, kind, exp), []).append(r)

    today_iso = today.isoformat()
    front: dict[tuple, str] = {}
    for (sym, kind, exp) in groups:
        key = (sym, kind)
        cur = front.get(key)
        if cur is None:
            front[key] = exp
        else:
            live = [e for e in (cur, exp) if e and e >= today_iso]
            front[key] = min(live) if live else max(cur, exp)

    for (sym, kind, exp), rlist in groups.items():
        if front.get((sym, kind)) != exp:
            continue
        if kind == "FUT":
            r = rlist[0]
            close = _f(r.get("ClsPric"))
            prev = _f(r.get("PrvsClsgPric"))
            oi = _f(r.get("OpnIntrst"))
            oi_chg = _f(r.get("ChngInOpnIntrst"))
            vol = _f(r.get("TtlTradgVol"))
            out["fut"][sym] = {
                "close": close, "prev_close": prev, "oi": oi, "oi_chg": oi_chg,
                "volume": vol, "expiry": exp,
                "high": _f(r.get("HghPric")), "low": _f(r.get("LwPric")),
            }
            spot = _f(r.get("UndrlygPric"))
            if spot:
                out["spot"][sym] = spot
        else:
            ce_oi = pe_oi = 0.0
            ce_vol = pe_vol = 0.0
            for r in rlist:
                oi = _f(r.get("OpnIntrst")) or 0.0
                vol = _f(r.get("TtlTradgVol")) or 0.0
                ot = str(r.get("OptnTp") or "").strip().upper()
                if ot == "CE":
                    ce_oi += oi
                    ce_vol += vol
                elif ot == "PE":
                    pe_oi += oi
                    pe_vol += vol
            out["opt"][sym] = {"ce_oi": ce_oi, "pe_oi": pe_oi, "ce_vol": ce_vol,
                               "pe_vol": pe_vol, "expiry": exp}
    _record_source("NSE F&O bhavcopy", bool(out["fut"]),
                   f"{len(out['fut'])} futures · {len(out['opt'])} option chains · {out['date']}")
    return out


def _bhavcopy_futures(symbols: list[str]) -> dict[str, dict]:
    """bhavcopy → {SYMBOL: {...}} (सिर्फ़ माँगे गए symbol)।"""
    try:
        data = _nse_fo_bhavcopy()
    except Exception:
        return {}
    fut = data.get("fut") or {}
    spot = data.get("spot") or {}
    opt = data.get("opt") or {}
    out: dict[str, dict] = {}
    for sym in symbols:
        key = str(sym).strip().upper()
        row = fut.get(key)
        if not row:
            continue
        item = dict(row)
        item["spot"] = spot.get(key)
        item["opt"] = opt.get(key) or {}
        item["date"] = data.get("date")
        out[key] = item
    return out


# ==============================================================================
# 6) UNIFIED DATA SERVICE
# ==============================================================================

@dataclass
class SymbolSnapshot:
    symbol: str
    spot: float | None = None
    spot_change_pct: float | None = None
    volume: int | None = None
    futures_ltp: float | None = None
    futures_change_pct: float | None = None
    futures_oi: float | None = None
    futures_oi_change_pct: float | None = None
    expiry: str | None = None
    pcr: float | None = None
    ce_oi: int | None = None
    pe_oi: int | None = None
    ce_oi_atm5: int | None = None
    pe_oi_atm5: int | None = None
    atm: float | None = None
    delivery_pct: float | None = None
    delivery_session: str | None = None
    announcements: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    tv_delay: str | None = None
    data_quality: int | None = None
    lot_size: float | None = None
    security_id: str | None = None
    spot_security_id: str | None = None
    fno_ban_status: str = "UNAVAILABLE"
    eligibility_status: str = "UNKNOWN"

    def source_label(self) -> str:
        return " + ".join(dict.fromkeys(self.sources)) if self.sources else \
            "OHLCV feed (F&O feed unavailable)"


class _UnifiedService:
    """सारा डेटा यहीं से — host repo के services पहले, फिर public fallbacks।"""

    def __init__(self, cache_ttl: float = 300.0) -> None:
        self._cache = _TTLCache(cache_ttl)
        self._cache_long = _TTLCache(6 * 3600)   # universe/catalog — 6 घंटे
        self._universe: list[str] = []
        self._universe_src = "universe unavailable"
        self._contracts: list[dict] = []
        self._provider = None

    # ------------------------------------------------------------- provider
    def _dhan_provider(self):
        if self._provider is not None:
            return self._provider
        if _HAS_USER_DHAN:
            try:
                self._provider = _user_provider_dhan.DhanProvider()
                return self._provider
            except Exception:
                self._provider = None
        return None

    def provider_status(self) -> dict:
        provider = self._dhan_provider()
        if provider is not None:
            try:
                info = provider.status()
                _record_source("DhanHQ API", bool(info.get("credentials_configured")),
                               str(info.get("status") or "")[:120])
                return info
            except Exception:
                pass
        _record_source("DhanHQ API", False, "credentials missing (public master only)")
        return {"status": "NOT_CONFIGURED", "note": "Dhan credentials missing"}

    # -------------------------------------------------------------- universe
    def _load_universe(self) -> None:
        cached = self._cache_long.get("universe")
        if cached:
            self._universe, self._universe_src, self._contracts = cached
            return
        symbols: list[str] = []
        source = "universe unavailable"
        contracts: list[dict] = []

        provider = self._dhan_provider()
        if provider is not None and _HAS_USER_INSTRUMENTS:
            try:
                master, _ts = _user_svc_instruments.get_instrument_master(provider)
                contracts = _user_svc_instruments.build_nse_futures_universe(
                    master, contract="front", exclude_test_instruments=True)
                symbols = [c["symbol"] for c in contracts if c.get("symbol")]
                source = "DhanHQ instrument master (front-month NSE futures)"
            except Exception:
                symbols, contracts = [], []
        if not symbols:
            symbols, source = self._universe_from_public_master()

        self._universe, self._universe_src, self._contracts = symbols, source, contracts
        self._cache_long.set("universe", (symbols, source, contracts))
        _record_source("NSE F&O universe (Dhan master)", bool(symbols),
                       f"{len(symbols)} symbols · {source}")

    def _universe_from_public_master(self) -> tuple[list[str], str]:
        """Dhan का public scrip-master CSV (बिना token) — वास्तविक NSE F&O universe।"""
        cached = self._cache_long.get("universe_public")
        if cached:
            return cached
        symbols: list[str] = []
        source = "DhanHQ public scrip master"
        try:
            import io

            import httpx

            url = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                resp = client.get(url, headers={"User-Agent": UA})
            if resp.status_code == 200:
                frame = pd.read_csv(io.BytesIO(resp.content), dtype=str, low_memory=False)
                frame.columns = [str(c).strip().upper() for c in frame.columns]
                need = {"EXCH_ID", "SEGMENT", "INSTRUMENT", "SM_EXPIRY_DATE"}
                if need.issubset(frame.columns):
                    sel = frame[
                        frame["EXCH_ID"].astype(str).str.upper().eq("NSE")
                        & frame["SEGMENT"].astype(str).str.upper().eq("D")
                        & frame["INSTRUMENT"].astype(str).str.upper().eq("FUTSTK")
                    ].copy()
                    sel["_sym"] = sel.apply(
                        lambda r: (str(r.get("UNDERLYING_SYMBOL", "")).strip().upper()
                                   or str(r.get("SYMBOL_NAME", "")).split("-")[0].strip().upper()),
                        axis=1)
                    sel["_exp"] = pd.to_datetime(sel["SM_EXPIRY_DATE"], errors="coerce").dt.date
                    today = datetime.now(IST).date()
                    sel = sel[sel["_exp"].notna() & (sel["_exp"] >= today)]
                    sel = sel[~sel["_sym"].str.contains("TEST", case=False, na=False)]
                    sel = sel.sort_values(["_sym", "_exp"], kind="stable")
                    sel = sel.groupby("_sym", sort=True, group_keys=False).head(1)
                    symbols = sorted({s for s in sel["_sym"] if s})
        except Exception as exc:
            symbols = []
            _record_source("Dhan scrip master (public CSV)", False, f"{type(exc).__name__}")
        self._cache_long.set("universe_public", (symbols, source))
        return symbols, source

    def get_universe(self) -> tuple[list[str], str]:
        self._load_universe()
        return list(self._universe), self._universe_src

    def get_contracts(self) -> list[dict]:
        self._load_universe()
        return list(self._contracts)

    # ---------------------------------------------------------------- bundle
    def get_bundle(self, symbols, tfs) -> dict:
        symbols = list(symbols or [])
        tfs = list(tfs or [])
        if not symbols or not tfs:
            return {}
        key = ("bundle", tuple(symbols), tuple(tfs))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        bundle: dict[str, dict] = {}
        provider = self._dhan_provider()
        contracts = {c.get("symbol"): c for c in self.get_contracts()}
        use_dhan = False
        if provider is not None and contracts and _HAS_USER_CANDLES:
            try:
                # Dhan candles के लिए token चाहिए — बिना token के yfinance path
                use_dhan = bool(getattr(provider, "credentials_ready", False))
            except Exception:
                use_dhan = False
        if use_dhan and _HAS_USER_CANDLES:
            for tf in tfs:
                per: dict[str, pd.DataFrame] = {}
                for sym in symbols:
                    contract = contracts.get(sym)
                    if not contract:
                        continue
                    try:
                        candles, _meta = _user_svc_candles.fetch_candles(
                            provider, contract, tf, include_oi=False)
                    except Exception:
                        continue
                    if candles is None or candles.empty:
                        continue
                    norm = _normalise_ohlcv(candles)
                    if norm is None or norm.empty:
                        continue
                    per[sym] = _resample(norm, tf)
                bundle[tf] = per
            got = sum(len(v or {}) for v in bundle.values())
            if got:
                _record_source("DhanHQ candles", True, f"{got} candle frames (live API)")
            # Dhan से कम/none मिला → token invalid या API down → Yahoo से भरें
            missing = [s for s in symbols
                       if not any((bundle.get(tf) or {}).get(s) is not None for tf in tfs)]
            if missing:
                try:
                    yf_bundle = self._bundle_yahoo(missing, tfs)
                except Exception:
                    yf_bundle = {}
                for tf_item in tfs:
                    for sym, blk in (yf_bundle.get(tf_item) or {}).items():
                        bundle.setdefault(tf_item, {}).setdefault(sym, blk)
        else:
            bundle = self._bundle_yahoo(symbols, tfs)
        if not any((bundle.get(tf) or {}) for tf in tfs):
            _record_source("Candles (OHLCV)", False, "कोई candle नहीं मिली")
        self._cache.set(key, bundle)
        return bundle

    def _bundle_yahoo(self, symbols: list[str], tfs: list[str]) -> dict:
        """Yahoo OHLCV (yfinance → chart-API fallback) → base feed → resample।"""
        bundle: dict[str, dict] = {}
        # base feed एक-एक बार download → सभी TF resample (तेज़)
        wanted: dict[str, set] = {}
        for tf_item in tfs:
            wanted.setdefault(_BASE_FEED.get(tf_item, "1d"), set()).add(tf_item)
        raw: dict[str, dict] = {}
        for base in wanted:
            data = _yf_download([f"{s}.NS" for s in symbols],
                                _YF_PERIOD.get(base, "1y"),
                                _YF_INTERVAL.get(base, "1d"))
            frames: dict[str, pd.DataFrame] = {}
            for sym in symbols:
                blk = _normalise_ohlcv(_extract_block(data, f"{sym}.NS"))
                if blk is not None and not blk.empty:
                    frames[sym] = blk
            if not frames:
                # yfinance fail → per-symbol chart API (सीमित, धीमा लेकिन भरोसेमंद)
                fallback = _yf_chart_download(
                    [s for s in symbols][:25],
                    rng=({"15m": "1mo", "60m": "6mo", "1d": "2y"}.get(base, "1y")),
                    interval=({"15m": "15m", "60m": "60m", "1d": "1d"}.get(base, "1d")),
                    max_symbols=25)
                for sym, blk in fallback.items():
                    frames[sym] = blk
                if fallback:
                    _record_source(f"Yahoo chart API ({base})", True,
                                   f"{len(fallback)} symbols (yfinance fail हुआ)")
            raw[base] = frames
        for tf_item in tfs:
            base = _BASE_FEED.get(tf_item, "1d")
            per_tf: dict[str, pd.DataFrame] = {}
            for sym, blk in (raw.get(base) or {}).items():
                out = _resample(blk, tf_item)
                if out is not None and not out.empty:
                    per_tf[sym] = out
            bundle[tf_item] = per_tf
        return bundle

    @staticmethod
    def frame(bundle: dict, tf: str, symbol: str):
        try:
            return (bundle.get(tf) or {}).get(symbol)
        except Exception:
            return None

    # -------------------------------------------------------------- snapshots
    def get_snapshots(self, symbols, include_announcements: bool = True) -> dict:
        symbols = list(symbols or [])
        if not symbols:
            return {}
        key = ("snapshots", tuple(symbols), bool(include_announcements))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        snaps = self._snapshots_dhan(symbols) or self._snapshots_public(symbols)
        self._cache.set(key, snaps)
        return snaps

    def _snapshots_dhan(self, symbols: list[str]) -> dict:
        provider = self._dhan_provider()
        contracts = {c.get("symbol"): c for c in self.get_contracts()}
        if provider is None or not contracts or not _HAS_USER_MARKET:
            return {}
        try:
            if not getattr(provider, "credentials_ready", False):
                return {}
        except Exception:
            return {}
        chosen = [contracts[s] for s in symbols if s in contracts]
        if not chosen:
            return {}
        try:
            payload = _user_svc_market.get_futures_snapshot(provider, chosen)
        except Exception:
            return {}
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not rows:
            return {}
        out: dict[str, SymbolSnapshot] = {}
        for row in rows:
            symbol = str(row.get("symbol") or "").upper()
            if not symbol:
                continue
            snap = SymbolSnapshot(
                symbol=symbol,
                spot=_f(row.get("spot_ltp")),
                spot_change_pct=_f(row.get("spot_change_pct")),
                volume=int(_f(row.get("daily_spot_volume")) or 0) or None,
                futures_ltp=_f(row.get("futures_ltp")),
                futures_change_pct=_f(row.get("futures_change_pct")),
                futures_oi=_f(row.get("futures_oi")),
                futures_oi_change_pct=_f(row.get("futures_oi_change_pct")),
                expiry=row.get("expiry"),
                data_quality=_f(row.get("data_quality_score")),
                lot_size=_f(row.get("lot_size")),
                security_id=row.get("security_id"),
                spot_security_id=row.get("spot_security_id"),
                fno_ban_status=str(row.get("fno_ban_status") or "UNAVAILABLE"),
                eligibility_status=str(row.get("eligibility_status") or "UNKNOWN"),
                sources=[str(row.get("source") or "DhanHQ")],
            )
            out[symbol] = snap
        return out

    @staticmethod
    def _tv_fut_tickers(symbols: list[str]) -> list[str]:
        """TradingView futures ticker — 'NSE:SYM1!' (dash → underscore वाला rule)।"""
        out: list[str] = []
        for sym in symbols:
            base = str(sym).strip().upper()
            variants = [f"NSE:{base}1!", f"NSE:{base.replace('-', '_')}1!"]
            if "&" in base:
                variants.append(f"NSE:{base.replace('&', '')}1!")
            for v in variants:
                if v not in out:
                    out.append(v)
        return out

    def _snapshots_public(self, symbols: list[str]) -> dict:
        """TradingView futures/spot (delayed) + NSE official bhavcopy (EOD)।"""
        out: dict[str, SymbolSnapshot] = {}
        fut_tickers = self._tv_fut_tickers(symbols)
        fut_rows = _tv_scan(fut_tickers, TV_GLOBAL_SCAN, TV_FUT_COLUMNS)
        spot_rows = _tv_scan([f"NSE:{s}" for s in symbols], TV_INDIA_SCAN, TV_SPOT_COLUMNS)
        bh = _bhavcopy_futures(symbols)
        bh_date = ""
        try:
            bh_date = str((_nse_fo_bhavcopy() or {}).get("date") or "")
        except Exception:
            bh_date = ""
        delay = None
        for symbol in symbols:
            sym_key = str(symbol).strip().upper()
            fut = {}
            for cand in (f"NSE:{sym_key}1!", f"NSE:{sym_key.replace('-', '_')}1!",
                         f"NSE:{sym_key.replace('&', '')}1!"):
                if cand in fut_rows:
                    fut = fut_rows[cand]
                    break
            spot = spot_rows.get(f"NSE:{symbol}") or {}
            b = bh.get(sym_key) or {}
            snap = SymbolSnapshot(symbol=symbol)
            snap.futures_ltp = _f(fut.get("close"))
            snap.futures_change_pct = _f(fut.get("change"))
            snap.futures_oi = _f(fut.get("open_interest"))
            snap.spot = _f(spot.get("close")) or None
            snap.spot_change_pct = _f(spot.get("change"))
            vol = _f(spot.get("volume"))
            snap.volume = int(vol) if vol else None
            snap.expiry = "front month (NSE:<SYM>1!)"
            snap.tv_delay = str(fut.get("update_mode") or "delayed_streaming_900")
            delay = snap.tv_delay
            sources = []
            if snap.futures_ltp is not None:
                sources.append("TradingView screener (futures, ~15 min delayed)")
            if snap.spot is not None:
                sources.append("TradingView screener (spot)")
            # --- NSE official bhavcopy (TradingView जहाँ futures नहीं देता) -----
            if b:
                bh_label = f"NSE F&O bhavcopy ({bh_date or 'EOD'}, official)"
                if snap.futures_ltp is None and b.get("close"):
                    snap.futures_ltp = b.get("close")
                    prev = b.get("prev_close")
                    if prev:
                        snap.futures_change_pct = round(
                            (float(b["close"]) - float(prev)) / float(prev) * 100.0, 2)
                    snap.expiry = str(b.get("expiry") or "")
                    if b.get("oi") is not None:
                        snap.futures_oi = b.get("oi")
                        oi_chg = b.get("oi_chg")
                        base_oi = float(b.get("oi") or 0) - float(oi_chg or 0)
                        if oi_chg is not None and base_oi:
                            snap.futures_oi_change_pct = round(
                                float(oi_chg) / base_oi * 100.0, 2)
                    if b.get("volume"):
                        snap.volume = int(float(b["volume"]))
                    sources.append(bh_label)
                if snap.spot is None and b.get("spot"):
                    snap.spot = b.get("spot")
                    sources.append(bh_label)
                opt = b.get("opt") or {}
                ce_oi = opt.get("ce_oi") or 0
                pe_oi = opt.get("pe_oi") or 0
                if ce_oi or pe_oi:
                    snap.ce_oi = int(ce_oi)
                    snap.pe_oi = int(pe_oi)
                    if ce_oi:
                        snap.pcr = round(pe_oi / ce_oi, 3)
                    sources.append(f"Option chain (NSE bhavcopy, {opt.get('expiry') or ''})")
            snap.sources = sources
            # Delivery % / Option chain — NSE public (datacenter IP पर अक्सर blocked)
            delivery = _nse_get_json("/api/quote-equity", {"symbol": symbol}, timeout=6.0)
            if isinstance(delivery, dict):
                info = ((delivery.get("securityInfo") or {}).get("deliveryInfo")
                        or (delivery.get("securityWiseInfo") or {}).get(symbol) or {})
                value = _f(info.get("deliveryQuantity") or info.get("deliveryToTradedQuantity"))
                traded = _f(info.get("totalTradedQuantity"))
                if value is not None and traded:
                    snap.delivery_pct = round(value / traded * 100.0, 2)
                    snap.delivery_session = str(info.get("date") or "")
                    snap.sources.append("NSE delivery data")
            out[symbol] = snap
        return out

    # ------------------------------------------------------------------ macro
    def get_macro(self) -> dict | None:
        cached = self._cache.get("macro")
        if cached is not None:
            return cached
        items = [("DXY", "DX-Y.NYB"), ("US10Y", "^TNX"), ("CRUDE", "CL=F"),
                 ("GOLD", "GC=F"), ("NIFTY", "^NSEI")]
        data = _yf_download([t for _n, t in items], "5d", "1d")
        lines: list[str] = []
        bias = 0
        numbers: dict[str, float] = {}
        for name, ticker in items:
            blk = _normalise_ohlcv(_extract_block(data, ticker))
            if blk is None or len(blk) < 2:
                lines.append(f"{name}: {NA}")
                continue
            p0 = float(blk["close"].iloc[-1])
            p1 = float(blk["close"].iloc[-2])
            chg = ((p0 - p1) / p1 * 100.0) if p1 else 0.0
            numbers[name] = chg
            if name in ("DXY", "US10Y"):
                bias += 1 if chg > 0 else (-1 if chg < 0 else 0)
            lines.append(f"{name}: {p0:,.2f} ({chg:+.2f}%)")
        _record_source("Macro (DXY/US10Y/Crude/Gold/NIFTY)", bool(numbers),
                       f"{len(numbers)} series · Yahoo (delayed)")
        result = {"lines": lines, "bias": bias, "changes": numbers,
                  "source": "Yahoo Finance (delayed)"}
        self._cache.set("macro", result)
        return result

    def get_fii(self) -> dict | None:
        cached = self._cache.get("fii")
        if cached is not None:
            return cached
        payload = _nse_get_json("/api/fiidiiTradeReact", timeout=7.0)
        fii_net = dii_net = None
        date_text = None
        if isinstance(payload, list) and payload:
            row = payload[-1] if isinstance(payload[-1], dict) else {}
            fii_net = _f(str(row.get("FII_Net", "")).replace(",", ""))
            dii_net = _f(str(row.get("DII_Net", "")).replace(",", ""))
            date_text = str(row.get("Date") or row.get("date") or "") or None
        _record_source("NSE FII/DII", fii_net is not None,
                       f"तिथि {date_text}" if date_text else "इस IP पर NSE route बंद")
        result = {"fii_net": fii_net, "dii_net": dii_net, "date": date_text,
                  "source": "NSE FII/DII live feed"}
        self._cache.set("fii", result)
        return result

    def get_gift_nifty(self) -> dict | None:
        cached = self._cache.get("gift")
        if cached is not None:
            return cached
        rows = _tv_scan(["NSE:NIFTY1!"], TV_GLOBAL_SCAN, TV_FUT_COLUMNS)
        row = rows.get("NSE:NIFTY1!") or {}
        result = {"price": _f(row.get("close")), "change_pct": _f(row.get("change")),
                  "source": "TradingView (delayed)"}
        self._cache.set("gift", result)
        return result

    def get_market_status(self) -> str:
        cached = self._cache.get("mstatus")
        if cached is not None:
            return cached
        payload = _nse_get_json("/api/marketStatus", timeout=6.0)
        status = "Unavailable"
        parts: list[str] = []
        if isinstance(payload, dict):
            state = payload.get("marketState")
            if isinstance(state, list):
                for seg in state:
                    if isinstance(seg, dict):
                        label = str(seg.get("market") or seg.get("marketSegment") or "")
                        value = str(seg.get("marketStatus")
                                    or seg.get("marketStatusMessage") or "")
                        if label and value:
                            parts.append(f"{label}: {value}")
            elif isinstance(state, str) and state:
                parts.append(state)
            message = payload.get("marketStateMessage")
            if isinstance(message, str) and message:
                parts.append(message)
        elif isinstance(payload, list) and payload:
            for seg in payload:
                if isinstance(seg, dict):
                    label = str(seg.get("market") or seg.get("marketSegment") or "")
                    value = str(seg.get("marketStatus") or seg.get("marketStatusMessage") or "")
                    if label and value:
                        parts.append(f"{label}: {value}")
        if parts:
            status = " · ".join(parts[:4])
        _record_source("NSE Market Status", bool(parts), status)
        self._cache.set("mstatus", status)
        return status

    def get_announcements(self, symbol: str) -> list[dict]:
        payload = _nse_get_json("/api/corporate-announcements",
                                {"index": "equities", "symbol": symbol}, timeout=6.0)
        out: list[dict] = []
        if isinstance(payload, list):
            for row in payload[:5]:
                if not isinstance(row, dict):
                    continue
                out.append({
                    "desc": str(row.get("desc") or row.get("subject") or "").strip(),
                    "date": str(row.get("attchmntText") or row.get("an_dt") or "").strip(),
                })
        return out

    # ------------------------------------------------------------------ misc
    def clear_cache(self) -> None:
        self._cache.clear()

    def option_chain(self, symbol: str, expiry: str | None = None) -> dict:
        """वास्तविक option chain → PCR (host repo की service पहले)।"""
        provider = self._dhan_provider()
        contracts = {c.get("symbol"): c for c in self.get_contracts()}
        contract = contracts.get(symbol) or {}
        spot_id = contract.get("spot_security_id")
        if provider is not None and spot_id and _HAS_USER_OPTIONS:
            try:
                if not getattr(provider, "credentials_ready", False):
                    return {"status": "UNAVAILABLE", "reason": "Dhan credentials not configured"}
                expiries = provider.get_option_expiries(str(spot_id))
                if not expiries:
                    return {"status": "UNAVAILABLE", "reason": "No listed expiries returned"}
                use_expiry = expiry or expiries[0]
                raw = provider.get_option_chain(str(spot_id), use_expiry)
                return _user_svc_options.normalize_option_chain(raw, symbol, use_expiry)
            except Exception:
                return {"status": "UNAVAILABLE", "reason": "Option chain request failed"}
        return {"status": "UNAVAILABLE", "reason": "Option chain source not configured"}


SERVICE = _UnifiedService()


# ==============================================================================
# 7) GLOBAL CATALOG  (host repo का data/global_instruments.json पहले)
# ==============================================================================

_FALLBACK_GLOBAL: list[dict[str, str]] = [
    {"id": "DXY", "name": "US Dollar Index", "ticker": "DX-Y.NYB", "tv": "TVC:DXY"},
    {"id": "USDINR", "name": "USD / INR", "ticker": "USDINR=X", "tv": "FX_IDC:USDINR"},
    {"id": "TLT", "name": "iShares 20+ Yr Treasury ETF", "ticker": "TLT", "tv": "NASDAQ:TLT"},
    {"id": "US10Y", "name": "US 10-Year Treasury Yield", "ticker": "^TNX", "tv": "TVC:US10Y"},
    {"id": "XAUUSD", "name": "Gold / US Dollar", "ticker": "GC=F", "tv": "OANDA:XAUUSD"},
    {"id": "XAGUSD", "name": "Silver / US Dollar", "ticker": "SI=F", "tv": "OANDA:XAGUSD"},
    {"id": "SPOTCRUDE", "name": "WTI Crude Oil", "ticker": "CL=F", "tv": "TVC:USOIL"},
    {"id": "US30", "name": "Dow Jones Industrial Average", "ticker": "^DJI", "tv": "DJ:DJI"},
    {"id": "US500", "name": "S&P 500 Index", "ticker": "^GSPC", "tv": "SP:SPX"},
    {"id": "SSE_COMPOSITE", "name": "SSE Composite Index", "ticker": "000001.SS",
     "tv": "SSE:000001"},
    {"id": "CHINA_A50", "name": "FTSE China A50 Index", "ticker": "XIN9.FGI", "tv": "SGX:XIN9"},
    {"id": "JP225", "name": "Nikkei 225 Index", "ticker": "^N225", "tv": "INDEX:NKY"},
    {"id": "GIFT_NIFTY_FUT", "name": "GIFT NIFTY 50 Futures", "ticker": "^NSEI",
     "tv": "NSE:NIFTY1!"},
    {"id": "NSE_NIFTY_FUT", "name": "NSE Nifty 50 Futures", "ticker": "^NSEI", "tv": "NSE:NIFTY1!"},
    {"id": "FTSE100", "name": "FTSE 100 Index", "ticker": "^FTSE", "tv": "INDEX:FTSE"},
    {"id": "DAX", "name": "DAX Index", "ticker": "^GDAXI", "tv": "XETR:DAX"},
    {"id": "COPPER", "name": "Copper", "ticker": "HG=F", "tv": "COMEX:HG1!"},
    {"id": "ALUMINIUM", "name": "Aluminium", "ticker": "ALI=F", "tv": "MCX:ALUMINIUM1!"},
    {"id": "ZINC", "name": "Zinc", "ticker": "ZNC=F", "tv": "MCX:ZINC1!"},
    {"id": "NATGAS", "name": "Natural Gas", "ticker": "NG=F", "tv": "NYMEX:NG1!"},
    {"id": "GBPUSD", "name": "GBP / USD", "ticker": "GBPUSD=X", "tv": "FX:GBPUSD"},
    {"id": "EURUSD", "name": "EUR / USD", "ticker": "EURUSD=X", "tv": "FX:EURUSD"},
    {"id": "JPYUSD", "name": "JPY / USD", "ticker": "JPY=X", "tv": "FX:USDJPY"},
    {"id": "JPYINR", "name": "JPY / INR", "ticker": "JPYINR=X", "tv": "FX_IDC:JPYINR"},
    {"id": "GBPINR", "name": "GBP / INR", "ticker": "GBPINR=X", "tv": "FX_IDC:GBPINR"},
    {"id": "BTCUSD", "name": "Bitcoin / USD", "ticker": "BTC-USD", "tv": "BINANCE:BTCUSDT"},
]

# id → (yfinance ticker, tradingview symbol) — catalog के instrument ids से मैच
_ALIAS: dict[str, tuple[str, str]] = {item["id"]: (item["ticker"], item["tv"])
                                     for item in _FALLBACK_GLOBAL}


def _global_instruments() -> list[dict[str, str]]:
    """Host repo का global catalog पहले; provider_symbol न हो तो alias map।"""
    if _HAS_USER_CONFIG:
        try:
            getter = getattr(_user_config, "get_global_catalog", None)
            if callable(getter):
                catalog = getter()
                rows: list[dict[str, str]] = []
                for item in catalog or []:
                    if not isinstance(item, dict):
                        continue
                    symbol = str(item.get("id") or item.get("symbol") or "").strip()
                    if not symbol:
                        continue
                    provider_symbol = str(item.get("provider_symbol")
                                          or item.get("yahoo_ticker")
                                          or item.get("ticker") or "").strip()
                    alias = _ALIAS.get(symbol.upper(), ("", ""))
                    ticker = provider_symbol or alias[0]
                    tv = str(item.get("tv_symbol") or item.get("tradingview")
                             or alias[1] or symbol)
                    if not ticker:
                        # provider symbol कॉन्फ़िगर नहीं → डेटा लाना संभव नहीं (काल्पनिक नहीं)
                        continue
                    rows.append({
                        "id": symbol,
                        "name": str(item.get("display_name") or item.get("name")
                                    or symbol),
                        "ticker": ticker,
                        "tv": tv,
                        "market": str(item.get("asset_class") or "GLOBAL"),
                        "status": str(item.get("status") or ""),
                    })
                if rows:
                    return rows
        except Exception:
            pass
    # host catalog नहीं → आपके GitHub repo का raw JSON (free, कोई key नहीं)
    try:
        gh_items = _catalog_from_github()
    except Exception:
        gh_items = []
    if gh_items:
        rows = []
        for item in gh_items:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("id") or item.get("symbol") or "").strip()
            if not symbol:
                continue
            alias = _ALIAS.get(symbol.upper(), ("", ""))
            ticker = str(item.get("provider_symbol") or item.get("yahoo_ticker")
                          or item.get("ticker") or alias[0] or "").strip()
            tv = str(item.get("tv_symbol") or item.get("tradingview")
                     or alias[1] or symbol)
            if not ticker:
                continue
            rows.append({"id": symbol, "name": str(item.get("display_name")
                                                    or item.get("name") or symbol),
                         "ticker": ticker, "tv": tv,
                         "market": str(item.get("asset_class") or "GLOBAL"),
                         "status": str(item.get("status") or "")})
        if rows:
            return rows
    return [dict(item) for item in _FALLBACK_GLOBAL]


GLOBAL_INSTRUMENTS = _global_instruments()


# ==============================================================================
# 8) AI EXPLANATION  (host repo का GeminiExplainer पहले — explanation only)
# ==============================================================================

def gemini_explain(evidence: dict) -> str | None:
    """सिर्फ़ दिए गए तथ्यों की व्याख्या — कोई नया आँकड़ा/संभावना नहीं।"""
    if not os.getenv("GEMINI_API_KEY"):
        return None
    if _HAS_USER_GEMINI:
        try:
            explainer = _user_svc_gemini.GeminiExplainer()
            if explainer.configured:
                out = explainer.explain(evidence)
                if isinstance(out, dict):
                    return str(out.get("summary") or "") or None
        except Exception:
            return None
    try:
        import httpx

        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{url_quote(model, safe='')}:generateContent")
        prompt = (
            "आप एक explanation-only मार्केट रिसर्च असिस्टेंट हैं। केवल JSON में दिए गए "
            "तथ्यों की व्याख्या करें (हिंदी में)। कोई नया प्राइस, OI, समाचार, probability "
            "या confidence न जोड़ें। buy/sell recommendation न दें। अंत में: (1) प्राप्त प्रमाण, "
            "(2) असहमति/अनुपलब्ध डेटा, (3) मुख्य जोखिम — तीन छोटे पैराग्राफ़।\n\n"
            f"सत्यापित JSON:\n{json.dumps(evidence, default=str, ensure_ascii=False)[:11000]}"
        )
        resp = httpx.post(url, headers={"x-goog-api-key": os.getenv("GEMINI_API_KEY", ""),
                                        "Content-Type": "application/json"},
                          json={"contents": [{"parts": [{"text": prompt}]}],
                                "generationConfig": {"temperature": 0.2, "maxOutputTokens": 500}},
                          timeout=httpx.Timeout(35.0, connect=8.0))
        if resp.status_code >= 400:
            return None
        body = resp.json()
        parts = body["candidates"][0]["content"]["parts"]
        text = "\n".join(str(p.get("text", "")) for p in parts if p.get("text"))
        return text.strip() or None
    except Exception:
        return None


# ==============================================================================
# 9) ENGINE STATUS (Settings tab में दिखेगा)
# ==============================================================================

def engine_status() -> dict:
    return {
        "host_zone_core": _HAS_USER_ZONE,
        "host_zones_service": _HAS_USER_ZONES_SVC,
        "host_indicators": _HAS_USER_IND,
        "host_signals": _HAS_USER_SIGNALS,
        "host_risk": _HAS_USER_RISK,
        "host_options": _HAS_USER_OPTIONS,
        "host_candles": _HAS_USER_CANDLES,
        "host_instruments": _HAS_USER_INSTRUMENTS,
        "host_market": _HAS_USER_MARKET,
        "host_dhan": _HAS_USER_DHAN,
        "host_config": _HAS_USER_CONFIG,
        "host_timeframes": _HAS_USER_TIMEFRAMES,
        "host_gemini": _HAS_USER_GEMINI,
        "host_secrets": _HAS_HOST_SECRETS,
    }


def engine_status_line() -> str:
    st_ = engine_status()
    host = [k.replace("host_", "") for k, v in st_.items() if v]
    if host:
        return "host repo: " + ", ".join(host)
    return "host repo modules उपलब्ध नहीं — inline fallback चल रहा है"


ORDERED_COLUMNS_TAB1 = [
    "Symbol", "Spot LTP", "Futures LTP", "Dif. %", "Spot Change %", "Futures Change %",
    "Selected-Timeframe Volume", "Volume Spike Ratio", "Futures OI", "Futures OI Change %",
    "Latest Verified Delivery %", "PCR", "CE Total OI", "PE Total OI",
    "Buildup Classification", "EMA 35 Signal", "EMA 100 Signal", "VWAP Signal",
    "Zone Signal", "Live Signals Highlight", "Final AI Bias", "Data Source",
]


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
# 3) SESSION STATE / SERVICE
# ==============================================================================

if "analysis_settings" not in st.session_state:
    st.session_state.analysis_settings = dict(DEFAULT_ANALYSIS_SETTINGS)

SETTINGS = st.session_state.analysis_settings
SETTINGS.setdefault("custom_tfs", list(ALL_TIMEFRAMES))
SETTINGS.setdefault("vol_spike_threshold", 2.1)
SETTINGS.setdefault("enrich_limit", 60)
SETTINGS.setdefault("scan_limit", 0)
SETTINGS.setdefault("use_llm", True)
SETTINGS["enrich_limit"] = int(os.getenv("AAP_ENRICH_LIMIT", SETTINGS["enrich_limit"]) or 0)
SETTINGS["scan_limit"] = int(os.getenv("AAP_SCAN_LIMIT", SETTINGS["scan_limit"]) or 0)

ACTIVE_TFS: list[str] = [tf for tf in SETTINGS.get("custom_tfs", ALL_TIMEFRAMES)]
if not ACTIVE_TFS:
    ACTIVE_TFS = ["1h", "Daily"]



def _layer_status() -> str:
    """Engine status — host repo modules पहले, inline fallback बाद।"""
    stt = engine_status()
    host_on = [k.replace("host_", "") for k, v in stt.items() if v]
    parts = [f"host repo: {len(host_on)} module(s) ✅" if host_on
             else "host repo modules ⚠️ (inline fallback)"]
    parts.append("zone_core ✅ (Pine-parity)" if stt["host_zone_core"] else "zone_core ⚠️")
    parts.append("signals ✅" if stt["host_signals"] else "signals ⚠️")
    parts.append("Dhan ✅" if stt["host_dhan"] else "Dhan ⚠️")
    return " · ".join(parts)


# ==============================================================================
# 4) MASTER SCAN  (OHLCV + zones + indicators)
# ==============================================================================

@st.cache_data(ttl=300, show_spinner=False)
def _build_master_frame(symbols: tuple[str, ...], tf: str, settings_json: str) -> pd.DataFrame:
    """वास्तविक OHLCV स्कैन + technicals + ज़ोन।

    ⚠️ cache में सिर्फ़ plain (picklable) values जाते हैं — भारी objects
    (DataFrame / Zone) `_ARTIFACTS` में रहते हैं। इससे Streamlit Cloud पर
    `UnserializableReturnValueError` नहीं आता और pickling भी तेज़ रहती है।
    """
    settings = json.loads(settings_json)
    bundle = SERVICE.get_bundle(symbols, [tf])
    lots = {c.get("symbol"): c.get("lot_size") for c in SERVICE.get_contracts()}
    art_df: dict[str, Any] = {}
    art_zone: dict[str, Any] = {}
    rows = []
    for sym in symbols:
        df = SERVICE.frame(bundle, tf, sym)
        ind = compute_indicators(df, float(settings.get("vol_spike_threshold", 2.1)),
                                  tf=tf, settings=settings)
        row = {
            "Symbol": sym,
            "Spot LTP": ind.get("close"),
            "Spot Change %": round(ind.get("change_pct") or 0.0, 2),
            "Open Gap": ind.get("gap", "Normal"),
            "Selected-Timeframe Volume": int(ind.get("volume") or 0),
            "Volume Spike Ratio": ind.get("vol_ratio", 1.0),
            "EMA 35 Signal": ind.get("ema35_signal") or NA,
            "EMA 100 Signal": ind.get("ema100_signal") or NA,
            "VWAP Signal": ind.get("vwap_signal") or NA,
            "Zone Signal": NA,
            "Live Signals Highlight": "",
            "_ind_keys": sorted(ind.keys()), "_lot_size": lots.get(sym),
        }
        art_df[sym] = df
        if df is not None and len(df) >= 12:
            try:
                zones = scan_zones(df[["open", "high", "low", "close", "volume"]].copy(),
                                   params=zone_params(settings), tf=tf)
                active = latest_active_zones(zones)
                zone, dist = pick_nearest(active, float(df["close"].iloc[-1]))
                if zone is not None:
                    kind = "DEMAND" if zone.isDemand else "SUPPLY"
                    art_zone[sym] = zone
                    extra = ""
                    if getattr(zone, "score10", None) is not None:
                        extra = f" · score {zone.score10}/10"
                    if getattr(zone, "hq", False):
                        extra += " · HQ"
                    row["Zone Signal"] = f"{zone.state} {kind}{extra}"
                    price = float(df["close"].iloc[-1])
                    if dist is not None and price and (dist / price) * 100 < 0.8:
                        row["Live Signals Highlight"] = (
                            f"Zone Proximity ({zone.state} @ {tf})")
            except Exception:
                pass
        tags = []
        if ind.get("vol_spike"):
            tags.append(f"Vol Spike ({ind['vol_ratio']}x @ {tf})")
        if row["EMA 35 Signal"] != NA and row["EMA 35 Signal"] == row["EMA 100 Signal"]:
            tags.append("EMA Bull Cross" if row["EMA 35 Signal"] == "+" else "EMA Bear Cross")
        if row["Live Signals Highlight"]:
            tags.append(row["Live Signals Highlight"])
        row["Live Signals Highlight"] = " | ".join(tags)
        rows.append(row)
    _ARTIFACTS[f"master::{tf}::{abs(hash(tuple(symbols)))}"] = {"df": art_df, "zone": art_zone}
    return pd.DataFrame(rows)


def _enrich_frame(frame: pd.DataFrame, limit: int) -> pd.DataFrame:
    """Futures / OI / Options / Delivery — वास्तविक डेटा (NSE → TradingView fallback)।"""
    if frame.empty:
        return frame
    for col in ("Futures LTP", "Dif. %", "Futures Change %", "Futures OI",
                "Futures OI Change %", "Latest Verified Delivery %", "PCR",
                "CE Total OI", "PE Total OI", "Buildup Classification", "Data Source"):
        frame[col] = NA
    frame["_Enrich"] = {}

    if limit <= 0:
        return frame
    tmp = frame.copy()
    tmp["_rank"] = pd.to_numeric(tmp["Selected-Timeframe Volume"], errors="coerce").fillna(0)
    targets = tmp.sort_values("_rank", ascending=False)["Symbol"].tolist()[:limit]
    try:
        snaps = SERVICE.get_snapshots(targets)
    except Exception:
        snaps = {}

    for idx, row in frame.iterrows():
        sym = row["Symbol"]
        snap = snaps.get(sym) or SymbolSnapshot(symbol=sym)
        frame.at[idx, "_Enrich"] = snap
        spot = row.get("Spot LTP")
        if spot is None and snap.spot:
            spot = snap.spot
            frame.at[idx, "Spot LTP"] = round(float(spot), 2)
        if snap.futures_ltp:
            frame.at[idx, "Futures LTP"] = f"₹{snap.futures_ltp:,.2f}"
            if spot:
                frame.at[idx, "Dif. %"] = f"{((snap.futures_ltp - float(spot)) / float(spot)) * 100:+.2f}%"
        if snap.futures_change_pct is not None:
            frame.at[idx, "Futures Change %"] = f"{snap.futures_change_pct:+.2f}%"
        if snap.futures_oi is not None:
            frame.at[idx, "Futures OI"] = f"{int(snap.futures_oi):,}"
        if snap.futures_oi_change_pct is not None:
            frame.at[idx, "Futures OI Change %"] = f"{snap.futures_oi_change_pct:+.2f}%"
        if snap.delivery_pct is not None:
            frame.at[idx, "Latest Verified Delivery %"] = f"{snap.delivery_pct:.1f}%"
        if snap.pcr is not None:
            frame.at[idx, "PCR"] = f"{snap.pcr:.2f}"
        if snap.ce_oi is not None:
            frame.at[idx, "CE Total OI"] = f"{int(snap.ce_oi):,}"
        if snap.pe_oi is not None:
            frame.at[idx, "PE Total OI"] = f"{int(snap.pe_oi):,}"

        frame.at[idx, "Buildup Classification"] = _buildup_label(
            snap.futures_change_pct, snap.futures_oi_change_pct)

        frame.at[idx, "Data Source"] = snap.source_label()
    return frame


# ==============================================================================
# 5) HYPOTHESIS (agents layer या degraded fallback)
# ==============================================================================

def _build_features(ind: dict, zone, snap, risk: dict, tf: str,
                    macro: dict | None = None) -> dict:
    """signals.score_setup के लिए evidence (+1/-1/0, None = उपलब्ध नहीं)।"""
    features: dict[str, Any] = {}
    close = _f(ind.get("close"))
    ema_fast = _f(ind.get("ema35"))
    ema_slow = _f(ind.get("ema100"))
    vwap = _f(ind.get("vwap"))
    structure = str(ind.get("structure") or "")

    # 1) intraday structure
    if "ABOVE" in structure:
        features["intraday_structure"] = 1
    elif "BELOW" in structure:
        features["intraday_structure"] = -1
    elif close is not None:
        features["intraday_structure"] = 0

    # 2) EMA alignment
    if ema_fast is not None and ema_slow is not None:
        features["ema_alignment"] = 1 if ema_fast > ema_slow else (-1 if ema_fast < ema_slow else 0)
    # 3) VWAP alignment
    if close is not None and vwap is not None:
        features["vwap_alignment"] = 1 if close > vwap else (-1 if close < vwap else 0)
    # 4) volume expansion
    ratio = _f(ind.get("vol_ratio")) or 1.0
    features["volume_expansion"] = 1 if ratio >= 2.1 else (-1 if ratio < 0.7 else 0)
    # 5) futures/OI behaviour
    fchg = _f(getattr(snap, "futures_change_pct", None))
    oi_chg = _f(getattr(snap, "futures_oi_change_pct", None))
    if fchg is not None and oi_chg is not None:
        if fchg > 0 and oi_chg > 0:
            features["futures_oi_behavior"] = 1
        elif fchg < 0 and oi_chg > 0:
            features["futures_oi_behavior"] = -1
        elif fchg > 0 and oi_chg < 0:
            features["futures_oi_behavior"] = -1
        elif fchg < 0 and oi_chg < 0:
            features["futures_oi_behavior"] = 1
        else:
            features["futures_oi_behavior"] = 0
    # 6) zone quality + entry confirmation + higher timeframe structure
    if zone is not None:
        score10 = _f(getattr(zone, "score10", None))
        hq = bool(getattr(zone, "hq", False))
        if hq or (score10 is not None and score10 >= 7):
            features["zone_quality"] = 1
        elif score10 is not None and score10 < 4:
            features["zone_quality"] = -1
        else:
            features["zone_quality"] = 0
        state = str(getattr(zone, "state", "")).lower()
        is_demand = bool(getattr(zone, "isDemand", True))
        prox = _f(getattr(zone, "proxVal", None))
        if close is not None and prox:
            near = abs(close - prox) / close * 100.0 <= 1.5
            if near and state in ("fresh", "tested"):
                features["entry_confirmation"] = 1 if is_demand else -1
            else:
                features["entry_confirmation"] = 0
        else:
            features["entry_confirmation"] = 0
        zone_tf = str(getattr(zone, "tf", tf) or tf)
        htf = zone_tf in ("Daily", "Weekly", "Monthly")
        if htf and close is not None and prox is not None:
            if is_demand:
                features["higher_timeframe_structure"] = 1 if close > prox else -1
            else:
                features["higher_timeframe_structure"] = 1 if close < prox else -1
        else:
            features["higher_timeframe_structure"] = 0
    # 7) market context (NIFTY + मैक्रो — वास्तविक बदलाव से)
    changes = (macro or {}).get("changes") or {}
    nifty_chg = _f(changes.get("NIFTY"))
    if nifty_chg is not None:
        features["nifty_context"] = 1 if nifty_chg > 0.3 else (-1 if nifty_chg < -0.3 else 0)
    dxy_chg = _f(changes.get("DXY"))
    if dxy_chg is not None:
        features["global_macro"] = -1 if dxy_chg > 0.2 else (1 if dxy_chg < -0.2 else 0)
    # 8) risk-reward
    rr = _f((risk or {}).get("rr"))
    features["risk_reward"] = 1 if (rr is not None and rr >= 1.5) else 0
    return features


def _fallback_hypothesis(symbol: str, df, zone, snap, macro, fii, settings, tf):
    """नियम-आधारित, पारदर्शी hypothesis (host signals engine या inline weights)।"""
    ind = compute_indicators(df, float(settings.get("vol_spike_threshold", 2.1)),
                              tf=tf, settings=settings)
    close = _f(ind.get("close"))
    risk_obj = build_risk_setup(close or 0.0, zone, _f(ind.get("atr")), settings,
                                True, getattr(snap, "lot_size", None))
    risk = risk_obj.to_dict()
    features = _build_features(ind, zone, snap, risk, tf, macro)
    scored = score_setup(features, _f(risk.get("rr")))
    bias = str(scored.get("bias") or "NO TRADE")
    bull = [str(x) for x in (scored.get("why_buy") or [])]
    bear = [str(x) for x in (scored.get("why_sell") or [])]
    missing = [str(x) for x in (scored.get("missing_features") or [])]
    gate = [str(x) for x in (scored.get("risk_gate") or [])]

    zone_txt = "कोई सक्रिय ज़ोन उपलब्ध नहीं"
    if zone is not None:
        zone_txt = (f"{getattr(zone, 'state', NA)} · "
                    f"{'DEMAND (माँग)' if getattr(zone, 'isDemand', True) else 'SUPPLY (आपूर्ति)'} · "
                    f"{getattr(zone, 'pattern', None) or 'pattern NA'} / "
                    f"{getattr(zone, 'category', None) or 'category NA'} · "
                    f"score {getattr(zone, 'score10', None) or NA}/10 · "
                    f"proximal {getattr(zone, 'proxVal', NA)} · distal {getattr(zone, 'distVal', NA)}")
    narrative = (f"**{symbol}** — **{bias}** (टाइमफ्रेम {tf})\n\n"
                 f"• ज़ोन: {zone_txt}\n"
                 f"• EMA 35/100: {ind.get('ema35') or NA} / {ind.get('ema100') or NA} · "
                 f"VWAP: {ind.get('vwap') or NA} · वॉल्यूम स्पाइक: {ind.get('vol_ratio')}x\n"
                 f"• प्रमाण: bullish {scored.get('bullish_score')} / bearish "
                 f"{scored.get('bearish_score')} (बुलिश अनुपात "
                 f"{scored.get('bullish_ratio')}% / नेट {scored.get('net_ratio')}%, "
                 f"कुल गुणवत्ता {scored.get('setup_quality_score')}, डेटा पूर्णता "
                 f"{scored.get('data_quality_score')}%, प्रमाण कवरेज "
                 f"{scored.get('evidence_coverage_pct')}%)\n"
                 f"• सेटअप: एंट्री ₹{risk.get('entry')} · SL ₹{risk.get('sl')} · "
                 f"लक्ष्य ₹{risk.get('target')} (1:{risk.get('rr')}) · साइज़ {risk.get('qty')} "
                 f"({'लॉट ' + str(risk.get('lots')) if risk.get('lots') is not None else 'शेयर'})\n"
                 + (f"• जोखिम गेट: {'; '.join(gate)}\n" if gate else "")
                 + "\n⚠️ यह केवल नियम-आधारित विश्लेषण है — लाभ की गारंटी नहीं; "
                   "कोई भी निष्कर्ष सत्यापित डेटा और आपके जोखिम नियम पर ही आधारित है।")
    evidence = [f"{k} = {v}" for k, v in features.items()]

    class _H:
        pass

    h = _H()
    h.symbol, h.bias, h.score = symbol, bias, scored.get("setup_quality_score")
    h.note, h.timeframe = "", tf
    h.bull, h.bear = bull, bear
    h.missing = missing + (["Risk gate: " + g for g in gate] if gate else [])
    h.risk = risk
    h.narrative = narrative
    h.evidence = evidence
    h.scored = scored
    return h


def _run_hypothesis(symbol: str, df, zone, snap, macro, fii, settings, tf):
    return _fallback_hypothesis(symbol, df, zone, snap, macro, fii, settings, tf)


def _llm_text(hyp, ctx_extra: dict | None = None) -> str | None:
    """Gemini explanation — केवल दिए गए सत्यापित तथ्यों के आधार पर (नई संख्या नहीं)।"""
    if not os.getenv("GEMINI_API_KEY"):
        return None
    evidence = {
        "symbol": getattr(hyp, "symbol", None),
        "bias": getattr(hyp, "bias", None),
        "timeframe": getattr(hyp, "timeframe", None),
        "narrative": getattr(hyp, "narrative", None),
        "bullish_evidence": list(getattr(hyp, "bull", []) or []),
        "bearish_evidence": list(getattr(hyp, "bear", []) or []),
        "missing": list(getattr(hyp, "missing", []) or []),
        "risk": getattr(hyp, "risk", None),
        "verified_evidence": list(getattr(hyp, "evidence", []) or []),
        "context": ctx_extra or {},
    }
    return gemini_explain(evidence)


# ==============================================================================
# 6) AI HYPOTHESIS MODAL
# ==============================================================================

def _modal_body(symbol: str, bias: str, ctx: dict, hyp, risk: dict, tf: str) -> None:
    """10 खंडों वाला पूरा हिंदी विश्लेषण — केवल सत्यापित डेटा, कोई अनुमान नहीं।"""
    ctx = ctx or {}
    risk = risk or {}

    def _section(title: str, body: Any) -> None:
        st.markdown(f"**{title}**")
        if body is None or (isinstance(body, str) and body.strip() in ("", NA)):
            st.caption(NA)
        elif isinstance(body, str):
            st.markdown(body)
        else:
            st.write(body)

    head_col, bias_col = st.columns([3, 2])
    with head_col:
        st.markdown(f"### {symbol} — {bias}")
        st.caption(f"टाइमफ्रेम: {tf} · अंतिम निर्णय: {bias} · "
                   f"इंजन: {getattr(hyp, 'scored', {}).get('source', 'rule-based')}")
    with bias_col:
        options = ["STRONG BUY", "BUY", "STRONG SELL", "SELL", "NO TRADE"]
        current = str(bias).upper()
        for opt in options:
            active = opt == current
            color = ("#00e676" if "BUY" in opt else "#ff5252") if "NO TRADE" not in opt else "#8ea5c0"
            border = f"2px solid {color}" if active else "1px solid #1b3a5c"
            bg = "rgba(57,167,255,0.12)" if active else "#0b1a2d"
            st.markdown(
                f"<div style='background:{bg}; border:{border}; border-radius:6px; "
                f"padding:4px 8px; margin-bottom:4px; font-size:0.8rem; color:{color}; "
                f"font-weight:{'bold' if active else '500'}'>"
                f"{'▶ ' if active else ''}{opt}</div>", unsafe_allow_html=True)

    scored = getattr(hyp, "scored", {}) or {}
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("बुलिश स्कोर", scored.get("bullish_score", NA))
    m2.metric("बेयरिश स्कोर", scored.get("bearish_score", NA))
    m3.metric("सेटअप गुणवत्ता", scored.get("setup_quality_score", NA))
    m4.metric("डेटा पूर्णता %", scored.get("data_quality_score", NA))
    m5.metric("प्रमाण कवरेज %", scored.get("evidence_coverage_pct", NA))

    st.markdown("---")
    _section("1️⃣ संक्षेप (Executive Summary)", getattr(hyp, "narrative", NA))
    _section("2️⃣ मूल्य संरचना (Price Action & Market Structure)",
             ctx.get("price_action") or NA)
    _section("3️⃣ टेक्निकल संकेत (EMA 35 / EMA 100 / VWAP / Volume Spike)",
             f"{ctx.get('price_action') or NA}\n\n{ctx.get('volume_text') or NA}\n\n"
             f"वॉल्यूम स्पाइक अनुपात: {ctx.get('vol_spike_txt') or NA}")
    _section("4️⃣ ज़ोन विश्लेषण (Zone Engine)", ctx.get("zone_text") or NA)
    _section("5️⃣ डेरिवेटिव्स (Futures / OI / Buildup)", ctx.get("futures_text") or NA)
    _section("6️⃣ विकल्प श्रृंखला (Option Chain / PCR)", ctx.get("options_text") or NA)
    _section("7️⃣ डिलीवरी डेटा (Delivery %)", ctx.get("delivery_text") or NA)
    _section("8️⃣ बाहरी संदर्भ (FII/DII + मैक्रो)",
             f"{ctx.get('fii_text') or NA}\n\n{ctx.get('macro_text') or NA}")
    _section("9️⃣ समाचार एवं घोषणाएँ (News & Events)", ctx.get("news_text") or NA)
    _section("🔟 जोखिम, अमान्यकरण एवं विपरीत साक्ष्य",
             f"{ctx.get('risk_text') or NA}\n\n"
             + "\n".join(f"• {item}" for item in (ctx.get("invalidations") or []))
             + (f"\n\nविपरीत साक्ष्य:\n" + "\n".join(f"• {x}" for x in (getattr(hyp, "bear", []) or []))
                if getattr(hyp, "bear", None) else "")
             + (f"\n\nअनुपलब्ध/लापता प्रमाण: {', '.join(getattr(hyp, 'missing', []) or []) or 'कोई नहीं'}"
                if getattr(hyp, "missing", None) else ""))

    if ctx.get("llm_used") and ctx.get("llm_text"):
        st.markdown("---")
        st.markdown("**🤖 AI व्याख्या (Gemini — केवल दिए गए तथ्यों के आधार पर)**")
        st.markdown(str(ctx.get("llm_text")))

    st.markdown("---")
    st.warning("⚠️ यह विश्लेषण केवल उपलब्ध सत्यापित डेटा पर आधारित है। "
               "कोई लाभ की गारंटी नहीं; Futures/OI/PCR/Delivery जैसा डेटा उपलब्ध न हो तो "
               "यहाँ 'Unavailable' लिखा गया है — कोई अनुमान नहीं लगाया गया।")
    st.caption(f"स्रोत: {ctx.get('data_source', '—')} · "
               f"स्कोरिंग: {scored.get('explanation', 'rule-based weighted score')}")


def _hypothesis_context(symbol: str, row, hyp, snap, macro, fii, settings, tf) -> dict:
    """Modal के 10 खंडों का text (वास्तविक डेटा से)।"""
    df = row.get("_df")
    ind = row.get("_ind") or compute_indicators(df) if df is not None else {}
    close = float(row.get("Spot LTP") or getattr(snap, "spot", None) or 0)
    chg = float(row.get("Spot Change %") or 0.0)
    fut = getattr(snap, "futures_ltp", None)
    dif = ((fut - close) / close * 100.0) if (fut and close) else None
    zone = row.get("_zone")
    ratio = ind.get("vol_ratio", 1.0)
    cur_vol = ind.get("volume", 0)
    avg_vol = ind.get("avg_volume", 0.0)

    oi_txt = f"{int(snap.futures_oi):,}" if getattr(snap, "futures_oi", None) else NA
    oi_chg = getattr(snap, "futures_oi_change_pct", None)
    oi_chg_txt = f"{oi_chg:.2f}" if oi_chg is not None else NA
    src_txt = getattr(snap, "source_label", lambda: NA)()
    delivery = getattr(snap, "delivery_pct", None)
    pcr = getattr(snap, "pcr", None)
    anns = getattr(snap, "announcements", None) or []

    if zone is not None:
        zone_txt = (f"{getattr(zone, 'state', NA)} · "
                    f"{'DEMAND (माँग)' if zone.isDemand else 'SUPPLY (आपूर्ति)'} · "
                    f"टाइमफ्रेम {getattr(zone, 'tf', tf)} · प्रॉक्सिमल "
                    f"{getattr(zone, 'proxVal', NA)} · डिस्टल {getattr(zone, 'distVal', NA)}")
        extras = []
        if getattr(zone, "pattern", None):
            extras.append(f"पैटर्न {zone.pattern}")
        if getattr(zone, "category", None):
            extras.append(f"श्रेणी {zone.category}")
        if getattr(zone, "score10", None) is not None:
            extras.append(f"स्कोर {zone.score10}/10")
        if getattr(zone, "hq", False):
            extras.append("HQ (उच्च गुणवत्ता)")
        if getattr(zone, "tests", None):
            extras.append(f"टच {zone.tests}")
        if getattr(zone, "slVal", None) is not None:
            extras.append(f"zone SL {zone.slVal}")
        if getattr(zone, "tpVal", None) is not None:
            extras.append(f"zone TP {zone.tpVal}")
        if getattr(zone, "source", None):
            extras.append(f"स्रोत {zone.source}")
        if extras:
            zone_txt += " · " + " · ".join(extras)
    else:
        zone_txt = NA

    risk = hyp.risk or {}
    ctx = {
        "close": close,
        "chg_pct_txt": f"{chg:+.2f}%",
        "dif_txt": f"{dif:+.2f}%" if dif is not None else "",
        "vol_spike_txt": f"{ratio:.2f}x",
        "macro_text": (" · ".join((macro or {}).get("lines", [])) or NA) +
                      f" (मैक्रो बायस: {(macro or {}).get('bias', 0):+d})",
        "fii_text": (f"FII नेट: {(fii or {}).get('fii_net'):+,.0f} Cr · "
                     f"DII नेट: {(fii or {}).get('dii_net'):+,.0f} Cr · "
                     f"स्रोत: NSE FII/DII live feed · तिथि: {(fii or {}).get('date') or NA}")
        if (fii or {}).get("fii_net") is not None else NA,
        "price_action": (f"भाव ₹{close:,.2f} ({chg:+.2f}%) · EMA35: "
                         f"{ind.get('ema35') or NA} · EMA100: {ind.get('ema100') or NA} · "
                         f"VWAP: {ind.get('vwap') or NA}"),
        "volume_text": (f"वर्तमान वॉल्यूम {cur_vol:,} · 20-बार औसत {avg_vol:,.0f} · "
                        f"स्पाइक अनुपात {ratio:.2f}x (थ्रेशहोल्ड "
                        f"{float(settings.get('vol_spike_threshold', 2.1)):.2f}x)"),
        "zone_text": zone_txt,
        "futures_text": (f"फ्यूचर्स LTP ₹{fut:,.2f} · स्प्रेड {dif:+.2f}% "
                         f"(प्रीमियम/डिस्काउंट) · OI: {oi_txt} · OI परिवर्तन: {oi_chg_txt}% "
                         f"· एक्सपायरी: {getattr(snap, 'expiry', None) or NA} · स्रोत: {src_txt}")
        if fut else NA,
        "options_text": (f"PCR {pcr:.2f} · कुल CE OI {getattr(snap, 'ce_oi', None) or NA} · "
                         f"कुल PE OI {getattr(snap, 'pe_oi', None) or NA} · "
                         f"ATM {getattr(snap, 'atm', None) or NA} · ATM±5 CE/PE OI "
                         f"{getattr(snap, 'ce_oi_atm5', None) or NA}/"
                         f"{getattr(snap, 'pe_oi_atm5', None) or NA}") if pcr is not None else NA,
        "delivery_text": (f"डिलीवरी {delivery:.1f}% · सत्र: "
                          f"{getattr(snap, 'delivery_session', None) or 'लास्ट उपलब्ध सत्र'} · "
                          f"स्रोत: NSE live delivery data") if delivery is not None else NA,
        "news_text": ("<br>".join(f"• {a.get('desc')} ({a.get('date')})" for a in anns)
                      or "कोई सत्यापित घोषणा उपलब्ध नहीं"),
        "risk_text": (f"एंट्री ₹{risk.get('entry')} · स्टॉपलॉस ₹{risk.get('sl')} · "
                      f"लक्ष्य (1:{risk.get('rr')}) ₹{risk.get('target')} · "
                      f"पोजीशन साइज़ {risk.get('qty')} शेयर (जोखिम बजट "
                      f"₹{risk.get('risk_budget', 0):,.0f} = ₹{risk.get('capital', 0):,.0f} का "
                      f"{settings.get('risk_pct', 0.5)}%) · आधार: {risk.get('basis')}"),
        "invalidations": [
            "सक्रिय ज़ोन की डिस्टल लाइन के बाहर क्लोज़",
            "वॉल्यूम स्पाइक के साथ विपरीत दिशा में ब्रेकआउट",
            "FII फ्लो का अचानक उलटना या वैश्विक मैक्रो में तीव्र बदलाव",
        ],
        "hypothesis_text": hyp.narrative,
        "data_source": src_txt,
        "llm_used": False,
    }
    return ctx


def _make_modal_decorator():
    """Streamlit 1.45+ में dialog width; पुराने versions में बिना width।"""
    title = "🏛️ संस्थागत AI बायस — बिंदुवार विश्लेषण"
    try:
        return st.dialog(title, width="large")
    except TypeError:
        return st.dialog(title)


_show_modal = _make_modal_decorator()(_modal_body)


# ==============================================================================
# 7) TAB 1 — NSE ALL FUTURE STOCKS
# ==============================================================================

@st.cache_data(ttl=60, show_spinner=False)
def _buildup_label(fchg: float | None, oi_chg: float | None) -> str:
    """Price + OI behaviour → build-up classification (कोई काल्पनिक डेटा नहीं)।"""
    if fchg is None or oi_chg is None:
        return NA
    if fchg > 0.15 and oi_chg > 0:
        return "Long Buildup"
    if fchg < -0.15 and oi_chg > 0:
        return "Short Buildup"
    if fchg > 0.15 and oi_chg < 0:
        return "Short Covering"
    if fchg < -0.15 and oi_chg < 0:
        return "Long Unwinding"
    return "Neutral"


def _quick_quotes_frame(symbols: tuple[str, ...]) -> pd.DataFrame:
    """⚡ त्वरित scan — सिर्फ़ TradingView/Dhan quotes (कोई candle/ज़ोन नहीं) → 2-5 सेकंड।"""
    snaps = SERVICE.get_snapshots(list(symbols))
    rows = []
    for sym in symbols:
        snap = snaps.get(sym) or SymbolSnapshot(symbol=sym)
        fut = snap.futures_ltp
        spot = snap.spot
        dif = ((fut - spot) / spot * 100.0) if (fut and spot) else None
        rows.append({
            "Symbol": sym,
            "Spot LTP": spot,
            "Spot Change %": round(snap.spot_change_pct, 2) if snap.spot_change_pct is not None else 0.0,
            "Futures LTP": f"₹{fut:,.2f}" if fut else NA,
            "Dif. %": f"{dif:+.2f}%" if dif is not None else NA,
            "Futures Change %": f"{snap.futures_change_pct:+.2f}%" if snap.futures_change_pct is not None else NA,
            "Futures OI": f"{int(snap.futures_oi):,}" if snap.futures_oi else NA,
            "Futures OI Change %": (f"{snap.futures_oi_change_pct:+.2f}%"
                                    if snap.futures_oi_change_pct is not None else NA),
            "Selected-Timeframe Volume": NA,
            "Volume Spike Ratio": NA,
            "Latest Verified Delivery %": NA,
            "PCR": f"{snap.pcr:.2f}" if snap.pcr is not None else NA,
            "CE Total OI": f"{int(snap.ce_oi):,}" if snap.ce_oi is not None else NA,
            "PE Total OI": f"{int(snap.pe_oi):,}" if snap.pe_oi is not None else NA,
            "Buildup Classification": _buildup_label(snap.futures_change_pct,
                                                    snap.futures_oi_change_pct),
            "EMA 35 Signal": NA, "EMA 100 Signal": NA, "VWAP Signal": NA,
            "Zone Signal": NA, "Live Signals Highlight": "",
            "Data Source": snap.source_label(),
            "_Enrich": snap,
        })
    return pd.DataFrame(rows)


def _show_source_health() -> None:
    """कौन-सा source ज़िंदा है — उपयोगकर्ता को तुरंत दिखे।"""
    rows = source_health_rows()
    if not rows:
        return
    with st.expander("🔌 डेटा-स्रोत स्वास्थ्य (इस स्कैन में क्या fetch हुआ)", expanded=False):
        try:
            st.dataframe(pd.DataFrame(rows).astype(str), hide_index=True, **df_layout(st))
        except Exception:
            st.dataframe(pd.DataFrame(rows).astype(str), hide_index=True)


def _zone_from_row(row) -> Any:
    """flat zone columns → _Zone object (modal के लिए)।"""
    try:
        return _Zone(state=str(row.get("_ZoneState", NA)),
                     isDemand=bool(row.get("_ZoneDemand", True)),
                     proxVal=float(row.get("_ZoneProx") or 0.0),
                     distVal=float(row.get("_ZoneDist") or 0.0),
                     tf=str(row.get("Near Timeframe", "1h")),
                     pattern=row.get("_ZonePattern"),
                     score10=row.get("_ZoneScore"),
                     slVal=_f(row.get("_ZoneSL")),
                     tpVal=_f(row.get("_ZoneTP")),
                     source=str(row.get("_ZoneSource", "") or ""))
    except Exception:
        return None


def _render_nse_tab(settings: dict) -> None:
    """मास्टर टैब — खुलते ही कुछ fetch नहीं (instant open), स्कैन बटन से।"""
    selected_tf = settings.get("selected_tf", "Daily")
    universe, universe_src = SERVICE.get_universe()
    if not universe:
        st.warning("F&O universe उपलब्ध नहीं (Dhan scrip master unreachable) — "
                   "Settings tab में provider status देखें।")
        return

    st.markdown("### NSE ALL FUTURES — LIVE SCAN")
    st.caption(f"यूनिवर्स: **{universe_src}** · {len(universe)} स्टॉक्स · {engine_status_line()}")

    # ---------------- controls (कोई data fetch नहीं → तुरंत खुलता है) --------
    c1, c2, c3, c4 = st.columns([1.7, 1.5, 1.3, 1.5])
    with c1:
        _scope_options = ["⚡ त्वरित (40)", "मध्यम (100)", "पूरा (सभी F&O)"]
        scope_label = st.selectbox("स्कैन सीमा", _scope_options,
                                    index=0, key="tab1_scope")
    with c2:
        new_tf = st.selectbox("Timeframe", ALL_TIMEFRAMES,
                              index=ALL_TIMEFRAMES.index(selected_tf)
                              if selected_tf in ALL_TIMEFRAMES else 8,
                              key="tab1_tf_select")
    with c3:
        deep = st.checkbox("डीप स्कैन\n(ज़ोन + EMA)", value=True, key="tab1_deep")
    with c4:
        run = st.button("🚀 स्कैन चलाएँ", type="primary", use_container_width=True)
        refresh = st.button("↻ कैश रिफ़्रेश", use_container_width=True)

    if refresh:
        st.cache_data.clear()
        try:
            SERVICE.clear_cache()
        except Exception:
            pass
        st.session_state.pop("_master_request", None)
        st.session_state.pop("master_nse_frame", None)
        st.rerun()

    scope = {"⚡ त्वरित (40)": 40, "मध्यम (100)": 100}.get(scope_label, 0)
    _cap = int(settings.get("scan_limit", 0) or 0)     # env AAP_SCAN_LIMIT (0 = कोई cap नहीं)
    if _cap > 0:
        scope = _cap if scope == 0 else min(scope, _cap)
    if run:
        symbols = tuple(universe[:scope] if scope else universe)
        st.session_state["_master_request"] = {"symbols": symbols, "tf": new_tf,
                                               "deep": bool(deep), "scope": scope_label}
        st.rerun()

    request = st.session_state.get("_master_request")
    if not request:
        st.info("🚀 **स्कैन चलाने के लिए ऊपर 'स्कैन चलाएँ' दबाएँ.** ऐप तुरंत खुलता है — "
                "डेटा सिर्फ़ आपके दबाव पर लाता है (कोई अटकता हुआ स्कैन नहीं).\n\n"
                "*⚡ त्वरित = सिर्फ़ Futures/OI quotes (2-5 सेकंड) · डीप = candle + ज़ोन + EMA "
                "(ज़्यादा समय, ज़्यादा विश्लेषण)*")
        _show_source_health()
        return

    symbols = tuple(request.get("symbols") or ())
    tf = str(request.get("tf") or selected_tf)
    deep = bool(request.get("deep"))
    if not symbols:
        st.session_state.pop("_master_request", None)
        st.rerun()

    t0 = time.time()
    if deep:
        with st.spinner(f"डीप स्कैन: {len(symbols)} प्रतीक · {tf} (ज़ोन + EMA + VWAP)…"):
            frame = _build_master_frame(symbols, tf, json.dumps(settings, default=str))
            frame = _enrich_frame(frame, int(settings.get("enrich_limit", 60)))
        arts = _ARTIFACTS.get(f"master::{tf}::{abs(hash(tuple(symbols)))}", {})
        df_map = arts.get("df") or {}
        zone_map = arts.get("zone") or {}
        frame["_df"] = [df_map.get(s) for s in frame["Symbol"]]
        frame["_zone"] = [zone_map.get(s) for s in frame["Symbol"]]
    else:
        with st.spinner(f"त्वरित scan: {len(symbols)} प्रतीक (Futures + OI)…"):
            frame = _quick_quotes_frame(symbols)
        frame["_df"] = [None] * len(frame)
        frame["_zone"] = [None] * len(frame)
    elapsed = time.time() - t0

    if frame.empty:
        st.info("डेटा लोड नहीं हो सका — Settings tab में source health देखें।")
        return

    macro = SERVICE.get_macro()
    fii = SERVICE.get_fii()

    hyps, rows = {}, []
    for _, row in frame.iterrows():
        snap = row.get("_Enrich") or SymbolSnapshot(symbol=row["Symbol"])
        if getattr(snap, "lot_size", None) is None and row.get("_lot_size"):
            try:
                snap.lot_size = _f(row.get("_lot_size"))
            except Exception:
                pass
        hyp = _run_hypothesis(row["Symbol"], row.get("_df"), row.get("_zone"), snap,
                              macro, fii, settings, tf)
        hyps[row["Symbol"]] = hyp
        rows.append(hyp.bias)
    frame["_Raw_Bias"] = rows
    frame["Final AI Bias"] = [b.title().replace("No Trade", "NO TRADE") for b in rows]
    st.session_state["master_nse_frame"] = frame
    st.caption(f"✅ स्कैन पूरा — {len(frame)} प्रतीक · {elapsed:.1f} सेकंड · "
               f"मोड: {'डीप' if deep else 'त्वरित'} · TF {tf}")

    token = uuid.uuid4().hex[:8]
    frame["_Bias_Link_URL"] = [
        f"?bias={sym}&b={str(bias).upper()}&t={token}"
        for sym, bias in zip(frame["Symbol"], frame["_Raw_Bias"])]
    frame["_Spot_Chart_URL"] = frame["Symbol"].map(
        lambda s: f"https://in.tradingview.com/chart/?symbol={url_quote('NSE:' + s, safe='')}")
    frame["_Fut_Chart_URL"] = [
        (f"https://in.tradingview.com/chart/?symbol={url_quote('NSE:' + s + '1!', safe='')}"
         + (f"#price=₹{str(v).replace('₹', '')}" if v and v != NA else ""))
        for s, v in zip(frame["Symbol"], frame["Futures LTP"])
    ]

    def _open_modal(symbol: str) -> None:
        r = frame[frame["Symbol"] == symbol].iloc[0]
        snap = r.get("_Enrich") or SymbolSnapshot(symbol=symbol)
        hyp = hyps.get(symbol)
        ctx = _hypothesis_context(symbol, r, hyp, snap, macro, fii, settings, tf)
        llm = _llm_text(hyp, {"macro": macro, "fii": fii})
        if llm:
            ctx["llm_used"] = True
            ctx["llm_text"] = llm
            ctx["hypothesis_text"] = llm + "\n\n---\n**नियम-आधारित सत्यापन:**\n" + hyp.narrative
        _show_modal(symbol, str(r["Final AI Bias"]), ctx, hyp, hyp.risk, tf)

    qp_sym = st.query_params.get("bias", None)
    if qp_sym and qp_sym in set(frame["Symbol"].tolist()):
        try:
            st.query_params.clear()
        except Exception:
            pass
        _open_modal(qp_sym)

    pending = st.session_state.pop("_modal_symbol", None)
    if pending and pending in set(frame["Symbol"].tolist()):
        _open_modal(pending)

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
                st.info("वर्तमान में कोई मज़बूत सेटअप नहीं (या डीप स्कैन चलाएँ).")
                continue
            disp = linkify_table(data[top_cols + ["_Spot_Chart_URL", "_Fut_Chart_URL",
                                                   "_Bias_Link_URL"]].copy())
            st.dataframe(disp.style.apply(row_styles, axis=1),
                         column_config=master_column_config(st),
                         hide_index=True, height=190, **df_layout(st))

    st.markdown("---")
    st.markdown("#### 📊 मास्टर तालिका — किसी भी **Final AI Bias** पर क्लिक करके पूरा हिंदी विश्लेषण खोलें")
    cols_to_render = [c for c in ORDERED_COLUMNS_TAB1 if c in frame.columns]
    hidden = [c for c in ("_Spot_Chart_URL", "_Fut_Chart_URL", "_Bias_Link_URL")
              if c in frame.columns]
    display = linkify_table(frame[cols_to_render + hidden].copy())
    event = None
    try:
        event = st.dataframe(display.style.apply(row_styles, axis=1),
                             column_config=master_column_config(st), hide_index=True,
                             height=560, on_select="rerun", selection_mode="single-row",
                             **df_layout(st))
    except (TypeError, ValueError):
        event = st.dataframe(display.style.apply(row_styles, axis=1),
                             column_config=master_column_config(st), hide_index=True,
                             height=560, **df_layout(st))
    sel_rows: list = []
    try:
        selection = getattr(event, "selection", None)
        if isinstance(selection, dict):
            sel_rows = list(selection.get("rows") or [])
        elif selection is not None:
            sel_rows = list(getattr(selection, "rows", []) or [])
    except Exception:
        sel_rows = []
    if sel_rows:
        idx = int(sel_rows[0])
        if 0 <= idx < len(frame) and st.session_state.get("_last_sel_row") != idx:
            st.session_state["_last_sel_row"] = idx
            st.session_state["_modal_symbol"] = str(frame.iloc[idx]["Symbol"])
            st.rerun()
    st.caption("Futures LTP/OI — TradingView (≈15 मिनट delayed) या NSE official F&O bhavcopy "
               "(पिछला session close) · PCR — NSE option chain (bhavcopy) · "
               "Delivery % — NSE delivery file उपलब्ध नहीं ⇒ 'Unavailable' (कोई काल्पनिक आँकड़ा नहीं). "
               "पूरा विश्लेषण खोलने के लिए **Final AI Bias** सेल पर क्लिक करें (या पंक्ति चुनें).")
    _show_source_health()


# ==============================================================================
# 8) TAB 2 — ZONE (multi-timeframe nearest zone)
# ==============================================================================

@st.cache_data(ttl=300, show_spinner=False)
def _scan_zones(symbols: tuple[str, ...], tfs_json: str, settings_json: str) -> list[dict]:
    settings = json.loads(settings_json)
    tfs = json.loads(tfs_json)
    bundle = SERVICE.get_bundle(symbols, tfs)
    lots = {c.get("symbol"): c.get("lot_size") for c in SERVICE.get_contracts()}
    out: list[dict] = []
    for sym in symbols:
        best = None
        for tf in tfs:
            df = SERVICE.frame(bundle, tf, sym)
            if df is None or len(df) < 12:
                continue
            try:
                zones = scan_zones(df[["open", "high", "low", "close", "volume"]].copy(),
                                   params=zone_params(settings), tf=tf)
                zone, dist = pick_nearest(latest_active_zones(zones),
                                          float(df["close"].iloc[-1]))
            except Exception:
                continue
            if zone is None or dist is None:
                continue
            price = float(df["close"].iloc[-1])
            dist_pct = (dist / price * 100.0) if price else 999.0
            cand = (dist_pct, ALL_TIMEFRAMES.index(tf) if tf in ALL_TIMEFRAMES else 99,
                    tf, zone, dist, df, price)
            if best is None or (cand[0], cand[1]) < (best[0], best[1]):
                best = cand
        if best is None:
            continue
        dist_pct, _r, tf, zone, _dist, df, price = best
        prox = float(zone.proxVal)
        distal = float(zone.distVal)
        is_demand = bool(zone.isDemand)
        risk = build_risk_setup(price, zone, None, settings, is_demand, lots.get(sym))
        if getattr(zone, "slVal", None) is None:
            # inline engine → distal buffer लगाएँ
            buf = float(settings.get("distal_buffer_pct", 0.5))
            adj = distal * (1 - buf / 100.0) if is_demand else distal * (1 + buf / 100.0)
            rr = float(settings.get("target_rr", 5.0))
            risk.sl = round(adj, 2)
            risk.per_unit_risk = round(abs(risk.entry - risk.sl), 2)
            risk.target = round(risk.entry + risk.per_unit_risk * rr if is_demand
                                else risk.entry - risk.per_unit_risk * rr, 2)
            budget = float(settings.get("account_capital", 25000.0)) * \
                float(settings.get("risk_pct", 0.5)) / 100.0
            risk.qty = int(math.floor(budget / risk.per_unit_risk)) \
                if risk.per_unit_risk > 0 else 0
        prev = float(df["close"].iloc[-2]) if len(df) > 1 else price
        chg = ((price - prev) / prev * 100.0) if prev else 0.0
        out.append({
            "Symbols": sym, "LTP": round(price, 2), "_Change_Pct": round(chg, 2),
            "demand/Supply": "Demand" if is_demand else "Supply",
            "Near Timeframe": tf, "Zone State": str(zone.state),
            "Zone Pattern": str(getattr(zone, "pattern", None) or NA),
            "Zone Score": getattr(zone, "score10", None) if getattr(zone, "score10", None) is not None else NA,
            "Entry Price (Proximal)": round(prox, 2),
            "Distal Stoploss": risk.sl,
            "Target Price": risk.target,
            "Quantity (Capital According)": risk.qty,
            "Distance %": round(dist_pct, 2),
            "AI Bias": "BUY" if (is_demand and chg > 0) else
                       ("SELL" if (not is_demand and chg < 0) else "NO TRADE"),
            "_ZoneState": str(zone.state),
            "_ZoneProx": prox, "_ZoneDist": distal, "_ZoneDemand": is_demand,
            "_ZonePattern": str(getattr(zone, "pattern", None) or NA),
            "_ZoneScore": getattr(zone, "score10", None),
            "_ZoneSL": getattr(zone, "slVal", None), "_ZoneTP": getattr(zone, "tpVal", None),
            "_ZoneSource": str(getattr(zone, "source", "") or ""),
            "_Risk": risk.to_dict(),
        })
    return out


def _render_zone_tab(settings: dict) -> None:
    """ज़ोन टैब — स्कैन बटन से (तुरंत खुलता है), सभी चुने TF में नज़दीकी ज़ोन।"""
    st.markdown("### 🎯 ZONE SCANNER — सभी टाइमफ्रेम में सबसे नज़दीकी ज़ोन")
    st.caption("15m→Monthly तक सभी चुने TF पर candidate zones → सबसे नज़दीकी सक्रिय ज़ोन "
               "(Fresh/Tested) + Entry / SL / Target / capital-risk sizing · "
               f"{engine_status_line()}")

    universe, _src = SERVICE.get_universe()
    c1, c2, c3 = st.columns([1.5, 2.0, 2.5])
    with c1:
        run = st.button("🚀 ज़ोन स्कैन चलाएँ", type="primary", use_container_width=True)
    with c2:
        mode = st.selectbox("मल्टी-टाइमफ्रेम स्कैन",
                            ["सभी टाइमफ्रेम", "केवल Daily", "केवल Intraday (≤1h)"],
                            key="zone_scan_mode")
    with c3:
        limit_label = st.selectbox("प्रतीक सीमा", ["40", "80", "150", "सभा"],
                                    key="zone_limit")
    if not universe:
        st.info("यूनिवर्स उपलब्ध नहीं।")
        return
    if run:
        limit = 0 if limit_label == "सभा" else int(limit_label)
        symbols = tuple(universe[:limit] if limit else universe)
        st.session_state["_zone_request"] = {"symbols": symbols, "mode": mode}
        st.rerun()

    request = st.session_state.get("_zone_request")
    if not request:
        st.info("🚀 **'ज़ोन स्कैन चलाएँ' दबाएँ** — ऐप तुरंत खुलता है, डेटा आपके "
                "दबाव पर आता है. 2h/4h/6h/8h 1h फ़ीड से, Weekly/Monthly daily फ़ीड से.")
        _show_source_health()
        return

    symbols = tuple(request.get("symbols") or ())
    mode = str(request.get("mode") or "सभी टाइमफ्रेम")
    if mode == "केवल Daily":
        tfs = ["Daily"]
    elif mode == "केवल Intraday (≤1h)":
        tfs = [tf for tf in ACTIVE_TFS if tf in ("15m", "30m", "75m", "1h")]
    else:
        tfs = ACTIVE_TFS
    t0 = time.time()
    with st.spinner(f"{len(symbols)} प्रतीक × {len(tfs)} टाइमफ्रेम ज़ोन स्कैन…"):
        rows = _scan_zones(symbols, json.dumps(tfs), json.dumps(settings, default=str))
    st.caption(f"✅ ज़ोन स्कैन पूरा — {len(rows)} प्रतीकों में सक्रिय ज़ोन · "
               f"{time.time() - t0:.1f} सेकंड")

    if not rows:
        st.info("चयनित स्टॉक्स में कोई सक्रिय Fresh/Tested ज़ोन नहीं मिला "
                "(या candle डेटा उपलब्ध नहीं) — Settings tab में source health देखें.")
        return

    df_res = pd.DataFrame(rows)
    token = uuid.uuid4().hex[:8]
    df_res["_Chart_URL"] = df_res["Symbols"].map(
        lambda s: f"https://in.tradingview.com/chart/?symbol={url_quote('NSE:' + s, safe='')}&interval=60")
    df_res["_Bias_Link_URL"] = [
        f"?znsym={sym}&b={str(bias).upper()}&t={token}"
        for sym, bias in zip(df_res["Symbols"], df_res["AI Bias"])]

    qp = st.query_params.get("znsym", None)
    if qp and qp in set(df_res["Symbols"].tolist()):
        try:
            st.query_params.clear()
        except Exception:
            pass
        r = df_res[df_res["Symbols"] == qp].iloc[0]
        zone = _zone_from_row(r)
        risk = dict(r.get("_Risk") or {})
        ctx = {
            "close": float(r["LTP"]), "chg_pct_txt": f"{r['_Change_Pct']:+.2f}%",
            "dif_txt": "", "vol_spike_txt": NA,
            "macro_text": "ज़ोन टैब — मैक्रो संदर्भ टैब 4 में",
            "fii_text": "ज़ोन टैब — FII/DII टैब 4 में",
            "price_action": f"भाव ₹{float(r['LTP']):,.2f}",
            "volume_text": NA,
            "zone_text": (f"{r['Zone State']} · {r['demand/Supply']} · टाइमफ्रेम "
                          f"{r['Near Timeframe']} · प्रॉक्सिमल {r['Entry Price (Proximal)']} · "
                          f"डिस्टल {r['Distal Stoploss']} · दूरी {r['Distance %']}% · "
                          f"पैटर्न {r.get('Zone Pattern', NA)}"),
            "futures_text": "ज़ोन टैब में Futures डेटा शामिल नहीं (टैब 1 में उपलब्ध)",
            "options_text": "ज़ोन टैब में Options डेटा शामिल नहीं (टैब 1 में उपलब्ध)",
            "delivery_text": "ज़ोन टैब में Delivery डेटा शामिल नहीं (टैब 1 में उपलब्ध)",
            "news_text": "ज़ोन टैब में समाचार डेटा शामिल नहीं (टैब 4 में उपलब्ध)",
            "risk_text": (f"एंट्री ₹{risk.get('entry')} · SL ₹{risk.get('sl')} · "
                          f"लक्ष्य ₹{risk.get('target')} · साइज़ {risk.get('qty')}"),
            "invalidations": [f"डिस्टल लाइन ₹{risk.get('sl')} के बाहर क्लोज़",
                              "टाइमफ्रेम बदलने पर ज़ोन की प्राथमिकता बदल सकती है"],
            "hypothesis_text": (f"**{qp}** — **{r['AI Bias']}** (ज़ोन आधारित, "
                                f"{r['Near Timeframe']})"),
            "data_source": "zone engine: " + str(r.get("_ZoneSource", "") or NA),
            "llm_used": False,
        }
        _show_modal(qp, str(r["AI Bias"]), ctx, _SimpleHyp(str(r["AI Bias"]), ctx["hypothesis_text"]),
                    risk, str(r["Near Timeframe"]))

    show = ["Symbols", "LTP", "demand/Supply", "Near Timeframe", "Zone State",
            "Zone Pattern", "Zone Score",
            "Entry Price (Proximal)", "Distal Stoploss", "Target Price",
            "Quantity (Capital According)", "Distance %", "AI Bias"]
    disp = linkify_table(df_res[show + ["_Chart_URL", "_Bias_Link_URL"]].copy())
    st.dataframe(disp.style.apply(zone_styles, axis=1),
                 column_config={
                     "Symbols": st.column_config.LinkColumn("Symbols", display_text=r".*symbol=NSE%3A([^&#]+).*"),
                     "AI Bias": st.column_config.LinkColumn("AI Bias", display_text=r".*&b=([A-Z ]+)&t=.*"),
                     "LTP": st.column_config.NumberColumn("LTP", format="₹%.2f"),
                     "Entry Price (Proximal)": st.column_config.NumberColumn("Entry Price (Proximal)", format="₹%.2f"),
                     "Distal Stoploss": st.column_config.NumberColumn("Distal Stoploss", format="₹%.2f"),
                     "Target Price": st.column_config.NumberColumn("Target Price", format="₹%.2f"),
                     "Distance %": st.column_config.NumberColumn("Distance %", format="%.2f%%"),
                     "Quantity (Capital According)": st.column_config.NumberColumn("Quantity (Capital According)", format="%d"),
                 }, hide_index=True, height=560, **df_layout(st))
    _show_source_health()


class _SimpleHyp:
    """Zone / Global modal के लिए हल्का hypothesis wrapper।"""

    def __init__(self, bias: str, narrative: str) -> None:
        self.bias = bias
        self.narrative = narrative
        self.bull: list[str] = []
        self.bear: list[str] = []
        self.missing: list[str] = []
        self.evidence: list = []


# ==============================================================================
# 9) TAB 3 — GLOBAL ZONE
# ==============================================================================

@st.cache_data(ttl=300, show_spinner=False)
def _scan_global_zones(tfs_json: str, settings_json: str) -> list[dict]:
    settings = json.loads(settings_json)
    tfs = json.loads(tfs_json)
    tickers = [i["ticker"] for i in GLOBAL_INSTRUMENTS]
    import yfinance as yf
    bundle: dict = {}
    for tf in tfs:
        interval = {"15m": "15m", "30m": "30m", "1h": "60m", "Daily": "1d",
                    "Weekly": "1wk", "Monthly": "1mo"}.get(tf, "1d")
        period = {"15m": "1mo", "30m": "1mo", "1h": "3mo", "Daily": "1y",
                  "Weekly": "1y", "Monthly": "1y"}.get(tf, "1y")
        try:
            data = yf.download(tickers, period=period, interval=interval, group_by="ticker",
                               progress=False, threads=True, auto_adjust=False)
        except Exception:
            data = pd.DataFrame()
        per: dict = {}
        for item in GLOBAL_INSTRUMENTS:
            tkr = item["ticker"]
            try:
                blk = data[tkr] if (isinstance(data.columns, pd.MultiIndex)
                                    and tkr in data.columns.get_level_values(0)) else data
            except Exception:
                continue
            if blk is None or len(blk) == 0:
                continue
            blk = blk.copy()
            blk.columns = [str(c).lower() for c in blk.columns]
            if "close" not in blk.columns:
                continue
            blk = blk.dropna(subset=["close"])
            if len(blk):
                per[tkr] = blk
        bundle[tf] = per

    out: list[dict] = []
    for item in GLOBAL_INSTRUMENTS:
        best = None
        for tf in tfs:
            df = (bundle.get(tf) or {}).get(item["ticker"])
            if df is None or len(df) < 12:
                continue
            try:
                zones = scan_zones(df[["open", "high", "low", "close", "volume"]].copy(),
                                   params=zone_params(settings), tf=tf)
                zone, dist = pick_nearest(latest_active_zones(zones),
                                          float(df["close"].iloc[-1]))
            except Exception:
                continue
            if zone is None or dist is None:
                continue
            price = float(df["close"].iloc[-1])
            cand = ((dist / price * 100.0) if price else 999.0,
                    ALL_TIMEFRAMES.index(tf) if tf in ALL_TIMEFRAMES else 99,
                    tf, zone, dist, df, price)
            if best is None or (cand[0], cand[1]) < (best[0], best[1]):
                best = cand
        if best is None:
            continue
        _dp, _r, tf, zone, _dist, df, price = best
        prox = float(zone.proxVal)
        distal = float(zone.distVal)
        is_demand = bool(zone.isDemand)
        risk = build_risk_setup(price, zone, None, settings, is_demand)
        if getattr(zone, "slVal", None) is None:
            buf = float(settings.get("distal_buffer_pct", 0.5))
            adj = distal * (1 - buf / 100.0) if is_demand else distal * (1 + buf / 100.0)
            rr = float(settings.get("target_rr", 5.0))
            risk.sl = round(adj, 4)
            risk.per_unit_risk = round(abs(risk.entry - risk.sl), 4)
            risk.target = round(risk.entry + risk.per_unit_risk * rr if is_demand
                                else risk.entry - risk.per_unit_risk * rr, 4)
        prev = float(df["close"].iloc[-2]) if len(df) > 1 else price
        chg = ((price - prev) / prev * 100.0) if prev else 0.0
        out.append({
            "Symbol": item["id"], "Name": item["name"], "LTP": round(price, 4),
            "_Change_Pct": round(chg, 2),
            "Demand/Supply": "Demand" if is_demand else "Supply",
            "Near Timeframe": tf, "Zone State": str(zone.state),
            "Zone Pattern": str(getattr(zone, "pattern", None) or NA),
            "Entry Price": round(prox, 4), "Distal Stoploss": risk.sl,
            "Target Price": risk.target,
            "AI Bias": "BUY" if (is_demand and chg > 0) else
                       ("SELL" if (not is_demand and chg < 0) else "NEUTRAL"),
            "_ZoneState": str(zone.state),
            "_ZoneProx": prox, "_ZoneDist": distal, "_ZoneDemand": is_demand,
            "_ZonePattern": str(getattr(zone, "pattern", None) or NA),
            "_ZoneSL": getattr(zone, "slVal", None), "_ZoneTP": getattr(zone, "tpVal", None),
        })
    return out


def _render_global_zone_tab(settings: dict) -> None:
    st.markdown("### 🌐 GLOBAL INSTRUMENTS — मल्टी-टाइमफ्रेम ज़ोन स्कैनर")
    st.caption("DXY, US10Y, सोना, क्रूड, US30, S&P500, A50, Nikkei — वास्तविक OHLCV से "
               "निकला नज़दीकी माँग/आपूर्ति ज़ोन + TradingView लिंक।")

    c1, c2 = st.columns([1.6, 3.0])
    with c1:
        run = st.button("🚀 ग्लोबल ज़ोन स्कैन", type="primary", use_container_width=True)
    with c2:
        tf_label = st.selectbox("टाइमफ्रेम", ["1h + Daily", "केवल 1h", "केवल Daily",
                                              "1h + 4h + Daily"], key="global_tfs")
    if run:
        st.session_state["_global_request"] = tf_label
        st.rerun()
    if not st.session_state.get("_global_request"):
        st.info("🚀 **'ग्लोबल ज़ोन स्कैन' दबाएँ** — 26 वैश्विक instruments (DXY, US10Y, "
                "Gold, Crude, A50, Nikkei, NIFTY/GIFT futures) पर multi-timeframe ज़ोन।")
        _show_source_health()
        return

    tfs = {"केवल 1h": ["1h"], "केवल Daily": ["Daily"],
           "1h + 4h + Daily": ["1h", "4h", "Daily"]}.get(
        str(st.session_state.get("_global_request")), ["1h", "Daily"])
    t0 = time.time()
    with st.spinner("ग्लोबल ज़ोन स्कैन चल रहा है…"):
        rows = _scan_global_zones(json.dumps(tfs), json.dumps(settings, default=str))
    st.caption(f"✅ ग्लोबल स्कैन पूरा — {len(rows)} instruments · {time.time() - t0:.1f} सेकंड")

    if not rows:
        st.info("ग्लोबल मार्केट्स में इस समय कोई सक्रिय Fresh/Tested ज़ोन नहीं मिला "
                "(या OHLCV उपलब्ध नहीं) — source health नीचे देखें।")
        _show_source_health()
        return

    df_gz = pd.DataFrame(rows)
    token = uuid.uuid4().hex[:8]
    df_gz["_Chart_URL"] = df_gz["Symbol"].map(
        lambda s: "https://in.tradingview.com/chart/?symbol=" + url_quote(
            next((i["tv"] for i in GLOBAL_INSTRUMENTS if i["id"] == s), s), safe=""))
    df_gz["_Bias_Link_URL"] = [
        f"?gznsym={sym}&b={str(bias).upper()}&t={token}"
        for sym, bias in zip(df_gz["Symbol"], df_gz["AI Bias"])]

    qp = st.query_params.get("gznsym", None)
    if qp and qp in set(df_gz["Symbol"].tolist()):
        try:
            st.query_params.clear()
        except Exception:
            pass
        r = df_gz[df_gz["Symbol"] == qp].iloc[0]
        zone = _zone_from_row(r)
        setup = build_risk_setup(float(r["LTP"]), zone, None, settings,
                                 str(r["AI Bias"]) == "BUY")
        risk = setup.to_dict()
        ctx = {
            "close": float(r["LTP"]), "chg_pct_txt": f"{r['_Change_Pct']:+.2f}%",
            "dif_txt": "", "vol_spike_txt": NA,
            "macro_text": "ग्लोबल इंस्ट्रूमेंट — मैक्रो संदर्भ इसी टैब से",
            "fii_text": "FII/DII डेटा टैब 4 (NSE लाइव) में उपलब्ध",
            "price_action": f"भाव {float(r['LTP']):,.4f} ({r['_Change_Pct']:+.2f}%)",
            "volume_text": NA,
            "zone_text": (f"{r['Zone State']} · {r['Demand/Supply']} · "
                          f"टाइमफ्रेम {r['Near Timeframe']} · प्रॉक्सिमल {r['Entry Price']} · "
                          f"डिस्टल {r['Distal Stoploss']} · पैटर्न {r.get('Zone Pattern', NA)}"),
            "futures_text": "ग्लोबल इंस्ट्रूमेंट के लिए NSE F&O डेटा लागू नहीं",
            "options_text": "ग्लोबल इंस्ट्रूमेंट के लिए Option Chain लागू नहीं",
            "delivery_text": "ग्लोबल इंस्ट्रूमेंट के लिए Delivery लागू नहीं",
            "news_text": "समाचार/घोषणाएँ टैब 4 में उपलब्ध",
            "risk_text": (f"एंट्री {r['Entry Price']} · SL {r['Distal Stoploss']} · "
                          f"लक्ष्य {r['Target Price']}"),
            "invalidations": ["डिस्टल लाइन के बाहर क्लोज़", "मैक्रो शॉक"],
            "hypothesis_text": (f"**{qp}** — **{r['AI Bias']}**\n\n"
                                f"• ज़ोन: {r['Zone State']} {r['Demand/Supply']} @ "
                                f"{r['Near Timeframe']}\n• एंट्री {r['Entry Price']} · "
                                f"SL {r['Distal Stoploss']} · लक्ष्य {r['Target Price']}"),
            "llm_used": False,
        }
        _show_modal(qp, str(r["AI Bias"]), ctx,
                    _SimpleHyp(str(r["AI Bias"]), ctx["hypothesis_text"]), risk,
                    str(r["Near Timeframe"]))

    g_cols = ["Symbol", "Name", "LTP", "Demand/Supply", "Near Timeframe", "Zone State",
              "Zone Pattern", "Entry Price", "Distal Stoploss", "Target Price", "AI Bias"]
    disp = linkify_table(df_gz[g_cols + ["_Chart_URL", "_Bias_Link_URL"]].copy())
    st.dataframe(disp.style.apply(global_styles, axis=1),
                 column_config={
                     "Symbol": st.column_config.LinkColumn("Symbol", display_text=r".*symbol=([^&#]+).*"),
                     "AI Bias": st.column_config.LinkColumn("AI Bias", display_text=r".*&b=([A-Z ]+)&t=.*"),
                     "LTP": st.column_config.NumberColumn("LTP", format="%.4f"),
                     "Entry Price": st.column_config.NumberColumn("Entry Price", format="%.4f"),
                     "Distal Stoploss": st.column_config.NumberColumn("Distal Stoploss", format="%.4f"),
                     "Target Price": st.column_config.NumberColumn("Target Price", format="%.4f"),
                 }, hide_index=True, height=560, **df_layout(st))
    _show_source_health()


# ==============================================================================
# 10) TAB 4 — NEWS & EVENTS
# ==============================================================================

def _render_news_events_tab(settings: dict) -> None:
    st.markdown("### 📰 GLOBAL STRIP & INSTITUTIONAL EVENTS")

    gift = SERVICE.get_gift_nifty() or {}
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
        import yfinance as yf
        tickers_list = [x["ticker"] for x in strip_items]
        data = yf.download(tickers_list, period="5d", interval="1d", group_by="ticker",
                           progress=False, threads=True, auto_adjust=False)
        for idx, item in enumerate(strip_items):
            tkr = item["ticker"]
            blk = (data[tkr] if (isinstance(data.columns, pd.MultiIndex)
                                 and tkr in data.columns.get_level_values(0)) else data)
            if blk is None or len(blk) == 0:
                continue
            blk = blk.copy()
            blk.columns = [str(c).lower() for c in blk.columns]
            if "close" not in blk.columns:
                continue
            blk = blk.dropna(subset=["close"])
            if len(blk) < 2:
                continue
            p0 = float(blk["close"].iloc[-1])
            p1 = float(blk["close"].iloc[-2])
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
        st.info("मास्टर फ़्रेम अभी लोड नहीं हुआ — मास्टर स्कैन चलाने के लिए बटन दबाएँ "
                "(या 'NSE ALL FUTURE STOCKS' टैब खोलें)।")
        if st.button("मास्टर स्कैन चलाएँ", type="primary"):
            universe, _src = SERVICE.get_universe()
            limit = int(settings.get("scan_limit", 0) or 0)
            symbols = tuple(universe[:limit] if limit > 0 and universe else universe)
            with st.spinner("मास्टर स्कैन चल रहा है…"):
                frame = _build_master_frame(symbols, settings.get("selected_tf", "Daily"),
                                             json.dumps(settings, default=str))
                frame = _enrich_frame(frame, int(settings.get("enrich_limit", 60)))
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
    for col, label, value, key in ((b1, "Advances", advances, "ADVANCES"),
                                   (b2, "Declines", declines, "DECLINES"),
                                   (b3, "Gap-Up", gap_ups, "GAP_UP"),
                                   (b4, "Gap-Down", gap_downs, "GAP_DOWN")):
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
        st.dataframe(linkify_table(sub[cols_sel + ["_Spot_Chart_URL", "_Fut_Chart_URL",
                                                    "_Bias_Link_URL"]]),
                     column_config=master_column_config(st), hide_index=True, height=280,
                     **df_layout(st))

    st.markdown("---")
    e1, e2 = st.columns(2)
    with e1:
        fii = SERVICE.get_fii() or {}
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
        st.caption(f"रिपोर्टिंग तिथि: {fii.get('date') or NA} · स्रोत: NSE FII/DII live feed · "
                   f"सत्र: {SERVICE.get_market_status()}")
        st.markdown("#### 🌅 Pre-Market Analysis (सुबह 08:30 IST)")
        st.caption("FII/DII प्रवाह + GIFT Nifty संकेत यहाँ उपलब्ध हैं — "
                   "किसी भी निष्कर्ष के लिए टैब 1 का AI Bias देखें।")

    with e2:
        st.markdown("#### 📢 ताज़ा NSE Corporate Announcements (सत्यापित)")
        universe, _src = SERVICE.get_universe()
        watch = [s for s in ("TCS", "RELIANCE", "HDFCBANK", "TATAMOTORS", "INFY")
                 if s in universe][:5]
        found = False
        for sym in watch:
            try:
                snaps = SERVICE.get_snapshots([sym], include_announcements=True)
                for ann in (snaps.get(sym).announcements or [])[:2]:
                    found = True
                    st.markdown(f"• **{sym}** — {ann.get('desc')} ({ann.get('date')})")
            except Exception:
                continue
        if not found:
            st.info("NSE घोषणा फ़ीड अभी उपलब्ध नहीं (खाली नहीं दिखाया गया)।")

    st.markdown("---")
    st.markdown("#### 📰 ताज़ा बाज़ार समाचार (मुक्त RSS स्रोत — कोई API key नहीं)")
    try:
        news = fetch_rss_news(limit=12)
    except Exception:
        news = []
    if news:
        n1, n2 = st.columns(2)
        half = (len(news) + 1) // 2
        for col, chunk in ((n1, news[:half]), (n2, news[half:])):
            with col:
                for item in chunk:
                    title = str(item.get("title") or "")[:150]
                    link = str(item.get("link") or "")
                    st.markdown(f"• [{title}]({link})  \n  <sub>{item.get('source', '')} · "
                                f"{item.get('published', '')[:31]}</sub>", unsafe_allow_html=True)
    else:
        st.info("अभी कोई RSS headline fetch नहीं हो पाया (नेटवर्क/स्रोत) — "
                "Source health नीचे देखें.")

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
    _show_source_health()


# ==============================================================================
# 11) TAB 5 — SETTINGS
# ==============================================================================

def _render_settings_tab(settings: dict) -> None:
    st.markdown("### ⚙️ TERMINAL & ZONE ENGINE SETTINGS")
    st.caption(f"लेयर स्थिति: {_layer_status()}")

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
            scan_limit = st.number_input("स्कैन यूनिवर्स सीमा (0 = सभी F&O स्टॉक्स)", 0, 600,
                                         int(settings.get("scan_limit", 0) or 0), 25)
            enrich_limit = st.slider("Futures/Options/Delivery enrich (टॉप N प्रतीक)", 0, 300,
                                     int(settings.get("enrich_limit", 60)), 10)
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
            try:
                SERVICE.clear_cache()
            except Exception:
                pass
            st.success("सेटिंग्स सेव हो गईं।")
            st.rerun()

    st.markdown("---")
    st.markdown("#### 🔑 Secrets (Streamlit Cloud / .env — key कभी दिखती नहीं)")
    sec = _secret_status()
    s1, s2, s3 = st.columns(3)
    with s1:
        st.write(f"**DhanHQ (live futures/OI/candles):** "
                 f"{'🟢 क्रेडेंशियल मौजूद' if sec['dhan'] else '🟡 सेट नहीं — public master + TradingView/NSE bhavcopy fallback'}")
    with s2:
        st.write(f"**Gemini (AI Pro narration):** "
                 f"{'🟢 क्रेडेंशियल मौजूद' if sec['gemini'] else '🟡 सेट नहीं — rule-based fallback चलेगा'}")
    with s3:
        st.write(f"**स्रोत:** {'Streamlit secrets' if sec['applied'] or sec['host_loader'] else 'env / .env'}"
                 f" · host loader: {'✅' if sec['host_loader'] else '—'}")
    st.caption("Streamlit Cloud में secrets को दोनों तरह रख सकते हैं — flat "
               "(`DHAN_CLIENT_ID = \"…\"`) या sectioned (`[dhan]` → `client_id`/`access_token`). "
               "ऐप दोनों पढ़ता है; यहाँ सिर्फ़ मौजूद/न-मौजूद दिखता है, key कभी नहीं।")

    st.markdown("---")
    st.markdown("#### 🔌 डेटा प्रोवाइडर स्थिति")
    caps: dict = _probe_caps()
    stt = engine_status()
    dhan_status = SERVICE.provider_status()

    def _dot(ok: bool, ok_txt: str, bad_txt: str) -> str:
        return f"🟢 {ok_txt}" if ok else f"🟡 {bad_txt}"

    p1, p2, p3, p4 = st.columns(4)
    with p1:
        st.write(f"**NSE Futures (quote-derivative):** "
                 f"{_dot(caps.get('derivative'), 'Live उपलब्ध', 'Blocked (Akamai/IP) — TradingView fallback')}")
        st.write(f"**NSE Option Chain / PCR:** "
                 f"{_dot(caps.get('option_chain'), 'Live उपलब्ध', 'Unavailable — कोई अनुमान नहीं')}")
        st.write(f"**NSE Delivery %:** "
                 f"{_dot(caps.get('equity'), 'Live उपलब्ध', 'Unavailable — कोई अनुमान नहीं')}")
    with p2:
        st.write("**TradingView screener (Futures + OI):** 🟢 Active (~15 min delayed)")
        st.write("**Yahoo Finance (OHLCV):** 🟢 Active (EMA 35/100, VWAP, Vol Spike)")
        st.write(f"**NSE FII/DII + घोषणाएँ:** "
                 f"{_dot(caps.get('announcements'), 'Live उपलब्ध', 'उपलब्ध नहीं')}")
    with p3:
        st.write(f"**Dhan (Scrip Master → यूनिवर्स):** 🟢 Public CSV"
                 f"{' + API key' if _secret_status()['dhan'] else ''}")
        st.write(f"**Dhan API quotes/candles/OI:** "
                 f"{_dot(_secret_status()['dhan'], 'Configured — live futures/OI', 'Not configured — TradingView + NSE bhavcopy fallback')}")
        st.write(f"**Gemini AI Pro:** {'🟢 Connected' if _secret_status()['gemini'] else '🟡 Rule-engine fallback'}")
    with p4:
        st.write(f"**Host repo zone_core:** {_dot(stt['host_zone_core'], 'Pine-parity engine', 'Inline fallback')}")
        st.write(f"**Host repo services:** {_dot(stt['host_zones_service'], 'zones/indicators/signals/risk', 'Inline fallback')}")
        st.write(f"**Dhan provider status:** {dhan_status.get('status', NA)}")
        st.write(f"**इंजन स्थिति:** {_layer_status()}")

    st.caption("NSE का Akamai लेयर datacenter IP (Render/Cloud) पर कुछ एंडपॉइंट ब्लॉक करता है। "
               "ऐसे में ऐप वास्तविक TradingView (delayed) Futures/OI दिखाता है और जो डेटा उपलब्ध नहीं "
               "उसे 'Unavailable' लिखता है — कोई काल्पनिक आँकड़ा नहीं।")

    st.markdown("---")
    st.markdown("#### 🔌 डेटा-स्रोत स्वास्थ्य (लाइव — यही बताता है कि क्या fetch हुआ)")
    rows = source_health_rows()
    if rows:
        try:
            st.dataframe(pd.DataFrame(rows).astype(str), hide_index=True, **df_layout(st))
        except Exception:
            st.dataframe(pd.DataFrame(rows).astype(str), hide_index=True)
    else:
        st.caption("अभी कोई स्रोत कॉल नहीं हुआ — कोई भी स्कैन चलाएँ.")
    st.caption("'🔴 उपलब्ध नहीं' का मतलब है कि उस source से कोई आँकड़ा **नहीं** लाया गया — "
               "ऐप उस जगह 'Unavailable' दिखाता है (काल्पनिक आँकड़ा कभी नहीं).")

    st.markdown("---")
    st.markdown("#### ⚡ गति (Speed) कैसे बढ़ाएँ")
    st.code("""# कोड में (Render / Streamlit Cloud env vars)
AAP_SCAN_LIMIT=40        # मास्टर स्कैन के लिए प्रतीक सीमा (0 = सभी)
AAP_ENRICH_LIMIT=40      # Futures/OI enrich सीमा (Top-N)

# ब्राउज़र से (Settings tab)
स्कैन सीमा   → ⚡ त्वरित (40) / मध्यम (100) / पूरा (सभी)
डीप स्कैन    → ON = candle + ज़ोन + EMA  |  OFF = सिर्फ़ Futures/OI quotes (2-5 सेकंड)
ज़ोन TF      → कम TF चुनें (केवल Daily / केवल Intraday) = बहुत तेज़""", language="text")

    st.markdown("---")
    st.markdown("#### 📦 Repo संरचना (single-file edition)")
    st.code("""aap.py                 ← पूरा ऐप इसी एक फ़ाइल में (repo root पर)
app.py                 ← optional shim: runpy.run_path("aap.py", run_name="__main__")
terminal/              ← आपका मौजूदा repo (zone_core, services, providers) — उपयोग होता है
ui/                    ← आपका मौजूदा repo (runtime_config → secrets)
requirements.txt
.env (केवल local) / Render env vars""", language="text")
    st.caption("aap.py आपके repo के modules (terminal.*, ui.*) को पहले इस्तेमाल करता है; "
               "वे न हों तो उसी फ़ाइल का inline fallback चलता है — कुछ भी overwrite नहीं होता।")


# ==============================================================================
# 12) MAIN
# ==============================================================================

TAB_NAMES = ["NSE ALL FUTURE STOCKS", "Zone", "Global Zone", "News & Events", "Settings"]


def _render_tab_bar() -> int:
    """Lazy tab bar — केवल चुनी हुई टैब render होती है (बहुत तेज़)।"""
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
