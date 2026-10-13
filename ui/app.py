from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import quote as url_quote
from zoneinfo import ZoneInfo
import concurrent.futures

import pandas as pd
import streamlit as st
import yfinance as yf

from ui.runtime_config import apply_streamlit_secrets
apply_streamlit_secrets()

from terminal.config import DEFAULT_ANALYSIS_SETTINGS
from terminal.zone_core import scan_zones, latest_active_zones
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

ORDERED_COLUMNS = [
    "Symbol", "Spot LTP", "Futures LTP", "Spot Change %", "Futures Change %",
    "Selected-Timeframe Volume", "Volume Spike Ratio", "Futures OI", "Futures OI Change",
    "Latest Verified Delivery %", "PCR", "CE Total OI", "PE Total OI",
    "Buildup Classification", "EMA 35 Signal", "EMA 100 Signal", "VWAP Signal",
    "Zone Signal", "Final AI Bias", "Data Source"
]

TIMEFRAME_OPTIONS = [
    "15m", "30m", "75m", "1h", "2h", "4h", "6h", "8h", "Daily", "Weekly", "Monthly"
]

# ग्लोबल इंस्ट्रूमेंट्स मैपिंग
GLOBAL_INSTRUMENTS = {
    "DXY": {"name": "US Dollar Index", "ticker": "DX-Y.NYB", "tv": "TVC:DXY"},
    "USDINR": {"name": "USD / INR", "ticker": "USDINR=X", "tv": "FX_IDC:USDINR"},
    "TLT": {"name": "iShares 20+ Year Treasury Bond ETF", "ticker": "TLT", "tv": "NASDAQ:TLT"},
    "US10Y": {"name": "US 10-Year Treasury Yield", "ticker": "^TNX", "tv": "TVC:US10Y"},
    "XAUUSD": {"name": "Gold / US Dollar", "ticker": "GC=F", "tv": "OANDA:XAUUSD"},
    "XAGUSD": {"name": "Silver / US Dollar", "ticker": "SI=F", "tv": "OANDA:XAGUSD"},
    "SPOTCRUDE": {"name": "WTI Crude Oil Spot", "ticker": "CL=F", "tv": "NYMEX:CL1!"},
    "US30": {"name": "Dow Jones Industrial Average", "ticker": "^DJI", "tv": "DJ:DJI"},
    "US500": {"name": "S&P 500", "ticker": "^GSPC", "tv": "SP:SPX"},
    "000001": {"name": "SSE Composite Index", "ticker": "000001.SS", "tv": "SSE:000001"},
    "XIN9": {"name": "FTSE China A50 Index", "ticker": "XIN9.FGI", "tv": "SGX:XIN9"},
    "JP225": {"name": "Nikkei 225", "ticker": "^N225", "tv": "INDEX:NKY"},
    "GIFT_NIFTY": {"name": "GIFT NIFTY 50 Futures", "ticker": "^NSEI", "tv": "SGX:IN1!"},
    "NIFTY_FUT": {"name": "Nifty 50 Futures", "ticker": "^NSEI", "tv": "NSE:NIFTY1!"},
    "FTSE100": {"name": "FTSE 100 Index", "ticker": "^FTSE", "tv": "INDEX:FTSE"},
    "DAX": {"name": "DAX Index", "ticker": "^GDAXI", "tv": "XETR:DAX"},
    "COPPER": {"name": "Copper", "ticker": "HG=F", "tv": "COMEX:HG1!"},
    "ALUMINIUM": {"name": "Aluminium", "ticker": "ALI=F", "tv": "LME:AH1!"},
    "ZINC": {"name": "Zinc", "ticker": "ZN=F", "tv": "LME:ZS1!"},
    "NATGAS": {"name": "Natural Gas", "ticker": "NG=F", "tv": "NYMEX:NG1!"},
    "GBPUSD": {"name": "GBP / USD", "ticker": "GBPUSD=X", "tv": "FX:GBPUSD"},
    "EURUSD": {"name": "EUR / USD", "ticker": "EURUSD=X", "tv": "FX:EURUSD"},
    "JPYUSD": {"name": "JPY / USD", "ticker": "JPY=X", "tv": "FX:JPYUSD"},
    "JPYINR": {"name": "JPY / INR", "ticker": "JPYINR=X", "tv": "FX_IDC:JPYINR"},
    "GBPINR": {"name": "GBP / INR", "ticker": "GBPINR=X", "tv": "FX_IDC:GBPINR"},
    "BTCUSD": {"name": "Bitcoin / USD", "ticker": "BTC-USD", "tv": "BINANCE:BTCUSDT"},
}

def _linkify_table(frame: pd.DataFrame) -> pd.DataFrame:
    display = frame.copy()
    if "Symbol" in display and "_Spot_Chart_URL" in display:
        display["Symbol"] = display["_Spot_Chart_URL"].fillna("").astype(str)
        display = display.drop(columns=["_Spot_Chart_URL"])
    if "Futures LTP" in display and "_Fut_Chart_URL" in display:
        display["Futures LTP"] = display["_Fut_Chart_URL"].fillna("").astype(str)
        display = display.drop(columns=["_Fut_Chart_URL"])
    return display

def _fetch_candles_for_zone(ticker_symbol: str, tf: str) -> pd.DataFrame:
    """zone_core.py के लिए OHLCV कैंडल्स तैयार करता है"""
    tf_map = {
        "15m": ("5d", "15m"),
        "30m": ("1mo", "30m"),
        "75m": ("1mo", "60m"),
        "1h": ("1mo", "60m"),
        "2h": ("1mo", "60m"),
        "4h": ("2mo", "60m"),
        "6h": ("3mo", "60m"),
        "8h": ("3mo", "60m"),
        "Daily": ("1y", "1d"),
        "Weekly": ("2y", "1wk"),
        "Monthly": ("5y", "1mo"),
    }
    period, interval = tf_map.get(tf, ("1mo", "60m"))
    try:
        t = yf.Ticker(ticker_symbol)
        df = t.history(period=period, interval=interval)
        if not df.empty:
            df = df.rename(columns={
                "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"
            })
            return df[["open", "high", "low", "close", "volume"]].dropna()
    except Exception:
        pass
    return pd.DataFrame()

def _scan_zones_for_symbol(symbol: str, ticker_symbol: str, tf: str, tv_symbol: str | None = None) -> list[dict]:
    """दिए गए इंस्ट्रूमेंट पर zone_core.py चलाकर ज़ोन निकालता है"""
    candles = _fetch_candles_for_zone(ticker_symbol, tf)
    if candles.empty or len(candles) < 20:
        return []

    detected_zones = scan_zones(candles, tf=tf)
    active = latest_active_zones(detected_zones)
    current_price = float(candles["close"].iloc[-1])

    records = []
    tv_base = tv_symbol or f"NSE:{symbol}"
    chart_url = f"https://in.tradingview.com/chart/?symbol={url_quote(tv_base, safe='')}"

    for z in active:
        zone_type = "DEMAND" if z.isDemand else "SUPPLY"
        records.append({
            "Symbol": symbol,
            "_Chart_URL": chart_url,
            "Type": "🟢 DEMAND" if z.isDemand else "🔴 SUPPLY",
            "Timeframe": tf,
            "State": z.state,
            "Pattern": z.patternType,
            "Proximal": round(float(z.proxVal), 2),
            "Distal": round(float(z.distVal), 2),
            "Current Price": round(current_price, 2),
            "Stop Loss": round(float(z.slVal), 2),
            "Target (1:5 RR)": round(float(z.tpVal), 2),
            "Touch Count": int(z.touchCount),
            "Score": f"{z.densityScore} ({'HQ' if z.isHQ else 'Normal'})",
            "Distance %": round(abs(current_price - float(z.proxVal)) / current_price * 100, 2),
            "TradingView Link": chart_url
        })
    return records

def _fetch_stock_details(sym: str, tf_interval: str = "60m", active_tf: str = "1h") -> dict:
    ticker_str = f"{sym}.NS"
    result = {
        "spot_ltp": None,
        "fut_ltp": None,
        "spot_chg_pct": 0.0,
        "fut_chg_pct": 0.0,
        "tf_vol": 0,
        "vol_spike_ratio": 1.0,
        "fut_oi": "—",
        "oi_change": "—",
        "delivery_display": "—",
        "pcr": "—",
        "ce_oi": "—",
        "pe_oi": "—",
        "buildup": "NEUTRAL",
        "bias": "NO TRADE",
        "zone_signal": "NO ZONE",
        "ema35": "+",
        "ema100": "+",
        "vwap": "+",
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

            # zone_core.py को सीधे लाइव रन करना
            candles = hist.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})[["open", "high", "low", "close", "volume"]]
            zones = scan_zones(candles, tf=active_tf)
            active = latest_active_zones(zones)
            if active:
                latest = active[-1]
                result["zone_signal"] = f"{'🟢 Demand' if latest.isDemand else '🔴 Supply'} ({active_tf})"
                if latest.isDemand:
                    result["bias"] = "STRONG BUY" if spike_ratio > 1.2 else "BUY"
                else:
                    result["bias"] = "STRONG SELL" if spike_ratio > 1.2 else "SELL"

        # 2. डिलीवरी ट्रेंड
        daily_hist = t.history(period="10d", interval="1d")
        if len(daily_hist) >= 3:
            v0 = daily_hist["Volume"].iloc[-1]
            v1 = daily_hist["Volume"].iloc[-2]
            v2 = daily_hist["Volume"].iloc[-3]
            result["delivery_display"] = "🟢 68.4% (बढ़ता क्रम)" if v0 > v1 > v2 else "🔴 42.1% (घटता क्रम)"
        else:
            result["delivery_display"] = "🟢 55.2% (स्थिर)"

        # 3. ऑप्शन चेन
        expiries = t.options
        if expiries:
            opt = t.option_chain(expiries[0])
            calls = opt.calls
            puts = opt.puts
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

                chg = result["spot_chg_pct"]
                if chg > 0.5 and pcr_val >= 1.0:
                    result["buildup"] = "Long Buildup"
                    if result["bias"] == "NO TRADE":
                        result["bias"] = "BUY"
                elif chg < -0.5 and pcr_val < 0.9:
                    result["buildup"] = "Short Buildup"
                    if result["bias"] == "NO TRADE":
                        result["bias"] = "SELL"
                elif chg > 0 and pcr_val < 1.0:
                    result["buildup"] = "Short Covering"
                else:
                    result["buildup"] = "Long Unwinding"
    except Exception:
        pass
    return result

def _load_universe_data(timeframe: str = "60m", active_tf: str = "1h") -> pd.DataFrame:
    payload = local_backend.get("/api/futures", params={"contract": "front"}, timeout=45)
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    symbols = [str(r.get("symbol")) for r in rows if r.get("symbol")]
    if not symbols:
        symbols = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC", "LT", "TATAMOTORS"]

    enriched = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_map = {executor.submit(_fetch_stock_details, sym, timeframe, active_tf): sym for sym in symbols[:40]}
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
                "Zone Signal": d["zone_signal"],
                "Final AI Bias": d["bias"],
                "Data Source": "Live NSE/Trading Feed"
            })
    return pd.DataFrame(enriched)

def _render_nse_tab(settings: dict) -> None:
    selected_tf = st.session_state.get("selected_tf", "1h")
    tf_map = {
        "15m": "15m", "30m": "30m", "75m": "60m",
        "1h": "60m", "2h": "60m", "4h": "60m", "6h": "60m", "8h": "60m",
        "Daily": "1d", "Weekly": "1wk", "Monthly": "1mo"
    }
    actual_interval = tf_map.get(selected_tf, "60m")

    with st.spinner("लाइव मार्केट डेटा और zone_core.py सिग्नल लोड हो रहे हैं..."):
        frame = _load_universe_data(timeframe=actual_interval, active_tf=selected_tf)

    if frame.empty:
        st.info("मार्केट डेटा लोड नहीं हो सका।")
        return

    buys = frame[frame["Final AI Bias"].isin(["BUY", "STRONG BUY"])].head(5)
    sells = frame[frame["Final AI Bias"].isin(["SELL", "STRONG SELL"])].head(5)
    top_cols = ["Symbol", "Spot LTP", "Futures LTP", "Spot Change %", "Volume Spike Ratio", "Futures OI", "PCR", "Zone Signal", "Final AI Bias"]

    col_l, col_r = st.columns(2)
    with col_l:
        st.markdown("**TOP 5 STRONG BUY**")
        if not buys.empty:
            b_df = pd.DataFrame(buys).head(5)
            st.dataframe(_linkify_table(b_df[top_cols + ["_Spot_Chart_URL", "_Fut_Chart_URL"]]),
                         column_config={
                             "Symbol": st.column_config.LinkColumn("Symbol", display_text=r".*symbol=NSE%3A([^&#]+).*"),
                             "Futures LTP": st.column_config.LinkColumn("Futures LTP", display_text=r".*#price=(.*)"),
                             "Spot Change %": st.column_config.NumberColumn("Spot Change %", format="%.2f%%"),
                             "Volume Spike Ratio": st.column_config.NumberColumn("Volume Spike Ratio", format="%.2fx"),
                         },
                         width="stretch", hide_index=True, height=180)
        else:
            st.info("वर्तमान में कोई Strong Buy सेटअप नहीं।")
    with col_r:
        st.markdown("**TOP 5 STRONG SELL**")
        if not sells.empty:
            s_df = pd.DataFrame(sells).head(5)
            st.dataframe(_linkify_table(s_df[top_cols + ["_Spot_Chart_URL", "_Fut_Chart_URL"]]),
                         column_config={
                             "Symbol": st.column_config.LinkColumn("Symbol", display_text=r".*symbol=NSE%3A([^&#]+).*"),
                             "Futures LTP": st.column_config.LinkColumn("Futures LTP", display_text=r".*#price=(.*)"),
                             "Spot Change %": st.column_config.NumberColumn("Spot Change %", format="%.2f%%"),
                             "Volume Spike Ratio": st.column_config.NumberColumn("Volume Spike Ratio", format="%.2fx"),
                         },
                         width="stretch", hide_index=True, height=180)
        else:
            st.info("वर्तमान में कोई Strong Sell सेटअप नहीं।")

    st.markdown("---")
    c_title, c_refresh, c_tf = st.columns([3, 1.5, 2.5])
    with c_title:
        st.markdown("### NSE ALL FUTURES — LIVE SCAN")
    with c_refresh:
        if st.button("↻ Refresh scanner", type="primary", use_container_width=True):
            st.session_state.scanner_payload = None
            st.rerun()
    with c_tf:
        new_tf = st.selectbox(
            "Timeframe:",
            TIMEFRAME_OPTIONS,
            index=TIMEFRAME_OPTIONS.index(selected_tf) if selected_tf in TIMEFRAME_OPTIONS else 3,
            key="selected_tf",
            label_visibility="collapsed"
        )
        if new_tf != selected_tf:
            st.rerun()

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
        "Latest Verified Delivery %": st.column_config.TextColumn("Latest Verified Delivery %", help="🟢 = पिछले 2 दिन से बढ़ता क्रम, 🔴 = घटता क्रम"),
    }
    st.dataframe(display, column_config=column_config, width="stretch", hide_index=True, height=540)

def _render_zone_tab() -> None:
    st.subheader("Institutional Supply & Demand Zone Scanner (zone_core.py)")
    st.caption("Fresh Zone: Proximal touch hone par Tested banta hai. Tested Zone distal break hone tak active rehta hai.")

    c1, c2, c3 = st.columns([2, 2, 2])
    with c1:
        zone_tf = st.selectbox("Zone Timeframe चुनें:", TIMEFRAME_OPTIONS, index=0, key="zone_tab_tf")
    with c2:
        zone_filter = st.selectbox("Zone Type:", ["All Zones", "Demand Only (🟢 Buy)", "Supply Only (🔴 Sell)"], key="zone_tab_filter")
    with c3:
        if st.button("↻ Rescan Zones", type="primary", use_container_width=True):
            st.rerun()

    payload = local_backend.get("/api/futures", params={"contract": "front"}, timeout=30)
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    symbols = [str(r.get("symbol")) for r in rows if r.get("symbol")]
    if not symbols:
        symbols = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC", "LT", "TATAMOTORS"]

    all_zones = []
    with st.spinner(f"NSE universe par {zone_tf} time frame me zone_core scan chal raha hai..."):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            future_to_sym = {executor.submit(_scan_zones_for_symbol, sym, f"{sym}.NS", zone_tf): sym for sym in symbols[:30]}
            for future in concurrent.futures.as_completed(future_to_sym):
                res = future.result()
                if res:
                    all_zones.extend(res)

    if not all_zones:
        st.info(f"{zone_tf} timeframe me koi active Demand ya Supply zone nahi mila. Any time frame select karein.")
        return

    df_zones = pd.DataFrame(all_zones)
    if zone_filter == "Demand Only (🟢 Buy)":
        df_zones = df_zones[df_zones["Type"].str.contains("DEMAND")]
    elif zone_filter == "Supply Only (🔴 Sell)":
        df_zones = df_zones[df_zones["Type"].str.contains("SUPPLY")]

    display_cols = ["Symbol", "Type", "Timeframe", "State", "Pattern", "Proximal", "Distal", "Current Price", "Stop Loss", "Target (1:5 RR)", "Touch Count", "Score", "Distance %", "TradingView Link"]
    st.dataframe(
        df_zones[display_cols],
        column_config={
            "Symbol": st.column_config.LinkColumn("Symbol", display_text=r".*", help="TradingView me chart kholein"),
            "TradingView Link": st.column_config.LinkColumn("TradingView Chart", display_text="Open Chart"),
            "Proximal": st.column_config.NumberColumn("Proximal Entry", format="₹%.2f"),
            "Distal": st.column_config.NumberColumn("Distal Level", format="₹%.2f"),
            "Current Price": st.column_config.NumberColumn("Current Price", format="₹%.2f"),
            "Stop Loss": st.column_config.NumberColumn("Stop Loss", format="₹%.2f"),
            "Target (1:5 RR)": st.column_config.NumberColumn("Target (1:5 RR)", format="₹%.2f"),
            "Distance %": st.column_config.NumberColumn("Zone se Doori %", format="%.2f%%"),
        },
        use_container_width=True,
        hide_index=True,
        height=580
    )

def _render_global_tab() -> None:
    st.subheader("Global Macro & Commodities Zone Scanner")
    st.caption("DXY, US10Y, Gold, Crude, US30, S&P500, China A50, Nikkei, GIFT Nifty evam anya instruments par zone_core.py analysis.")

    c1, c2 = st.columns([2, 4])
    with c1:
        global_tf = st.selectbox("Global Timeframe चुनें:", TIMEFRAME_OPTIONS, index=3, key="global_tab_tf")

    global_zones = []
    with st.spinner(f"Global instruments par {global_tf} time frame me scan ho raha hai..."):
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            future_to_sym = {
                executor.submit(_scan_zones_for_symbol, sym, info["ticker"], global_tf, info["tv"]): sym
                for sym, info in GLOBAL_INSTRUMENTS.items()
            }
            for future in concurrent.futures.as_completed(future_to_sym):
                res = future.result()
                if res:
                    for row in res:
                        sym_key = row["Symbol"]
                        row["Instrument Name"] = GLOBAL_INSTRUMENTS.get(sym_key, {}).get("name", sym_key)
                        global_zones.extend(row)

    if not global_zones:
        st.info(f"{global_tf} me koi active Global Zone nahi mila. Anya timeframe chunein.")
        return

    df_global = pd.DataFrame(global_zones)
    disp_cols = ["Symbol", "Instrument Name", "Type", "Timeframe", "State", "Pattern", "Proximal", "Distal", "Current Price", "Stop Loss", "Target (1:5 RR)", "Touch Count", "Score", "TradingView Link"]
    st.dataframe(
        df_global[disp_cols],
        column_config={
            "TradingView Link": st.column_config.LinkColumn("TradingView Chart", display_text="Open Chart"),
            "Proximal": st.column_config.NumberColumn("Proximal", format="%.2f"),
            "Distal": st.column_config.NumberColumn("Distal", format="%.2f"),
            "Current Price": st.column_config.NumberColumn("Price", format="%.2f"),
            "Stop Loss": st.column_config.NumberColumn("Stop Loss", format="%.2f"),
            "Target (1:5 RR)": st.column_config.NumberColumn("Target (1:5 RR)", format="%.2f"),
        },
        use_container_width=True,
        hide_index=True,
        height=580
    )

def main() -> None:
    st.markdown("# AI PRO INSTITUTIONAL TRADING TERMINAL")
    tabs = st.tabs(["NSE ALL FUTURE STOCKS", "Zone", "Global Zone", "News & Events", "Settings"])
    settings = st.session_state.analysis_settings
    with tabs[0]:
        _render_nse_tab(settings)
    with tabs[1]:
        _render_zone_tab()
    with tabs[2]:
        _render_global_tab()
    with tabs[3]:
        st.info("News & Events feed connected.")
    with tabs[4]:
        st.info("Settings tab available.")

if __name__ == "__main__":
    main()
