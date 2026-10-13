from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from ui.runtime_config import apply_streamlit_secrets

# Streamlit Cloud secrets are applied before provider modules snapshot env config.
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
    p, label, span, div { line-height: 1.35; }
    [data-testid="stMetric"] { background: #0b1a2d; border: 1px solid #18314d; padding: .55rem .7rem; border-radius: 7px; }
    [data-testid="stMetricLabel"] { color: #9bb0c9 !important; font-size: .77rem !important; }
    [data-testid="stMetricValue"] { color: #f1f6ff !important; font-size: 1.12rem !important; }
    div[data-testid="stDataFrame"] { border: 1px solid #1a3858; border-radius: 6px; }
    div[data-testid="stExpander"] { border: 1px solid #1a3858; border-radius: 6px; background: #091729; }
    .terminal-subtitle { color: #9db2cc; margin-top: 0; font-size: .87rem; }
    .terminal-pill { display: inline-block; padding: 3px 8px; margin: 2px 5px 2px 0; border: 1px solid #28496b; border-radius: 12px; background: #0b1a2d; color: #bad0eb; font-size: .72rem; }
    .terminal-note { color: #9cb0c8; font-size: .82rem; }
    .small-muted { color: #90a4bb; font-size: .76rem; }
    .stButton button { border: 1px solid #315273; background: #112744; color: #edf5ff; border-radius: 5px; }
    .stButton button:hover { border-color: #55a6ec; color: white; }
    [data-baseweb="tab-list"] { gap: 3px; border-bottom: 1px solid #1c3857; }
    [data-baseweb="tab"] { background: #0a192b; border-radius: 5px 5px 0 0; color: #b8c9dc; padding-left: 14px; padding-right: 14px; }
    [aria-selected="true"][data-baseweb="tab"] { background: #102642; color: #ffffff; border-bottom: 2px solid #39a7ff; }
    hr { border-color: #1b344f; }
    </style>
    """,
    unsafe_allow_html=True,
)

if "analysis_settings" not in st.session_state:
    st.session_state.analysis_settings = dict(DEFAULT_ANALYSIS_SETTINGS)
if "visible_columns" not in st.session_state:
    st.session_state.visible_columns = []
if "scanner_payload" not in st.session_state:
    st.session_state.scanner_payload = None
if "zone_job_id" not in st.session_state:
    st.session_state.zone_job_id = ""
if "watchlist_symbols" not in st.session_state:
    st.session_state.watchlist_symbols = []
if "global_provider_mappings" not in st.session_state:
    st.session_state.global_provider_mappings = {}


SCANNER_COLUMNS = [
    "Rank", "Symbol", "Company Name", "Instrument Type", "Contract Expiry", "Contract Status", "F&O Ban Status",
    "Market Data Mode", "Data Source", "Spot Data Status", "Spot Bar Date",
    "Spot LTP", "Spot Change", "Spot Change %", "Daily Spot Volume", "Futures LTP", "Futures Change", "Futures Change %",
    "Futures Basis", "Basis Change", "Selected-Timeframe Volume", "Average Volume", "Volume Spike Ratio", "Volume Signal",
    "Latest Verified Delivery %", "Previous Session Delivery %", "Delivery Difference (pp)", "Delivery Trend",
    "Delivery/Price Divergence", "Delivery Data Date", "Futures OI", "Futures OI Change", "OI Change %",
    "PCR", "CE Total OI", "PE Total OI", "CE OI Change", "PE OI Change", "Buildup Classification",
    "EMA 35 Signal", "EMA 100 Signal", "VWAP Signal", "Market Structure", "Zone Signal", "Sector Relative Strength",
    "Bullish Score", "Bearish Score", "Data Quality Score", "Final AI Bias", "Risk Status", "Setup Invalidation",
    "TradingView Chart", "Options/OI Details", "News Details", "Last Updated",
]
DEFAULT_VISIBLE = [
    "Rank", "Symbol", "Spot LTP", "Spot Change %", "Daily Spot Volume",
    "Futures LTP", "Futures Change %", "Selected-Timeframe Volume", "Volume Spike Ratio",
    "Futures OI", "Futures OI Change", "Latest Verified Delivery %", "PCR",
    "CE Total OI", "PE Total OI", "Buildup Classification", "EMA 35 Signal", "EMA 100 Signal",
    "VWAP Signal", "Zone Signal", "Final AI Bias", "Market Data Mode", "Data Source", "Risk Status",
    "TradingView Chart",
]


def _timeframe_options(settings: dict | None = None) -> list[str]:
    custom = (settings or {}).get("custom_timeframes", [])
    valid_custom = [tf for tf in custom if isinstance(tf, str) and is_supported_timeframe(tf) and tf not in TIMEFRAMES]
    return list(dict.fromkeys([*TIMEFRAMES, *valid_custom]))


def _fmt(value, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _payload_rows(payload: dict) -> list[dict]:
    data = payload.get("data")
    return data if isinstance(data, list) else []


def _status_text(payload: dict) -> str:
    return str(payload.get("status", "UNKNOWN")).upper()


def _status_message(payload: dict) -> str:
    return str(payload.get("reason") or payload.get("note") or "")


def _global_catalog_payload() -> tuple[dict, list[dict]]:
    payload = local_backend.get("/api/global/markets")
    rows = [dict(row) for row in _payload_rows(payload)]
    mappings = st.session_state.get("global_provider_mappings", {})
    if isinstance(mappings, dict):
        for row in rows:
            mapped = str(mappings.get(row.get("id", ""), "")).strip()
            if mapped:
                row["provider_symbol"] = mapped
                if row.get("status") in {"UNMAPPED", "MAPPING_REVIEW"}:
                    row["status"] = "USER_MAPPED_UNVERIFIED"
    return payload, rows


def _scanner_frame(rows: list[dict]) -> pd.DataFrame:
    output = []
    for item in rows:
        rank = item.get("rank")
        output.append({
            "Rank": rank if rank is not None else "—",
            "Symbol": item.get("symbol", "—"),
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
            "Final AI Bias": item.get("final_ai_bias", "NO TRADE"),
            "Risk Status": item.get("risk_status", "UNKNOWN"),
            "Setup Invalidation": "—",
            "TradingView Chart": item.get("tradingview_url", ""),
            "Options/OI Details": "Load on demand",
            "News Details": "UNAVAILABLE",
            "Last Updated": item.get("received_at", "—"),
        })
    return pd.DataFrame(output, columns=SCANNER_COLUMNS)


def _no_data_table(title: str, reason: str) -> None:
    st.info(f"**{title}:** कोई योग्य/सत्यापित परिणाम उपलब्ध नहीं। {reason}")
    st.caption("Data उपलब्ध न होने पर इस table में sample या अनुमानित rows नहीं दिखाई जातीं।")


def _data_status(payload: dict, label: str = "Data status") -> None:
    status = _status_text(payload)
    reason = _status_message(payload)
    if status in {"AVAILABLE", "CONNECTED", "OK", "COMPLETE"}:
        st.success(f"{label}: {status}" + (f" · {reason}" if reason else ""))
    elif status in {"PARTIAL", "QUEUED", "RUNNING", "NO_QUALIFIED_SETUPS"}:
        st.warning(f"{label}: {status}" + (f" · {reason}" if reason else ""))
    else:
        st.warning(f"{label}: {status}" + (f" · {reason}" if reason else ""))


def _load_scanner(contract: str, force: bool = False) -> dict:
    loaded = st.session_state.scanner_payload
    if force or loaded is None or loaded.get("_contract") != contract:
        with st.spinner("Scanner market data load हो रहा है…"):
            payload = local_backend.get("/api/futures", params={"contract": contract, "refresh": force}, timeout=60)
        payload["_contract"] = contract
        st.session_state.scanner_payload = payload
        return payload
    return loaded


def _details_panel(symbol: str, settings: dict) -> None:
    st.markdown(f"#### {symbol} — verified detail requests")
    timeframe_options = _timeframe_options(settings)
    indicator_timeframes = settings.get("indicator_timeframes", [settings.get("zone_timeframe", "15m")])
    indicator_timeframes = [tf for tf in timeframe_options if tf in set(indicator_timeframes)] or [settings.get("zone_timeframe", "15m")]
    vwap_timeframe = settings.get("vwap_timeframe", "15m")
    request_timeframes = list(dict.fromkeys(indicator_timeframes + [vwap_timeframe]))
    st.caption(f"Volume: {settings.get('volume_timeframe', '15m')} · Indicators: {', '.join(indicator_timeframes)} · VWAP: {vwap_timeframe}")
    if not st.button("Load candles, volume, indicators and quote", key=f"detail_{symbol}"):
        st.caption("Detail endpoints are called only after you request them; historical-data quota is respected.")
        return
    params = {"timeframe": settings.get("volume_timeframe", "15m"),
              "lookback": settings.get("volume_lookback", 30),
              "threshold": settings.get("volume_spike_threshold", 2.1),
              "minimum_volume": settings.get("minimum_volume", 0),
              "completed_only": settings.get("completed_candles_only", True)}
    indicator_payloads: dict[str, dict] = {}
    with st.spinner(f"{symbol} के verified Dhan details प्राप्त हो रहे हैं…"):
        quote = local_backend.get(f"/api/futures/{symbol}")
        volume = local_backend.get(f"/api/volume/{symbol}", params=params, timeout=90)
        for timeframe in request_timeframes:
            indicator_payloads[timeframe] = local_backend.get(f"/api/indicators/{symbol}", params={
                "timeframe": timeframe,
                "ema_fast": settings.get("ema_fast", 35),
                "ema_slow": settings.get("ema_slow", 100),
            }, timeout=90)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Quote / Open Interest**")
        st.json(quote.get("data") or {"status": quote.get("status"), "reason": _status_message(quote)})
    with c2:
        st.markdown("**Volume expansion**")
        st.json(volume.get("data") or {"status": volume.get("status"), "reason": _status_message(volume)})
    with c3:
        st.markdown("**EMA / VWAP / ATR**")
        indicator_rows = []
        for timeframe, result in indicator_payloads.items():
            item = result.get("data") or {}
            indicator_rows.append({
                "Timeframe": timeframe,
                "Status": result.get("status"),
                "Last close": item.get("last_close"),
                "EMA 35": item.get("ema_fast"),
                "EMA 35 signal": item.get("ema_fast_signal"),
                "EMA 100": item.get("ema_slow"),
                "EMA 100 signal": item.get("ema_slow_signal"),
                "EMA cross": item.get("ema_cross_signal"),
                "VWAP": item.get("vwap"),
                "VWAP signal": item.get("vwap_signal"),
                "ATR": item.get("atr"),
                "Structure": item.get("market_structure"),
                "Candle time": item.get("last_completed_or_available_candle"),
                "Reason": result.get("reason"),
            })
        st.dataframe(pd.DataFrame(indicator_rows), width="stretch", hide_index=True)
    gemini_status = ((st.session_state.get("health_payload", {}).get("data") or {}).get("gemini") or {})
    if gemini_status.get("credentials_configured"):
        if st.button("Gemini: explain these supplied facts", key=f"gemini_{symbol}"):
            evidence = {"symbol": symbol, "quote": quote.get("data"), "volume": volume.get("data"),
                        "indicators": {tf: item.get("data") for tf, item in indicator_payloads.items()}}
            explanation = local_backend.post("/api/ai/explain", {"evidence": evidence}, timeout=45)
            _data_status(explanation, "Gemini explanation")
            if explanation.get("data"):
                st.write(explanation["data"].get("summary", ""))
    else:
        st.caption("Gemini explanation is optional and currently not configured; deterministic rules do not depend on Gemini.")


def _render_nse_tab(settings: dict) -> None:
    c1, c2, c3 = st.columns([1, 1, 1.2])
    with c1:
        contract = st.selectbox(
            "Futures contract", ["front", "next", "all"],
            index=["front", "next", "all"].index(settings.get("futures_contract", "front")),
        )
    with c2:
        page_size = st.selectbox(
            "Rows per page", [20, 50, 100],
            index=[20, 50, 100].index(settings.get("page_size", 20)),
        )
    with c3:
        refresh = st.button("↻ Refresh scanner", type="primary")

    payload = _load_scanner(contract, force=refresh)
    rows = _payload_rows(payload)
    frame = _scanner_frame(rows)
    universe_symbols = sorted({str(row.get("symbol")) for row in rows if row.get("symbol")})

    buy_cols = ["Rank", "Symbol", "Futures LTP", "Futures Change %", "Volume Spike Ratio", "Final AI Bias"]
    sell_cols = buy_cols.copy()
    qualified_buys = [row for row in rows if row.get("final_ai_bias") in {"STRONG BUY", "BUY"}]
    qualified_sells = [row for row in rows if row.get("final_ai_bias") in {"STRONG SELL", "SELL"}]
    buy_frame = _scanner_frame(qualified_buys).head(5)[buy_cols] if qualified_buys else pd.DataFrame(columns=buy_cols)
    sell_frame = _scanner_frame(qualified_sells).head(5)[sell_cols] if qualified_sells else pd.DataFrame(columns=sell_cols)
    buy_col, sell_col = st.columns(2)
    with buy_col:
        st.markdown("**TOP 5 STRONG BUY**")
        st.dataframe(buy_frame, width="stretch", hide_index=True, height=190)
    with sell_col:
        st.markdown("**TOP 5 STRONG SELL**")
        st.dataframe(sell_frame, width="stretch", hide_index=True, height=190)

    market_mode = str(payload.get("market_data_mode", ""))
    if market_mode == "RESEARCH_ONLY":
        st.markdown("### NSE ALL FUTURES — SPOT RESEARCH")
    else:
        st.markdown("### NSE ALL FUTURES — LIVE SCAN")
    search_col, filter_col = st.columns([2, 1])
    with search_col:
        search = st.text_input("Search", key="scanner_search", placeholder="Symbol / company")
    with filter_col:
        signal_filter = st.selectbox("Signal", ["All", "Buy", "Sell", "NO TRADE"], index=0)

    if search:
        mask = frame["Symbol"].astype(str).str.contains(search, case=False, na=False) | frame["Company Name"].astype(str).str.contains(search, case=False, na=False)
        frame = frame[mask]
    if signal_filter == "Buy":
        frame = frame[frame["Final AI Bias"].astype(str).isin(["BUY", "STRONG BUY"])]
    elif signal_filter == "Sell":
        frame = frame[frame["Final AI Bias"].astype(str).isin(["SELL", "STRONG SELL"])]
    elif signal_filter == "NO TRADE":
        frame = frame[frame["Final AI Bias"].astype(str).eq("NO TRADE")]

    all_symbols = frame["Symbol"].dropna().astype(str).tolist()
    watchlist = st.multiselect(
        "Watchlist", universe_symbols,
        default=[symbol for symbol in st.session_state.watchlist_symbols if symbol in universe_symbols],
        key="session_watchlist_control",
    )
    st.session_state.watchlist_symbols = watchlist
    if watchlist:
        frame = frame[frame["Symbol"].astype(str).isin(watchlist)]
        all_symbols = frame["Symbol"].dropna().astype(str).tolist()

    default_cols = st.session_state.visible_columns or [col for col in DEFAULT_VISIBLE if col in SCANNER_COLUMNS]
    chosen_cols = st.multiselect(
        "Columns", SCANNER_COLUMNS,
        default=[col for col in default_cols if col in SCANNER_COLUMNS],
        key="scanner_visible_columns",
    )
    if chosen_cols:
        st.session_state.visible_columns = chosen_cols
    else:
        chosen_cols = ["Rank", "Symbol", "Futures LTP", "Final AI Bias"]

    pages = max(1, (len(frame) + page_size - 1) // page_size)
    page = st.selectbox("Page", list(range(1, pages + 1)), key="scanner_page")
    start = (page - 1) * page_size
    page_frame = frame.iloc[start:start + page_size]
    display = page_frame[chosen_cols].copy()
    column_config = {
        "TradingView Chart": st.column_config.LinkColumn("TradingView Chart", display_text="Open"),
        "Futures Change %": st.column_config.NumberColumn("Futures Change %", format="%.2f%%"),
        "Spot Change %": st.column_config.NumberColumn("Spot Change %", format="%.2f%%"),
        "Daily Spot Volume": st.column_config.NumberColumn("Daily Spot Volume", format="localized"),
        "Volume Spike Ratio": st.column_config.NumberColumn("Volume Spike Ratio", format="%.2fx"),
    }
    st.dataframe(display, column_config=column_config, width="stretch", hide_index=True, height=480)

    with st.expander("Selected symbol details"):
        selected = st.selectbox("Symbol", all_symbols, key="details_symbol") if all_symbols else None
        if selected:
            _details_panel(selected, settings)

def _render_zone_tab(settings: dict) -> None:
    st.subheader("Zone Scanner — NSE Futures universe")
    st.markdown('<p class="terminal-subtitle">पहले tab के dynamic NSE FUTSTK universe पर user-supplied zone_core.py rules</p>', unsafe_allow_html=True)
    universe_payload = local_backend.get("/api/instruments", params={"contract": "front"}, timeout=45)
    universe = _payload_rows(universe_payload)
    if not universe:
        _data_status(universe_payload, "Instrument universe")
        st.info("Dhan instrument master उपलब्ध होने पर यहां selectable universe दिखेगा।")
        return
    symbols = [row.get("symbol") for row in universe if row.get("symbol")]
    timeframe_options = _timeframe_options(settings)
    c1, c2, c3 = st.columns([1.5, 1, 1])
    with c1:
        scan_scope = st.radio("Scan scope", ["Selected symbols", "Full NSE FUTSTK universe"], horizontal=True)
    with c2:
        timeframe = st.selectbox("Zone timeframe", timeframe_options, index=timeframe_options.index(settings.get("zone_timeframe", "15m")))
    with c3:
        contract = st.selectbox("Contract", ["front", "next"], index=0, help="Current/next valid contract from Dhan's active instrument master.")
    if scan_scope == "Selected symbols":
        selected_symbols = st.multiselect("Symbols", symbols, default=symbols[:min(5, len(symbols))], key="zone_symbols")
    else:
        selected_symbols = symbols
        st.warning(f"Full-universe scan में {len(symbols)} instrument हैं। Provider limits के कारण यह कई मिनट ले सकता है; job background में चलता है।")

    a, b, c, d = st.columns(4)
    with a:
        high_buffer = st.number_input("EOD High buffer (%)", min_value=0.0, max_value=100.0,
                                      value=float(settings.get("eod_high_buffer_pct", 10.0)), step=1.0)
    with b:
        low_buffer = st.number_input("EOD Low buffer (%)", min_value=0.0, max_value=100.0,
                                     value=float(settings.get("eod_low_buffer_pct", 10.0)), step=1.0)
    with c:
        history_days = st.selectbox("Intraday history (days)", [5, 15, 30, 60, 89], index=2)
    with d:
        include_broken = st.checkbox("Include broken zones", value=False)
    use_eod = st.checkbox("Use EOD-range filter", value=bool(settings.get("use_eod_range", True)),
                          help="Proximal line must lie between session low × (1−low buffer) and session high × (1+high buffer), following the supplied core.")
    scan_clicked = st.button("Start zone scan", type="primary", disabled=(not selected_symbols))
    if scan_clicked:
        body = {
            "scope": "universe" if scan_scope == "Full NSE FUTSTK universe" else "selected",
            "symbols": selected_symbols if scan_scope == "Selected symbols" else [],
            "contract": contract,
            "timeframe": timeframe,
            "history_days": history_days,
            "eod_high_buffer_pct": high_buffer,
            "eod_low_buffer_pct": low_buffer,
            "use_eod_range": use_eod,
            "include_broken": include_broken,
            "account_capital": settings.get("account_capital", 25000.0),
            "risk_pct": settings.get("risk_pct", 0.5),
            "target_rr": settings.get("target_rr", 5.0),
        }
        result = local_backend.post("/api/zones/scan", body, timeout=30)
        job_data = result.get("data") or {}
        if result.get("status") == "QUEUED" and job_data.get("job_id"):
            st.session_state.zone_job_id = job_data["job_id"]
            st.success(f"Zone scan job queued: {job_data['job_id']}")
        else:
            _data_status(result, "Zone scan request")

    job_id = st.session_state.zone_job_id
    if job_id:
        c_refresh, c_info = st.columns([1, 3])
        with c_refresh:
            check_job = st.button("Check scan progress", key="check_zone_job")
        if check_job:
            job_payload = local_backend.get(f"/api/zones/jobs/{job_id}", timeout=30)
            job = job_payload.get("data") or {}
            progress = job.get("progress")
            if isinstance(progress, (int, float)):
                st.progress(min(max(progress / 100.0, 0.0), 1.0), text=f"{job.get('status')} · {progress:.1f}% · {job.get('completed', 0)}/{job.get('total', 0)}")
            else:
                _data_status(job_payload, "Zone scan job")
            st.caption(job.get("reason") or job.get("rate_limit_note") or "")
        if st.button("Load latest results", key="load_zone_results"):
            result_payload = local_backend.get("/api/zones", params={"job_id": job_id}, timeout=30)
            results = _payload_rows(result_payload)
            result_rows = []
            for item in results:
                for zone in item.get("zones", []):
                    result_rows.append(zone)
            if result_rows:
                st.dataframe(pd.DataFrame(result_rows), width="stretch", hide_index=True, height=460)
            else:
                _data_status(result_payload, "Zone results")
                st.caption("No active zones may simply mean that the supplied core found none under the selected settings; this is not replaced with sample zones.")

    st.info("EOD buffers default to +10% / −10%, editable above and in Settings. This adapter preserves the pasted core. The core's full-session high/low grouping can introduce same-day look-ahead in historical intraday replay; its warning is returned with scan results.")
    if st.button("Open current/latest stored zone results", key="load_latest_zone_results"):
        latest = local_backend.get("/api/zones", timeout=30)
        result_rows = [zone for item in _payload_rows(latest) for zone in item.get("zones", [])]
        if result_rows:
            st.dataframe(pd.DataFrame(result_rows), width="stretch", hide_index=True)
        else:
            _data_status(latest, "Stored zone results")


def _render_global_tab() -> None:
    st.subheader("Global Zone")
    st.markdown('<p class="terminal-subtitle">Verified global feed मिलने पर live scan; अभी user-supplied OHLCV CSV पर वही zone_core.py चल सकता है</p>', unsafe_allow_html=True)
    payload, rows = _global_catalog_payload()
    _data_status(payload, "Global market feed")
    if rows:
        df = pd.DataFrame(rows)
        cols = [c for c in ["id", "display_name", "asset_class", "user_input", "provider_symbol", "status", "note"] if c in df.columns]
        st.dataframe(df[cols], width="stretch", hide_index=True, height=350)
    st.warning("Live global quotes/candles के लिए अभी verified, licensed provider और mappings configure नहीं हैं। Uploaded CSV को UNVERIFIED user data के रूप में label किया जाएगा—live feed नहीं।")
    c1, c2 = st.columns([1, 1])
    with c1:
        global_symbol = st.selectbox("Global instrument", [row.get("id") for row in rows] or ["—"])
    with c2:
        timeframe_options = _timeframe_options(st.session_state.analysis_settings)
        timeframe = st.selectbox("Global zone timeframe", timeframe_options, index=timeframe_options.index(st.session_state.analysis_settings.get("zone_timeframe", "15m")), key="global_tf")
    if global_symbol != "—" and st.button("Check live Global Zone data status"):
        result = local_backend.get("/api/global/zones", params={"symbol": global_symbol, "timeframe": timeframe})
        _data_status(result, "Global Zone")
        if result.get("reason"):
            st.caption(result["reason"])

    st.markdown("### Scan a real OHLCV CSV you provide")
    st.download_button("Download CSV header template",
                       data="timestamp,open,high,low,close,volume\n",
                       file_name="global-ohlcv-template.csv", mime="text/csv")
    uploaded = st.file_uploader("Upload historical OHLCV CSV (max 5 MB)", type=["csv"], key="global_ohlcv_upload")
    if uploaded and global_symbol != "—" and st.button("Run supplied-data Zone scan", key="run_global_csv_scan"):
        settings = st.session_state.analysis_settings
        params = {
            "symbol": global_symbol,
            "timeframe": timeframe,
            "eod_high_buffer_pct": settings.get("eod_high_buffer_pct", 10.0),
            "eod_low_buffer_pct": settings.get("eod_low_buffer_pct", 10.0),
            "use_eod_range": settings.get("use_eod_range", True),
            "account_capital": settings.get("account_capital", 25000.0),
            "risk_pct": settings.get("risk_pct", 0.5),
            "target_rr": settings.get("target_rr", 5.0),
        }
        result = local_backend.upload_csv("/api/global/zones/upload", uploaded.name, uploaded.getvalue(), params=params)
        _data_status(result, "Uploaded-data zone scan")
        scan = result.get("data") or {}
        st.caption("Source is the file you uploaded; values and timestamps are not independently verified or marked live. Intraday resampling is anchored at 09:15 IST; align CSV timestamps to the intended market session before scanning.")
        zone_rows = scan.get("zones", []) if isinstance(scan, dict) else []
        if zone_rows:
            st.dataframe(pd.DataFrame(zone_rows), width="stretch", hide_index=True, height=420)
        elif scan:
            st.info("CSV parsed successfully, but the supplied zone_core found no active zone under the selected settings.")
        elif result.get("reason"):
            st.caption(result["reason"])


def _render_news_tab() -> None:
    st.subheader("News & Events")
    news_col, event_col = st.columns(2)
    with news_col:
        st.markdown("### News / Disclosures")
        news = local_backend.get("/api/news")
        _data_status(news, "News feed")
        st.caption(_status_message(news) or "No verified news items.")
        st.markdown("### FII / DII activity")
        fii = local_backend.get("/api/fii-dii")
        _data_status(fii, "FII/DII")
        st.caption(_status_message(fii))
        st.markdown("### Delivery data")
        delivery = local_backend.get("/api/delivery/RELIANCE")
        _data_status(delivery, "Delivery source")
        st.caption("No sample/placeholder values are shown. Delivery source status: " + _status_text(delivery))
    with event_col:
        st.markdown("### Corporate / macro events")
        events = local_backend.get("/api/events")
        _data_status(events, "Events calendar")
        st.caption(_status_message(events) or "No verified upcoming events.")
        st.markdown("### Provider limitations")
        st.markdown(
            "- Corporate announcements should come from official NSE/BSE/company disclosures.\n"
            "- FII/DII values are publication-based EOD data, not live.\n"
            "- Gemini may summarize a verified item only after a news source is connected; it will not create prices, OI or headlines.\n"
            "- These feeds are not represented as connected until a source and source timestamps are verified."
        )


def _render_settings_tab() -> None:
    st.subheader("Settings")
    st.markdown('<p class="terminal-subtitle">Settings अभी इस Streamlit session में लागू होते हैं; credentials केवल Streamlit Cloud Secrets में server-side रखें।</p>', unsafe_allow_html=True)
    current = dict(st.session_state.analysis_settings)
    st.markdown("### Add a custom intraday timeframe")
    st.caption("1–390 minute bars are resampled from Dhan's native 1-minute candles; no provider-native interval is implied.")
    custom_minute = int(st.number_input("Custom interval in minutes", min_value=1, max_value=390, value=20, step=1, key="custom_tf_minutes"))
    if st.button("Add custom timeframe"):
        custom_label = f"{custom_minute}m"
        custom_list = list(current.get("custom_timeframes", []))
        if custom_label in TIMEFRAMES:
            st.info(f"{custom_label} is already a standard timeframe.")
        elif custom_label in custom_list:
            st.info(f"{custom_label} is already configured.")
        else:
            st.session_state.analysis_settings = {**current, "custom_timeframes": [*custom_list, custom_label]}
            st.success(f"Added {custom_label}; choose it below. Export settings JSON to keep it beyond this session.")
            st.rerun()
    custom_list = list(st.session_state.analysis_settings.get("custom_timeframes", []))
    if custom_list:
        st.caption("Custom intervals: " + ", ".join(custom_list))
        remove_custom = st.multiselect("Custom intervals to remove", custom_list, key="remove_custom_tf")
        if st.button("Remove selected custom intervals"):
            st.session_state.analysis_settings = {**st.session_state.analysis_settings,
                                                  "custom_timeframes": [tf for tf in custom_list if tf not in remove_custom]}
            st.rerun()
    current = dict(st.session_state.analysis_settings)
    timeframe_options = _timeframe_options(current)
    with st.form("analysis_settings_form"):
        left, right = st.columns(2)
        with left:
            zone_tf = st.selectbox("Default zone timeframe", timeframe_options,
                                   index=timeframe_options.index(current.get("zone_timeframe", "15m")))
            volume_tf = st.selectbox("Volume-spike timeframe", timeframe_options,
                                     index=timeframe_options.index(current.get("volume_timeframe", "15m")))
            indicator_timeframes = st.multiselect(
                "EMA / indicator timeframes",
                timeframe_options,
                default=[tf for tf in current.get("indicator_timeframes", ["15m", "1h", "Daily"]) if tf in timeframe_options],
            )
            vwap_choices = [tf for tf in timeframe_options if tf not in {"Weekly", "Monthly"}]
            vwap_timeframe = st.selectbox(
                "VWAP timeframe", vwap_choices,
                index=vwap_choices.index(current.get("vwap_timeframe", "15m")) if current.get("vwap_timeframe", "15m") in vwap_choices else vwap_choices.index("15m"),
            )
            volume_lookback = st.number_input("Comparable candles baseline", min_value=5, max_value=200,
                                              value=int(current.get("volume_lookback", 30)), step=1)
            threshold = st.number_input("Volume spike threshold (×)", min_value=0.1, max_value=50.0,
                                        value=float(current.get("volume_spike_threshold", 2.1)), step=0.1)
            minimum_volume = st.number_input("Minimum absolute candle volume", min_value=0.0,
                                             value=float(current.get("minimum_volume", 0)), step=100.0)
            completed_only = st.checkbox("Use completed candles by default", value=bool(current.get("completed_candles_only", True)))
        with right:
            high_buffer = st.number_input("Zone EOD High buffer (%)", min_value=0.0, max_value=100.0,
                                          value=float(current.get("eod_high_buffer_pct", 10.0)), step=1.0)
            low_buffer = st.number_input("Zone EOD Low buffer (%)", min_value=0.0, max_value=100.0,
                                         value=float(current.get("eod_low_buffer_pct", 10.0)), step=1.0)
            use_eod = st.checkbox("Enable EOD range filter", value=bool(current.get("use_eod_range", True)))
            ema_fast = st.number_input("EMA fast", min_value=1, max_value=500,
                                       value=int(current.get("ema_fast", 35)), step=1)
            ema_slow = st.number_input("EMA slow", min_value=2, max_value=1000,
                                       value=int(current.get("ema_slow", 100)), step=1)
            account_capital = st.number_input("Account capital (₹)", min_value=1.0,
                                              value=float(current.get("account_capital", 25000.0)), step=1000.0)
            risk_pct = st.number_input("Risk per setup (%)", min_value=0.01, max_value=10.0,
                                       value=float(current.get("risk_pct", 0.5)), step=0.05)
            target_rr = st.number_input("Zone target R:R", min_value=0.1, max_value=20.0,
                                        value=float(current.get("target_rr", 5.0)), step=0.1)
        pcr_scope = st.selectbox("PCR scope", ["selected_atm_5_each_side", "all_strikes_selected_expiry"],
                                 index=0 if current.get("pcr_scope") == "selected_atm_5_each_side" else 1)
        selected_contract = st.selectbox("Default Futures contract", ["front", "next"],
                                         index=0 if current.get("futures_contract", "front") == "front" else 1)
        page_size = st.selectbox("Scanner page size", [20, 50, 100],
                                 index=[20, 50, 100].index(int(current.get("page_size", 20))))
        visible = st.multiselect("Default visible scanner columns", SCANNER_COLUMNS,
                                 default=[c for c in (st.session_state.visible_columns or DEFAULT_VISIBLE) if c in SCANNER_COLUMNS])
        save = st.form_submit_button("Save settings for this session", type="primary")
        if save:
            if ema_fast >= ema_slow:
                st.error("For this terminal's default trend alignment, EMA fast should be less than EMA slow.")
            else:
                new_settings = {
                    **current,
                    "zone_timeframe": zone_tf,
                    "volume_timeframe": volume_tf,
                    "indicator_timeframes": indicator_timeframes,
                    "vwap_timeframe": vwap_timeframe,
                    "volume_lookback": int(volume_lookback),
                    "volume_spike_threshold": float(threshold),
                    "minimum_volume": float(minimum_volume),
                    "completed_candles_only": bool(completed_only),
                    "eod_high_buffer_pct": float(high_buffer),
                    "eod_low_buffer_pct": float(low_buffer),
                    "use_eod_range": bool(use_eod),
                    "ema_fast": int(ema_fast),
                    "ema_slow": int(ema_slow),
                    "account_capital": float(account_capital),
                    "risk_pct": float(risk_pct),
                    "target_rr": float(target_rr),
                    "pcr_scope": pcr_scope,
                    "futures_contract": selected_contract,
                    "page_size": int(page_size),
                }
                st.session_state.analysis_settings = new_settings
                st.session_state.visible_columns = visible
                st.success("Settings saved for this Streamlit session. Export JSON below if you want to keep a copy.")
                current = new_settings

    st.markdown("### Provider status and source configuration")
    providers = local_backend.get("/api/providers/status")
    provider_rows = _payload_rows(providers)
    if provider_rows:
        st.dataframe(pd.DataFrame(provider_rows), width="stretch", hide_index=True)
    st.caption("Dhan credentials को Streamlit Cloud Secrets में रखें; वे इसी server-side Python process में पढ़े जाते हैं। Tokens इस form, screenshot या GitHub commit में न डालें।")
    st.caption("Dhan access tokens expire/renew according to the account's DhanHQ authentication flow; check official Dhan docs before production use.")

    st.markdown("### Global symbol mapping review")
    catalog, catalog_rows = _global_catalog_payload()
    if catalog_rows:
        mapping_cols = ["id", "display_name", "asset_class", "user_input", "provider_symbol", "status", "note"]
        mapping_frame = pd.DataFrame(catalog_rows)[mapping_cols]
        with st.form("global_mapping_form"):
            edited_mappings = st.data_editor(
                mapping_frame,
                width="stretch",
                hide_index=True,
                height=340,
                key="global_mapping_editor",
                disabled=["id", "display_name", "asset_class", "user_input", "status", "note"],
                column_config={
                    "provider_symbol": st.column_config.TextColumn(
                        "Provider symbol",
                        help="Provider-specific ticker only; entering a symbol does not verify the mapping or create a data feed.",
                    )
                },
            )
            save_mappings = st.form_submit_button("Save provider symbols for this session")
        if save_mappings:
            updated = {}
            for item in edited_mappings.to_dict("records"):
                instrument_id = str(item.get("id", "")).strip()
                provider_symbol = str(item.get("provider_symbol") or "").strip()
                if provider_symbol:
                    if len(provider_symbol) > 100 or "\n" in provider_symbol or "\r" in provider_symbol:
                        st.error(f"Invalid provider symbol for {instrument_id}; use 1–100 characters on one line.")
                        updated = None
                        break
                    updated[instrument_id] = provider_symbol
            if updated is not None:
                st.session_state.global_provider_mappings = updated
                st.success("Provider symbols saved for this session. They remain unverified until the selected provider returns matching data.")
                st.rerun()
        st.download_button(
            "Download global provider mappings JSON",
            data=json.dumps({"global_provider_mappings": st.session_state.global_provider_mappings}, indent=2, ensure_ascii=False),
            file_name="global-provider-mappings.json",
            mime="application/json",
        )
    st.info("Duplicate aliases are folded; the two NIFTY1! entries remain separate (GIFT and NSE). Suspected spelling/orientation corrections stay flagged. An editable mapping is not live data; verify the provider contract/source first.")

    st.markdown("### Export / import session settings")
    export = {"analysis_settings": st.session_state.analysis_settings,
              "visible_columns": st.session_state.visible_columns,
              "watchlist_symbols": st.session_state.watchlist_symbols,
              "global_provider_mappings": st.session_state.global_provider_mappings,
              "schema_version": 1}
    st.download_button("Download settings JSON", data=json.dumps(export, indent=2, ensure_ascii=False),
                       file_name="terminal-settings.json", mime="application/json")
    uploaded = st.file_uploader("Import settings JSON", type=["json"], key="settings_upload")
    if uploaded and st.button("Apply uploaded settings", key="apply_settings_upload"):
        try:
            payload = json.load(uploaded)
            imported = payload.get("analysis_settings", {})
            if not isinstance(imported, dict):
                raise ValueError("analysis_settings must be an object")
            safe = dict(DEFAULT_ANALYSIS_SETTINGS)
            safe.update({k: v for k, v in imported.items() if k in DEFAULT_ANALYSIS_SETTINGS})
            for key in ("account_capital", "risk_pct", "target_rr", "volume_spike_threshold",
                        "minimum_volume", "eod_high_buffer_pct", "eod_low_buffer_pct"):
                safe[key] = float(safe[key])
            for key in ("ema_fast", "ema_slow", "volume_lookback", "page_size"):
                safe[key] = int(safe[key])
            if not isinstance(safe["custom_timeframes"], list) or any(
                not isinstance(tf, str) or not is_supported_timeframe(tf) or tf in TIMEFRAMES
                for tf in safe["custom_timeframes"]
            ):
                raise ValueError("Custom timeframes must be unique intraday intervals from 1m to 390m")
            safe["custom_timeframes"] = list(dict.fromkeys(safe["custom_timeframes"]))
            import_timeframes = _timeframe_options(safe)
            if safe["zone_timeframe"] not in import_timeframes or safe["volume_timeframe"] not in import_timeframes:
                raise ValueError("Unsupported timeframe")
            if safe["vwap_timeframe"] not in [tf for tf in import_timeframes if tf not in {"Weekly", "Monthly"}]:
                raise ValueError("Unsupported VWAP timeframe")
            if safe["page_size"] not in [20, 50, 100] or safe["futures_contract"] not in ["front", "next"]:
                raise ValueError("Unsupported page size or Futures contract setting")
            if safe["pcr_scope"] not in {"full_expiry", "selected_atm_5_each_side"}:
                raise ValueError("Unsupported PCR scope")
            if not isinstance(safe["indicator_timeframes"], list) or any(tf not in import_timeframes for tf in safe["indicator_timeframes"]):
                raise ValueError("Invalid indicator timeframe list")
            if not (0 < safe["account_capital"] and 0 < safe["risk_pct"] <= 10 and 0 < safe["target_rr"] <= 20):
                raise ValueError("Risk settings are outside supported bounds")
            if not (5 <= safe["volume_lookback"] <= 200 and 0.1 <= safe["volume_spike_threshold"] <= 50 and safe["minimum_volume"] >= 0):
                raise ValueError("Volume settings are outside supported bounds")
            if not (0 <= safe["eod_high_buffer_pct"] <= 100 and 0 <= safe["eod_low_buffer_pct"] <= 100):
                raise ValueError("EOD buffers are outside supported bounds")
            if not (2 <= safe["ema_fast"] < safe["ema_slow"] <= 500):
                raise ValueError("EMA settings are outside supported bounds")
            safe_mappings = dict(st.session_state.global_provider_mappings)
            if "global_provider_mappings" in payload:
                imported_mappings = payload.get("global_provider_mappings")
                if not isinstance(imported_mappings, dict):
                    raise ValueError("global_provider_mappings must be an object")
                allowed_ids = {str(row.get("id", "")) for row in catalog_rows}
                safe_mappings = {}
                for instrument_id, provider_symbol in imported_mappings.items():
                    instrument_id = str(instrument_id)
                    if instrument_id not in allowed_ids:
                        continue
                    if not isinstance(provider_symbol, str):
                        raise ValueError("Provider symbols must be text")
                    provider_symbol = provider_symbol.strip()
                    if provider_symbol and (len(provider_symbol) > 100 or "\n" in provider_symbol or "\r" in provider_symbol):
                        raise ValueError("Provider symbols must be 1–100 characters on one line")
                    if provider_symbol:
                        safe_mappings[instrument_id] = provider_symbol
            st.session_state.analysis_settings = safe
            st.session_state.visible_columns = [c for c in payload.get("visible_columns", []) if c in SCANNER_COLUMNS]
            st.session_state.watchlist_symbols = [str(s).upper() for s in payload.get("watchlist_symbols", []) if str(s).strip()]
            st.session_state.global_provider_mappings = safe_mappings
            st.success("Settings and provider mappings imported for this session. Mappings remain unverified until provider data confirms them.")
        except Exception as exc:
            st.error(f"Settings file was not applied: {exc}")


def main() -> None:
    st.markdown("# AI PRO INSTITUTIONAL TRADING TERMINAL")
    if "health_payload" not in st.session_state:
        st.session_state.health_payload = local_backend.get("/api/health", timeout=10)
    if "market_payload" not in st.session_state:
        st.session_state.market_payload = local_backend.get("/api/market/summary", timeout=10)

    tabs = st.tabs(["NSE ALL FUTURE STOCKS", "Zone", "Global Zone", "News & Events", "Settings"])
    settings = st.session_state.analysis_settings
    with tabs[0]:
        _render_nse_tab(settings)
    with tabs[1]:
        _render_zone_tab(settings)
    with tabs[2]:
        _render_global_tab()
    with tabs[3]:
        _render_news_tab()
    with tabs[4]:
        _render_settings_tab()


if __name__ == "__main__":
    main()
else:
    main()
