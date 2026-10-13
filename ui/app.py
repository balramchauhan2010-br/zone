from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import quote as url_quote
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from ui.runtime_config import apply_streamlit_secrets
apply_streamlit_secrets()

from terminal.config import DEFAULT_ANALYSIS_SETTINGS
from terminal.timeframes import TIMEFRAMES, is_supported_timeframe
from ui import local_backend

IST = ZoneInfo("Asia/Kolkata")

st.set_page_config(
    page_title="AI Pro Institutional Trading Terminal",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    :root { color-scheme: dark; }
    .stApp { background: #07111f; color: #e7eef8; }
    [data-testid="stHeader"] { background: rgba(7,17,31,.92); }
    [data-testid="stSidebar"] { background: #0a1728; }
    h1, h2, h3 { color: #f3f7ff; letter-spacing: .01em; }
    h1 { font-size: 1.65rem !important; margin-bottom: .15rem !important; }
    h2 { font-size: 1.16rem !important; margin-top: .3rem !important; }
    h3 { font-size: 1rem !important; }
    div[data-testid="stDataFrame"] { border: 1px solid #1a3858; border-radius: 6px; }
    .stButton button { border: 1px solid #315273; background: #112744; color: #edf5ff; border-radius: 5px; }
    .stButton button:hover { border-color: #55a6ec; color: white; }
    </style>
    """,
    unsafe_allow_html=True,
)

if "analysis_settings" not in st.session_state:
    st.session_state.analysis_settings = dict(DEFAULT_ANALYSIS_SETTINGS)
if "scanner_payload" not in st.session_state:
    st.session_state.scanner_payload = None
if "zone_job_id" not in st.session_state:
    st.session_state.zone_job_id = ""
if "global_provider_mappings" not in st.session_state:
    st.session_state.global_provider_mappings = {}

SCANNER_COLUMNS = [
    "Symbol", "Company Name", "Instrument Type", "Contract Expiry", "Contract Status", "F&O Ban Status",
    "Market Data Mode", "Data Source", "Spot Data Status", "Spot Bar Date",
    "Spot LTP", "Spot Change", "Spot Change %", "Daily Spot Volume", "Futures LTP", "Futures Change", "Futures Change %",
    "Futures Basis", "Basis Change", "Selected-Timeframe Volume", "Average Volume", "Volume Spike Ratio", "Volume Signal",
    "Latest Verified Delivery %", "Previous Session Delivery %", "Delivery Difference (pp)", "Delivery Trend",
    "Delivery/Price Divergence", "Delivery Data Date", "Futures OI", "Futures OI Change", "OI Change %",
    "PCR", "CE Total OI", "PE Total OI", "CE OI Change", "PE OI Change", "Buildup Classification",
    "EMA 35 Signal", "EMA 100 Signal", "VWAP Signal", "Market Structure", "Zone Signal", "Sector Relative Strength",
    "Bullish Score", "Bearish Score", "Data Quality Score", "AI Hypothesis", "Final AI Bias", "Signal Blocker", "Risk Status", "Setup Invalidation",
    "Options/OI Details", "News Details", "Last Updated",
]

DEFAULT_VISIBLE = [
    "Symbol", "AI Hypothesis", "Spot LTP", "Spot Change %", "Daily Spot Volume",
    "Futures LTP", "Futures Change %", "Selected-Timeframe Volume", "Volume Spike Ratio",
    "Futures OI", "Futures OI Change", "Latest Verified Delivery %", "PCR",
    "CE Total OI", "PE Total OI", "Buildup Classification", "EMA 35 Signal", "EMA 100 Signal",
    "VWAP Signal", "Zone Signal", "Final AI Bias", "Market Data Mode", "Data Source", "Signal Blocker", "Risk Status",
]

def _final_bias(item: dict) -> str:
    ai_bias = str(item.get("final_ai_bias") or "").upper()
    scored_bias = str(item.get("final_bias") or "").upper()
    if ai_bias not in {"", "NO TRADE", "NONE"}:
        return ai_bias
    if scored_bias not in {"", "NO TRADE", "NONE"}:
        return scored_bias
    return "NO TRADE"

def _directional_hypothesis(item: dict) -> str:
    final = _final_bias(item)
    if final in {"STRONG BUY", "STRONG SELL", "BUY", "SELL", "HOLD"}:
        return final + " · rule-engine"
    if str(item.get("market_data_mode", "")).upper() == "RESEARCH_ONLY":
        return "CASH research only"
    if item.get("futures_ltp") is None or item.get("futures_change_pct") is None:
        return "WAIT · quote missing"
    return "WAIT · checks pending"

def _linkify_symbols(frame: pd.DataFrame) -> pd.DataFrame:
    display = frame.copy()
    if "Symbol" in display and "_Chart URL" in display:
        display["Symbol"] = display["_Chart URL"].fillna("").astype(str)
        display = display.drop(columns=["_Chart URL"])
    return display

def _scanner_frame(rows: list[dict]) -> pd.DataFrame:
    output = []
    for item in rows:
        symbol = str(item.get("symbol", "—"))
        chart_url = item.get("tradingview_url") or f"https://in.tradingview.com/chart/?symbol={url_quote('NSE:' + symbol, safe='')}"
        output.append({
            "Symbol": symbol,
            "_Chart URL": chart_url,
            "AI Hypothesis": item.get("ai_hypothesis") or _directional_hypothesis(item),
            "Signal Blocker": item.get("signal_reason") or item.get("reason") or "—",
            "Company Name": item.get("company_name", "—"),
            "Instrument Type": item.get("instrument_type", "FUTSTK"),
            "Contract Expiry": item.get("expiry", "—"),
            "Contract Status": item.get("contract_status", "UNKNOWN"),
            "F&O Ban Status": item.get("fno_ban_status", "UNAVAILABLE"),
            "Market Data Mode": item.get("market_data_mode", "BROKER_MARKET_DATA"),
            "Data Source": item.get("spot_data_source", item.get("source", "—")),
            "Spot Data Status": item.get("spot_data_status", "UNAVAILABLE"),
            "Spot Bar Date": item.get("spot_bar_date", "—"),
            "Spot LTP": item.get("spot_ltp"),
            "Spot Change": item.get("spot_change"),
            "Spot Change %": item.get("spot_change_pct"),
            "Daily Spot Volume": item.get("daily_spot_volume"),
            "Futures LTP": item.get("futures_ltp"),
            "Futures Change": item.get("futures_change"),
            "Futures Change %": item.get("futures_change_pct"),
            "Futures Basis": item.get("futures_basis"),
            "Basis Change": item.get("basis_change"),
            "Selected-Timeframe Volume": item.get("selected_timeframe_volume"),
            "Average Volume": item.get("average_volume"),
            "Volume Spike Ratio": item.get("volume_spike_ratio"),
            "Volume Signal": item.get("volume_signal", "NOT_CALCULATED"),
            "Latest Verified Delivery %": "UNAVAILABLE",
            "Previous Session Delivery %": "UNAVAILABLE",
            "Delivery Difference (pp)": "UNAVAILABLE",
            "Delivery Trend": "UNAVAILABLE",
            "Delivery/Price Divergence": "UNAVAILABLE",
            "Delivery Data Date": "UNAVAILABLE",
            "Futures OI": item.get("futures_oi"),
            "Futures OI Change": item.get("futures_oi_change"),
            "OI Change %": item.get("futures_oi_change_pct"),
            "PCR": "UNAVAILABLE",
            "CE Total OI": "UNAVAILABLE",
            "PE Total OI": "UNAVAILABLE",
            "CE OI Change": "UNAVAILABLE",
            "PE OI Change": "UNAVAILABLE",
            "Buildup Classification": item.get("oi_observation_status", "UNKNOWN"),
            "EMA 35 Signal": "?",
            "EMA 100 Signal": "?",
            "VWAP Signal": "?",
            "Market Structure": "NOT_CALCULATED",
            "Zone Signal": "NOT_SCANNED",
            "Sector Relative Strength": "UNAVAILABLE",
            "Bullish Score": item.get("bullish_score"),
            "Bearish Score": item.get("bearish_score"),
            "Data Quality Score": item.get("data_quality_score"),
            "Final AI Bias": _final_bias(item),
            "Risk Status": item.get("risk_status", "UNKNOWN"),
            "Setup Invalidation": "—",
            "Options/OI Details": "Load on demand",
            "News Details": "Search under News & Events",
            "Last Updated": item.get("received_at", "—"),
        })
    return pd.DataFrame(output, columns=[*SCANNER_COLUMNS, "_Chart URL"])

def _load_scanner(contract: str = "front", force: bool = False) -> dict:
    loaded = st.session_state.scanner_payload
    if force or loaded is None:
        with st.spinner("Market data लोड हो रहा है..."):
            payload = local_backend.get("/api/futures", params={"contract": contract, "refresh": force}, timeout=60)
        st.session_state.scanner_payload = payload
        return payload
    return loaded

def _render_nse_tab(settings: dict) -> None:
    c_btn, _ = st.columns([1, 4])
    with c_btn:
        if st.button("↻ Refresh scanner", type="primary"):
            st.session_state.scanner_payload = None
            st.rerun()

    payload = _load_scanner("front")
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    frame = _scanner_frame(rows)

    qualified_buys = [row for row in rows if _final_bias(row) == "STRONG BUY"]
    qualified_sells = [row for row in rows if _final_bias(row) == "STRONG SELL"]
    buy_cols = ["Symbol", "Futures LTP", "Futures Change %", "Volume Spike Ratio", "AI Hypothesis", "Final AI Bias"]
    buy_frame = _scanner_frame(qualified_buys).head(5)
    sell_frame = _scanner_frame(qualified_sells).head(5)

    buy_col, sell_col = st.columns(2)
    with buy_col:
        st.markdown("**TOP 5 STRONG BUY**")
        if qualified_buys:
            st.dataframe(_linkify_symbols(buy_frame[buy_cols + ["_Chart URL"]]),
                         column_config={"Symbol": st.column_config.LinkColumn("Symbol", display_text=r".*symbol=NSE%3A([^&#]+).*")},
                         width="stretch", hide_index=True, height=190)
        else:
            st.info("कोई Strong Buy परिणाम उपलब्ध नहीं।")
    with sell_col:
        st.markdown("**TOP 5 STRONG SELL**")
        if qualified_sells:
            st.dataframe(_linkify_symbols(sell_frame[buy_cols + ["_Chart URL"]]),
                         column_config={"Symbol": st.column_config.LinkColumn("Symbol", display_text=r".*symbol=NSE%3A([^&#]+).*")},
                         width="stretch", hide_index=True, height=190)
        else:
            st.info("कोई Strong Sell परिणाम उपलब्ध नहीं।")

    st.markdown("### NSE ALL FUTURES — LIVE SCAN")
    cols_to_render = [col for col in DEFAULT_VISIBLE if col in SCANNER_COLUMNS]
    if "Symbol" in cols_to_render:
        cols_to_render.append("_Chart URL")
    
    display = _linkify_symbols(frame[cols_to_render].copy())
    column_config = {
        "Symbol": st.column_config.LinkColumn("Symbol", display_text=r".*symbol=NSE%3A([^&#]+).*"),
        "Futures Change %": st.column_config.NumberColumn("Futures Change %", format="%.2f%%"),
        "Spot Change %": st.column_config.NumberColumn("Spot Change %", format="%.2f%%"),
        "Daily Spot Volume": st.column_config.NumberColumn("Daily Spot Volume", format="localized"),
        "Volume Spike Ratio": st.column_config.NumberColumn("Volume Spike Ratio", format="%.2fx"),
    }
    st.dataframe(display, column_config=column_config, width="stretch", hide_index=True, height=520)

def main() -> None:
    st.markdown("# AI PRO INSTITUTIONAL TRADING TERMINAL")
    tabs = st.tabs(["NSE ALL FUTURE STOCKS", "Zone", "Global Zone", "News & Events", "Settings"])
    settings = st.session_state.analysis_settings
    with tabs[0]:
        _render_nse_tab(settings)
    with tabs[1]:
        st.info("Zone Scanner universe loaded.")
    with tabs[2]:
        st.info("Global Zone loaded.")
    with tabs[3]:
        st.info("News & Events loaded.")
    with tabs[4]:
        st.info("Settings tab.")

if __name__ == "__main__":
    main()
