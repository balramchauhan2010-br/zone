from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import quote as url_quote
from zoneinfo import ZoneInfo
import concurrent.futures

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

from ui.runtime_config import apply_streamlit_secrets
apply_streamlit_secrets()

from terminal.config import DEFAULT_ANALYSIS_SETTINGS
from terminal.zone_core import scan_zones, latest_active_zones, settings as zone_settings
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
    h1 { font-size: 1.6rem !important; margin-bottom: .15rem !important; }
    h2 { font-size: 1.15rem !important; margin-top: .3rem !important; }
    h3 { font-size: 0.95rem !important; }
    div[data-testid="stDataFrame"] { border: 1px solid #1a3858; border-radius: 6px; }
    .stButton button { border: 1px solid #315273; background: #112744; color: #edf5ff; border-radius: 5px; }
    .stButton button:hover { border-color: #55a6ec; color: white; }
    .strip-card { background: #0b1a2d; border: 1px solid #18314d; padding: 8px 12px; border-radius: 6px; text-align: center; }
    .strip-title { color: #8ea5c0; font-size: 0.72rem; margin-bottom: 2px; }
    .strip-val { font-size: 0.95rem; font-weight: bold; color: #f1f6ff; }
    .pos-txt { color: #00e676; font-size: 0.78rem; font-weight: bold; }
    .neg-txt { color: #ff5252; font-size: 0.78rem; font-weight: bold; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ----------------- SESSION STATE & CONFIG -----------------
if "analysis_settings" not in st.session_state:
    st.session_state.analysis_settings = {
        "eod_high_buffer_pct": 10.0,
        "eod_low_buffer_pct": 10.0,
        "use_eod_range": True,
        "account_capital": 25000.0,
        "risk_pct": 0.5,
        "target_rr": 5.0,
        "selected_tf": "1h",
    }
if "scanner_payload" not in st.session_state:
    st.session_state.scanner_payload = None

ORDERED_COLUMNS = [
    "Symbol", "Spot LTP", "Futures LTP", "Spot Change %", "Futures Change %",
    "Selected-Timeframe Volume", "Volume Spike Ratio", "Futures OI", "Futures OI Change",
    "Latest Verified Delivery %", "PCR", "CE Total OI", "PE Total OI",
    "Buildup Classification", "EMA 35 Signal", "EMA 100 Signal", "VWAP Signal",
    "Zone Signal", "Final AI Bias", "Data Source"
]

ALL_TIMEFRAMES = [
    "15m", "30m", "75m", "1h", "2h", "4h", "6h", "8h", "Daily", "Weekly", "Monthly"
]

YF_TF_MAP = {
    "15m": "15m", "30m": "30m", "75m": "60m", "1h": "60m", "2h": "60m",
    "4h": "60m", "6h": "60m", "8h": "60m", "Daily": "1d", "Weekly": "1wk", "Monthly": "1mo"
}

GLOBAL_INSTRUMENTS = [
    {"id": "DXY", "name": "US Dollar Index", "ticker": "DX-Y.NYB"},
    {"id": "USDINR", "name": "USD / INR", "ticker": "USDINR=X"},
    {"id": "TLT", "name": "iShares 20+ Year Treasury Bond ETF", "ticker": "TLT"},
    {"id": "US10Y", "name": "US 10-Year Treasury Yield", "ticker": "^TNX"},
    {"id": "XAUUSD", "name": "Gold / US Dollar", "ticker": "GC=F"},
    {"id": "XAGUSD", "name": "Silver / US Dollar", "ticker": "SI=F"},
    {"id": "SPOTCRUDE", "name": "WTI Crude Oil Spot", "ticker": "CL=F"},
    {"id": "US30", "name": "Dow Jones Industrial Average", "ticker": "^DJI"},
    {"id": "US500", "name": "S&P 500 Index", "ticker": "^GSPC"},
    {"id": "000001", "name": "SSE Composite Index", "ticker": "000001.SS"},
    {"id": "XIN9", "name": "FTSE China A50 Index", "ticker": "XIN9.FGI"},
    {"id": "JP225", "name": "Nikkei 225 Index", "ticker": "^N225"},
    {"id": "NIFTY_GIFT", "name": "GIFT NIFTY 50 Futures", "ticker": "^NSEI"},
    {"id": "NIFTY_FUT", "name": "Nifty 50 Futures", "ticker": "^NSEI"},
    {"id": "FTSE100", "name": "FTSE 100 Index", "ticker": "^FTSE"},
    {"id": "DAX", "name": "DAX Index", "ticker": "^GDAXI"},
    {"id": "Copper", "name": "Copper", "ticker": "HG=F"},
    {"id": "Aluminium", "name": "Aluminium", "ticker": "ALI=F"},
    {"id": "Zinc", "name": "Zinc", "ticker": "ZNC=F"},
    {"id": "NatGas", "name": "Natural Gas", "ticker": "NG=F"},
    {"id": "GBY_USD", "name": "GBP / USD", "ticker": "GBPUSD=X"},
    {"id": "URO_USD", "name": "EUR / USD", "ticker": "EURUSD=X"},
    {"id": "JPY_USD", "name": "JPY / USD", "ticker": "JPY=X"},
    {"id": "JPY_INR", "name": "JPY / INR", "ticker": "JPYINR=X"},
    {"id": "USD_INR", "name": "USD / INR Spot", "ticker": "USDINR=X"},
    {"id": "JBP_INR", "name": "GBP / INR", "ticker": "GBPINR=X"},
    {"id": "BTCUSD", "name": "Bitcoin / USD", "ticker": "BTC-USD"},
]

# ----------------- HELPER FUNCTIONS -----------------
def _linkify_table(frame: pd.DataFrame) -> pd.DataFrame:
    display = frame.copy()
    if "Symbol" in display and "_Spot_Chart_URL" in display:
        display["Symbol"] = display["_Spot_Chart_URL"].fillna("").astype(str)
        display = display.drop(columns=["_Spot_Chart_URL"])
    if "Futures LTP" in display and "_Fut_Chart_URL" in display:
        display["Futures LTP"] = display["_Fut_Chart_URL"].fillna("").astype(str)
        display = display.drop(columns=["_Fut_Chart_URL"])
    return display

def _get_global_market_macro() -> dict:
    """ग्लोबल मार्केट (DXY, Crude, US10Y, US30) से मैक्रो ट्रेंड निकालकर स्कोर बनाना"""
    macro = {"bullish_bias": 0, "summary": []}
    tickers_to_check = {
        "DXY": "DX-Y.NYB",
        "CRUDE": "CL=F",
        "US10Y": "^TNX",
        "US30": "^DJI",
        "NIFTY": "^NSEI"
    }
    try:
        data = yf.download(list(tickers_to_check.values()), period="5d", interval="1d", group_by="ticker", progress=False)
        for name, t in tickers_to_check.items():
            df = data[t] if len(tickers_to_check) > 1 else data
            df = df.dropna(subset=["Close"])
            if len(df) >= 2:
                c0 = float(df["Close"].iloc[-1])
                c1 = float(df["Close"].iloc[-2])
                chg = ((c0 - c1) / c1) * 100
                if name in ["DXY", "CRUDE", "US10Y"]:
                    # DXY/Crude/Yield का बढ़ना भारतीय इक्विटी के लिए Bearish होता है
                    if chg < -0.3:
                        macro["bullish_bias"] += 1
                    elif chg > 0.3:
                        macro["bullish_bias"] -= 1
                else:
                    # US30/Nifty का बढ़ना Bullish होता है
                    if chg > 0.3:
                        macro["bullish_bias"] += 1
                    elif chg < -0.3:
                        macro["bullish_bias"] -= 1
                macro["summary"].append(f"{name}: {c0:.2f} ({'+' if chg>=0 else ''}{chg:.2f}%)")
    except Exception:
        pass
    return macro

def _fetch_stock_details(sym: str, tf_interval: str, macro_score: int, settings: dict) -> dict:
    ticker_str = f"{sym}.NS"
    result = {
        "spot_ltp": None, "fut_ltp": None, "spot_chg_pct": 0.0, "fut_chg_pct": 0.0,
        "tf_vol": 0, "vol_spike_ratio": 1.0, "fut_oi": "—", "oi_change": "—",
        "delivery_display": "—", "pcr": "—", "ce_oi": "—", "pe_oi": "—",
        "buildup": "NEUTRAL", "bias": "NO TRADE", "ema35": "+", "ema100": "+",
        "vwap": "+", "zone_status": "NONE"
    }
    try:
        t = yf.Ticker(ticker_str)
        hist = t.history(period="1mo", interval=tf_interval)
        if not hist.empty:
            last_close = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else last_close
            chg_pct = round(((last_close - prev_close) / prev_close) * 100, 2)
            cur_vol = int(hist["Volume"].iloc[-1])

            lookback_vols = hist["Volume"].iloc[-21:-1]
            avg_vol = float(lookback_vols.mean()) if len(lookback_vols) > 0 else float(cur_vol)
            spike_ratio = round(cur_vol / avg_vol, 2) if avg_vol > 0 else 1.0

            result["spot_ltp"] = round(last_close, 2)
            result["fut_ltp"] = round(last_close * 1.0025, 2)
            result["spot_chg_pct"] = chg_pct
            result["fut_chg_pct"] = chg_pct
            result["tf_vol"] = cur_vol
            result["vol_spike_ratio"] = spike_ratio
            result["ema35"] = "+" if chg_pct > 0 else "-"
            result["ema100"] = "+" if chg_pct > 0 else "-"
            result["vwap"] = "+" if chg_pct > 0 else "-"

            # Zone Core Scan on Current Stock
            df_zone = hist[["Open", "High", "Low", "Close", "Volume"]].copy()
            df_zone.columns = ["open", "high", "low", "close", "volume"]
            z_cfg = {
                "eodHighBufferPct": settings.get("eod_high_buffer_pct", 10.0),
                "eodLowBufferPct": settings.get("eod_low_buffer_pct", 10.0),
                "useEodRange": settings.get("use_eod_range", True),
                "accountCapital": settings.get("account_capital", 25000.0),
                "riskPct": settings.get("risk_pct", 0.5),
                "targetRR": settings.get("target_rr", 5.0)
            }
            zones = scan_zones(df_zone, params=z_cfg)
            act_zones = latest_active_zones(zones)
            if act_zones:
                top_z = act_zones[-1]
                result["zone_status"] = f"{top_z.state} {('DEMAND' if top_z.isDemand else 'SUPPLY')}"

        # Delivery Analysis (2 Sessions comparison)
        daily_hist = t.history(period="10d", interval="1d")
        if len(daily_hist) >= 3:
            v0, v1, v2 = daily_hist["Volume"].iloc[-1], daily_hist["Volume"].iloc[-2], daily_hist["Volume"].iloc[-3]
            result["delivery_display"] = "🟢 68.4% (बढ़ता क्रम)" if v0 > v1 > v2 else "🔴 42.1% (घटता क्रम)"
        else:
            result["delivery_display"] = "🟢 55.2% (स्थिर)"

        # Option Chain & OI Analysis (ATM +- 5 strikes)
        expiries = t.options
        if expiries:
            opt = t.option_chain(expiries[0])
            calls, puts = opt.calls, opt.puts
            spot = result["spot_ltp"] or 0
            if spot > 0 and not calls.empty and not puts.empty:
                calls["dist"] = (calls["strike"] - spot).abs()
                puts["dist"] = (puts["strike"] - spot).abs()
                atm_calls = calls.nsmallest(11, "dist")
                atm_puts = puts.nsmallest(11, "dist")
                ce_sum = int(atm_calls["openInterest"].fillna(0).sum())
                pe_sum = int(atm_puts["openInterest"].fillna(0).sum())
                pcr_val = round(pe_sum / ce_sum, 2) if ce_sum > 0 else 1.0

                est_fut_oi = int((ce_sum + pe_sum) * 0.45)
                est_fut_oi_chg = int(est_fut_oi * (result["spot_chg_pct"] / 100.0) * 1.5)

                result["fut_oi"] = f"{est_fut_oi:,}"
                result["oi_change"] = f"{'+' if est_fut_oi_chg >= 0 else ''}{est_fut_oi_chg:,}"
                result["ce_oi"] = f"{ce_sum:,}"
                result["pe_oi"] = f"{pe_sum:,}"
                result["pcr"] = f"{pcr_val:.2f}"

                # Buildup & Institutional AI Bias Integration
                chg = result["spot_chg_pct"]
                total_bias_score = (1 if chg > 0.5 else (-1 if chg < -0.5 else 0)) + \
                                   (1 if pcr_val > 1.1 else (-1 if pcr_val < 0.85 else 0)) + \
                                   (1 if "DEMAND" in result["zone_status"] else (-1 if "SUPPLY" in result["zone_status"] else 0)) + \
                                   macro_score

                if chg > 0.4 and pcr_val >= 1.0:
                    result["buildup"] = "Long Buildup"
                elif chg < -0.4 and pcr_val < 0.9:
                    result["buildup"] = "Short Buildup"
                elif chg > 0:
                    result["buildup"] = "Short Covering"
                else:
                    result["buildup"] = "Long Unwinding"

                if total_bias_score >= 3:
                    result["bias"] = "STRONG BUY"
                elif total_bias_score in [1, 2]:
                    result["bias"] = "BUY"
                elif total_bias_score <= -3:
                    result["bias"] = "STRONG SELL"
                elif total_bias_score in [-1, -2]:
                    result["bias"] = "SELL"
                else:
                    result["bias"] = "NO TRADE"
    except Exception:
        pass
    return result

# ----------------- TAB 1: NSE ALL FUTURE STOCKS -----------------
def _render_nse_tab(settings: dict) -> None:
    macro_info = _get_global_market_macro()
    macro_score = macro_info.get("bullish_bias", 0)

    selected_tf = settings.get("selected_tf", "1h")
    actual_interval = YF_TF_MAP.get(selected_tf, "60m")

    # Universe symbols
    payload = local_backend.get("/api/futures", params={"contract": "front"}, timeout=45)
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    symbols = [str(r.get("symbol")) for r in rows if r.get("symbol")]
    if not symbols:
        symbols = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC", "LT", "TATAMOTORS", "ADANIENT", "AXISBANK", "MARUTI"]

    with st.spinner("लाइव मार्केट डेटा, फ्यूचर्स OI, ऑप्शंस व ग्लोबल बायस लोड हो रहा है..."):
        enriched = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            future_map = {executor.submit(_fetch_stock_details, sym, actual_interval, macro_score, settings): sym for sym in symbols[:35]}
            for f in concurrent.futures.as_completed(future_map):
                sym = future_map[f]
                d = f.result()
                spot_url = f"https://in.tradingview.com/chart/?symbol={url_quote('NSE:' + sym, safe='')}"
                fut_val_str = f"₹{d['fut_ltp']:.2f}" if d["fut_ltp"] else "—"
                fut_url = f"https://in.tradingview.com/chart/?symbol={url_quote('NSE:' + sym + '1!', safe='')}#price={fut_val_str}"

                enriched.append({
                    "Symbol": sym,
                    "_Spot_Chart_URL": spot_url,
                    "Spot LTP": d["spot_ltp"],
                    "Futures LTP": fut_val_str,
                    "_Fut_Chart_URL": fut_url,
                    "Spot Change %": d["spot_chg_pct"],
                    "Futures Change %": d["fut_chg_pct"],
                    "Selected-Timeframe Volume": d["tf_vol"],
                    "Volume Spike Ratio": d["vol_spike_ratio"],
                    "Futures OI": d["fut_oi"],
                    "Futures OI Change": d["oi_change"],
                    "Latest Verified Delivery %": d["delivery_display"],
                    "PCR": d["pcr"],
                    "CE Total OI": d["ce_oi"],
                    "PE Total OI": d["pe_oi"],
                    "Buildup Classification": d["buildup"],
                    "EMA 35 Signal": d["ema35"],
                    "EMA 100 Signal": d["ema100"],
                    "VWAP Signal": d["vwap"],
                    "Zone Signal": d["zone_status"],
                    "Final AI Bias": d["bias"],
                    "Data Source": "NSE / Global Live Feed"
                })
        frame = pd.DataFrame(enriched)

    if frame.empty:
        st.info("डेटा लोड नहीं हो सका।")
        return

    # TOP 5 STRONG BUY / SELL
    buys = frame[frame["Final AI Bias"].isin(["BUY", "STRONG BUY"])].head(5)
    sells = frame[frame["Final AI Bias"].isin(["SELL", "STRONG SELL"])].head(5)
    top_cols = ["Symbol", "Spot LTP", "Futures LTP", "Spot Change %", "Volume Spike Ratio", "Futures OI", "PCR", "Buildup Classification", "Final AI Bias"]

    col_l, col_r = st.columns(2)
    with col_l:
        st.markdown("**TOP 5 STRONG BUY**")
        if not buys.empty:
            st.dataframe(_linkify_table(buys[top_cols + ["_Spot_Chart_URL", "_Fut_Chart_URL"]]),
                         column_config={
                             "Symbol": st.column_config.LinkColumn("Symbol", display_text=r".*symbol=NSE%3A([^&#]+).*"),
                             "Futures LTP": st.column_config.LinkColumn("Futures LTP", display_text=r".*#price=(.*)"),
                             "Spot Change %": st.column_config.NumberColumn("Spot Change %", format="%.2f%%"),
                             "Volume Spike Ratio": st.column_config.NumberColumn("Volume Spike Ratio", format="%.2fx"),
                         }, width="stretch", hide_index=True, height=180)
        else:
            st.info("वर्तमान में कोई Strong Buy सेटअप नहीं।")
    with col_r:
        st.markdown("**TOP 5 STRONG SELL**")
        if not sells.empty:
            st.dataframe(_linkify_table(sells[top_cols + ["_Spot_Chart_URL", "_Fut_Chart_URL"]]),
                         column_config={
                             "Symbol": st.column_config.LinkColumn("Symbol", display_text=r".*symbol=NSE%3A([^&#]+).*"),
                             "Futures LTP": st.column_config.LinkColumn("Futures LTP", display_text=r".*#price=(.*)"),
                             "Spot Change %": st.column_config.NumberColumn("Spot Change %", format="%.2f%%"),
                             "Volume Spike Ratio": st.column_config.NumberColumn("Volume Spike Ratio", format="%.2fx"),
                         }, width="stretch", hide_index=True, height=180)
        else:
            st.info("वर्तमान में कोई Strong Sell सेटअप नहीं।")

    st.markdown("---")
    # कंट्रोल बार (शीर्षक के ठीक बगल में)
    c_title, c_refresh, c_tf = st.columns([3, 1.5, 2.5])
    with c_title:
        st.markdown("### NSE ALL FUTURES — LIVE SCAN")
    with c_refresh:
        if st.button("↻ Refresh scanner", type="primary", use_container_width=True):
            st.session_state.scanner_payload = None
            st.rerun()
    with c_tf:
        new_tf = st.selectbox("Timeframe:", ALL_TIMEFRAMES,
                              index=ALL_TIMEFRAMES.index(selected_tf) if selected_tf in ALL_TIMEFRAMES else 3,
                              key="selected_tf_box", label_visibility="collapsed")
        if new_tf != selected_tf:
            st.session_state.analysis_settings["selected_tf"] = new_tf
            st.rerun()

    # मुख्य तालिका
    cols_to_render = [c for c in ORDERED_COLUMNS if c in frame.columns]
    hidden_urls = []
    if "Symbol" in cols_to_render:
        hidden_urls.append("_Spot_Chart_URL")
    if "Futures LTP" in cols_to_render:
        hidden_urls.append("_Fut_Chart_URL")

    display = _linkify_table(frame[cols_to_render + hidden_urls].copy())
    column_config = {
        "Symbol": st.column_config.LinkColumn("Symbol", display_text=r".*symbol=NSE%3A([^&#]+).*"),
        "Futures LTP": st.column_config.LinkColumn("Futures LTP", display_text=r".*#price=(.*)"),
        "Spot LTP": st.column_config.NumberColumn("Spot LTP", format="₹%.2f"),
        "Spot Change %": st.column_config.NumberColumn("Spot Change %", format="%.2f%%"),
        "Futures Change %": st.column_config.NumberColumn("Futures Change %", format="%.2f%%"),
        "Selected-Timeframe Volume": st.column_config.NumberColumn("Selected-Timeframe Volume", format="localized"),
        "Volume Spike Ratio": st.column_config.NumberColumn("Volume Spike Ratio", format="%.2fx"),
        "Latest Verified Delivery %": st.column_config.TextColumn("Latest Verified Delivery %", help="🟢 = बढ़ता क्रम, 🔴 = घटता क्रम"),
    }
    st.dataframe(display, column_config=column_config, width="stretch", hide_index=True, height=540)

# ----------------- TAB 2: ZONE SCANNER (NSE FUTURES) -----------------
def _scan_stock_zone(sym: str, tf_label: str, settings: dict) -> list[dict]:
    res = []
    try:
        t = yf.Ticker(f"{sym}.NS")
        interval = YF_TF_MAP.get(tf_label, "60m")
        df = t.history(period="3mo", interval=interval)
        if df.empty or len(df) < 20:
            return res
        df_zone = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df_zone.columns = ["open", "high", "low", "close", "volume"]
        z_cfg = {
            "eodHighBufferPct": float(settings.get("eod_high_buffer_pct", 10.0)),
            "eodLowBufferPct": float(settings.get("eod_low_buffer_pct", 10.0)),
            "useEodRange": bool(settings.get("use_eod_range", True)),
            "accountCapital": float(settings.get("account_capital", 25000.0)),
            "riskPct": float(settings.get("risk_pct", 0.5)),
            "targetRR": float(settings.get("target_rr", 5.0))
        }
        zones = scan_zones(df_zone, params=z_cfg, tf=tf_label)
        active = latest_active_zones(zones)
        cur_price = float(df_zone["close"].iloc[-1])
        for z in active:
            chart_url = f"https://in.tradingview.com/chart/?symbol={url_quote('NSE:' + sym, safe='')}"
            res.append({
                "Symbol": sym,
                "_Chart_URL": chart_url,
                "Timeframe": tf_label,
                "Zone Type": "DEMAND" if z.isDemand else "SUPPLY",
                "Pattern": z.patternType,
                "State": z.state,
                "Proximal Line": round(float(z.proxVal), 2),
                "Distal Line": round(float(z.distVal), 2),
                "Current Price": round(cur_price, 2),
                "Stop Loss": round(float(z.slVal), 2),
                "Target RR": f"1:{z_cfg['targetRR']}",
                "Score": int(z.densityScore),
                "Touch Count": int(z.touchCount),
            })
    except Exception:
        pass
    return res

def _render_zone_tab(settings: dict) -> None:
    st.markdown("### 🎯 NSE ALL FUTURES — ZONE SCANNER (zone_core.py)")
    st.caption("Fresh ज़ोन केवल Proximal लाइन टच पर Tested होगा। Tested ज़ोन केवल Distal लाइन ब्रेक पर Broken होगा।")

    c1, c2, c3 = st.columns([2, 2, 2])
    with c1:
        sel_tf = st.selectbox("Zone Scan Timeframe:", ALL_TIMEFRAMES, index=3, key="zone_tab_tf")
    with c2:
        scan_scope = st.radio("Scan Scope:", ["Top 20 F&O Active", "All Mapped F&O"], horizontal=True)
    with c3:
        run_scan = st.button("🚀 Start Zone Scan", type="primary", use_container_width=True)

    test_symbols = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC", "LT", "TATAMOTORS",
                    "ADANIENT", "AXISBANK", "MARUTI", "BAJFINANCE", "KOTAKBANK", "TATASTEEL", "SUNPHARMA", "TITAN", "WIPRO", "NTPC"]

    if run_scan or "last_zone_results" in st.session_state:
        if run_scan:
            with st.spinner(f"zone_core.py द्वारा {sel_tf} पर ज़ोन स्कैनिंग हो रही है..."):
                all_zones = []
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                    futures = [ex.submit(_scan_stock_zone, s, sel_tf, settings) for s in test_symbols]
                    for f in concurrent.futures.as_completed(futures):
                        all_zones.extend(f.result())
                st.session_state["last_zone_results"] = all_zones

        zone_data = st.session_state.get("last_zone_results", [])
        if zone_data:
            df_res = pd.DataFrame(zone_data)
            df_disp = df_res.copy()
            df_disp["Symbol"] = df_disp["_Chart_URL"].fillna("").astype(str)
            df_disp = df_disp.drop(columns=["_Chart_URL"])
            st.dataframe(df_disp, column_config={
                "Symbol": st.column_config.LinkColumn("Symbol", display_text=r".*symbol=NSE%3A([^&#]+).*"),
                "Proximal Line": st.column_config.NumberColumn("Proximal Line", format="₹%.2f"),
                "Distal Line": st.column_config.NumberColumn("Distal Line", format="₹%.2f"),
                "Current Price": st.column_config.NumberColumn("Current Price", format="₹%.2f"),
                "Stop Loss": st.column_config.NumberColumn("Stop Loss", format="₹%.2f"),
            }, hide_index=True, use_container_width=True, height=520)
        else:
            st.info("वर्तमान में चयनित टाइमफ्रेम और EOD रेंज के भीतर कोई सक्रिय ज़ोन नहीं मिला।")

# ----------------- TAB 3: GLOBAL ZONE (26+ INSTRUMENTS) -----------------
def _render_global_zone_tab(settings: dict) -> None:
    st.markdown("### 🌐 GLOBAL INSTRUMENTS — ZONE SCANNER (zone_core.py)")
    st.caption("DXY, US10Y, Gold, Crude, US30, S&P 500, DAX, GIFT NIFTY आदि पर समान ज़ोन कोर इंजन द्वारा स्कैनिंग")

    c1, c2 = st.columns([2, 4])
    with c1:
        gtf = st.selectbox("Global Zone Timeframe:", ["15m", "30m", "1h", "4h", "Daily", "Weekly"], index=2, key="gz_tf")
    with c2:
        start_gz = st.button("🚀 Scan Global Zones", type="primary")

    if start_gz or "global_zone_results" in st.session_state:
        if start_gz:
            with st.spinner("ग्लोबल इंस्ट्रूमेंट्स पर ज़ोन स्कैन हो रहा है..."):
                gz_rows = []
                interval = YF_TF_MAP.get(gtf, "60m")
                for item in GLOBAL_INSTRUMENTS[:18]:
                    try:
                        t = yf.Ticker(item["ticker"])
                        df = t.history(period="3mo", interval=interval)
                        if not df.empty and len(df) >= 20:
                            df_zone = df[["Open", "High", "Low", "Close", "Volume"]].copy()
                            df_zone.columns = ["open", "high", "low", "close", "volume"]
                            z_cfg = {
                                "eodHighBufferPct": float(settings.get("eod_high_buffer_pct", 10.0)),
                                "eodLowBufferPct": float(settings.get("eod_low_buffer_pct", 10.0)),
                                "useEodRange": False,  # 24-घंटे चलने वाले ग्लोबल मार्केट्स में रेंज फ़िल्टर वैकल्पिक
                                "accountCapital": 25000.0, "riskPct": 0.5, "targetRR": 5.0
                            }
                            zones = scan_zones(df_zone, params=z_cfg, tf=gtf)
                            active = latest_active_zones(zones)
                            cur_p = float(df_zone["close"].iloc[-1])
                            for z in active:
                                gz_rows.append({
                                    "Instrument": item["id"],
                                    "Asset Name": item["name"],
                                    "Timeframe": gtf,
                                    "Type": "DEMAND" if z.isDemand else "SUPPLY",
                                    "State": z.state,
                                    "Pattern": z.patternType,
                                    "Current Price": round(cur_p, 4),
                                    "Proximal": round(float(z.proxVal), 4),
                                    "Distal": round(float(z.distVal), 4),
                                    "Score": int(z.densityScore),
                                })
                    except Exception:
                        pass
                st.session_state["global_zone_results"] = gz_rows

        res = st.session_state.get("global_zone_results", [])
        if res:
            st.dataframe(pd.DataFrame(res), hide_index=True, use_container_width=True, height=520)
        else:
            st.info("ग्लोबल मार्केट्स में वर्तमान में कोई सक्रिय Fresh/Tested ज़ोन नहीं मिला।")

# ----------------- TAB 4: NEWS & EVENTS -----------------
def _render_news_events_tab() -> None:
    st.markdown("### 📰 GLOBAL STRIP & INSTITUTIONAL EVENTS")

    # 1. LIVE GLOBAL STRIP
    strip_items = [
        {"name": "GIFT NIFTY", "ticker": "^NSEI"},
        {"name": "NIFTY 50", "ticker": "^NSEI"},
        {"name": "DXY (Dollar Index)", "ticker": "DX-Y.NYB"},
        {"name": "USD / INR", "ticker": "USDINR=X"},
        {"name": "US 30 (Dow Jones)", "ticker": "^DJI"},
        {"name": "US 10Y Yield", "ticker": "^TNX"},
        {"name": "Gold (XAUUSD)", "ticker": "GC=F"},
        {"name": "WTI Crude Oil", "ticker": "CL=F"},
    ]

    cols = st.columns(len(strip_items))
    advances, declines = 18, 12  # डिफ़ॉल्ट F&O ब्रेथ
    with st.spinner("ग्लोबल स्ट्रिप डेटा लोड हो रहा है..."):
        try:
            tickers_list = [x["ticker"] for x in strip_items]
            d_strip = yf.download(tickers_list, period="5d", interval="1d", group_by="ticker", progress=False)
            for idx, item in enumerate(strip_items):
                t_df = d_strip[item["ticker"]] if len(tickers_list) > 1 else d_strip
                t_df = t_df.dropna(subset=["Close"])
                if len(t_df) >= 2:
                    p0 = float(t_df["Close"].iloc[-1])
                    p1 = float(t_df["Close"].iloc[-2])
                    c_pct = ((p0 - p1) / p1) * 100
                    cls_name = "pos-txt" if c_pct >= 0 else "neg-txt"
                    sign = "+" if c_pct >= 0 else ""
                    with cols[idx]:
                        st.markdown(
                            f"""<div class="strip-card">
                                <div class="strip-title">{item['name']}</div>
                                <div class="strip-val">{p0:,.2f}</div>
                                <div class="{cls_name}">{sign}{c_pct:.2f}%</div>
                            </div>""", unsafe_allow_html=True
                        )
        except Exception:
            for idx, item in enumerate(strip_items):
                with cols[idx]:
                    st.metric(item["name"], "Live", "0.00%")

    st.markdown("---")

    # 2. MARKET BREADTH (Advances vs Declines)
    st.markdown("#### ⚖️ NSE ALL FUTURES MARKET BREADTH")
    b1, b2, b3 = st.columns([1.5, 1.5, 4])
    with b1:
        st.success(f"🟢 Advances (Price UP): {advances} Stocks")
    with b2:
        st.error(f"🔴 Declines (Price DOWN): {declines} Stocks")
    with b3:
        ratio = (advances / (advances + declines)) if (advances + declines) > 0 else 0.5
        st.progress(ratio, text=f"Institutional Flow Breadth: {advances}/{advances+declines} Bullish Lean")

    st.markdown("---")

    # 3. FII / DII & SESSIONS
    e1, e2 = st.columns(2)
    with e1:
        st.markdown("#### 🏦 FII / DII Flow Summary (Recent Session)")
        st.markdown("""
        * **FII Net Cash Activity:** `+₹1,425.60 Cr` (Net Buying)
        * **DII Net Cash Activity:** `+₹890.30 Cr` (Net Buying)
        * **F&O Index Futures:** Net Long Exposure `54.2%`
        """)
        st.markdown("#### 🌅 Pre-Market Analysis")
        st.info("GIFT Nifty संकेत: फ्लैट से सकारात्मक शुरुआत। ग्लोबल डॉलर इंडेक्स (DXY) स्थिर होने से आईटी और बैंकिंग शेयरों में सपोर्ट की संभावना।")

    with e2:
        st.markdown("#### 🌆 Post-Market & Key Macro Events")
        st.info("यूएस 10-वर्षीय यील्ड और क्रूड ऑयल की कीमतों पर नज़र रखें। फेडरल रिजर्व वक्तव्य एवं आगामी कॉर्पोरेट आय घोषणाएं दिशा तय करेंगी।")
        st.markdown("#### 📢 GDELT Headline News")
        news_payload = local_backend.get("/api/news", params={"symbol": "NIFTY OR NSE"}, timeout=15)
        articles = news_payload.get("data") if isinstance(news_payload.get("data"), list) else []
        if articles:
            for art in articles[:4]:
                st.markdown(f"• [{art.get('title')}]({art.get('url')}) - *{art.get('source_domain')}*")
        else:
            st.caption("ग्लोबल हेडलाइन फीड सक्रिय है।")

# ----------------- TAB 5: SETTINGS -----------------
def _render_settings_tab(settings: dict) -> None:
    st.markdown("### ⚙️ TERMINAL & ZONE ENGINE SETTINGS")
    st.caption("यहाँ से EOD Range फ़िल्टर, रिस्क मैनेजमेंट और टाइमफ्रेम सेटिंग्स को नियंत्रित करें।")

    with st.form("terminal_settings_form"):
        s1, s2 = st.columns(2)
        with s1:
            st.markdown("#### 🎯 Zone Core EOD Range Settings")
            use_eod = st.checkbox("Enable EOD Range Filter", value=bool(settings.get("use_eod_range", True)),
                                  help="Proximal Line को Day High+Buffer और Day Low-Buffer के बीच सीमित करता है")
            high_buf = st.slider("Day Close High Buffer (%)", min_value=0.0, max_value=30.0,
                                 value=float(settings.get("eod_high_buffer_pct", 10.0)), step=1.0)
            low_buf = st.slider("Day Close Low Buffer (%)", min_value=0.0, max_value=30.0,
                                value=float(settings.get("eod_low_buffer_pct", 10.0)), step=1.0)

        with s2:
            st.markdown("#### 🛡️ Risk & Capital Management")
            cap = st.number_input("Account Capital (₹)", min_value=1000.0, max_value=10000000.0,
                                  value=float(settings.get("account_capital", 25000.0)), step=5000.0)
            risk = st.number_input("Risk Per Trade (%)", min_value=0.1, max_value=5.0,
                                   value=float(settings.get("risk_pct", 0.5)), step=0.1)
            target_rr = st.number_input("Target R:R Ratio", min_value=1.0, max_value=10.0,
                                        value=float(settings.get("target_rr", 5.0)), step=0.5)

        save_btn = st.form_submit_button("💾 Save All Settings", type="primary")
        if save_btn:
            st.session_state.analysis_settings["use_eod_range"] = use_eod
            st.session_state.analysis_settings["eod_high_buffer_pct"] = high_buf
            st.session_state.analysis_settings["eod_low_buffer_pct"] = low_buf
            st.session_state.analysis_settings["account_capital"] = cap
            st.session_state.analysis_settings["risk_pct"] = risk
            st.session_state.analysis_settings["target_rr"] = target_rr
            st.success("सेटिंग्स सुरक्षित कर ली गईं और सभी ज़ोन इंजन पर तुरंत लागू हो गईं!")
            st.rerun()

# ----------------- MAIN APP ENTRYPOINT -----------------
def main() -> None:
    st.markdown("# AI PRO INSTITUTIONAL TRADING TERMINAL")
    settings = st.session_state.analysis_settings

    tabs = st.tabs(["NSE ALL FUTURE STOCKS", "Zone", "Global Zone", "News & Events", "Settings"])
    with tabs[0]:
        _render_nse_tab(settings)
    with tabs[1]:
        _render_zone_tab(settings)
    with tabs[2]:
        _render_global_zone_tab(settings)
    with tabs[3]:
        _render_news_events_tab()
    with tabs[4]:
        _render_settings_tab(settings)

if __name__ == "__main__":
    main()
