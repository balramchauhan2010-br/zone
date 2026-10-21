"""
AI Pro Institutional Trading Terminal - Streamlit Dashboard UI
"""

import streamlit as st
import pandas as pd
from datetime import datetime
import pytz

# टर्मिनल सर्विसेज व टाइमफ्रेम इम्पोर्ट
from terminal.timeframes import DEFAULT_TIMEFRAMES, ALL_AVAILABLE_TIMEFRAMES
from terminal.services.market import is_market_open, calculate_market_breadth
from terminal.services.signals import calculate_ai_hypothesis_score

IST = pytz.timezone("Asia/Kolkata")

st.set_page_config(
    page_title="AI Pro Institutional Trading Terminal",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# कस्टम डार्क थीम स्टाइलिंग
st.markdown("""
<style>
    .reportview-container, .main .block-container {
        padding-top: 1rem;
        padding-bottom: 2rem;
    }
    .metric-card {
        background-color: #131722;
        border: 1px solid #2a2e39;
        border-radius: 6px;
        padding: 10px;
        text-align: center;
    }
    .status-verified {
        color: #089981;
        font-weight: bold;
    }
    .status-unverified {
        color: #f23645;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

st.title("AI PRO INSTITUTIONAL TRADING TERMINAL")

# 1. नेविगेशन टैब्स
tab_futures, tab_zone, tab_global, tab_news, tab_settings = st.tabs([
    "NSE ALL FUTURE STOCKS", 
    "Zone", 
    "Global Zone", 
    "News & Events", 
    "Settings"
])

# 2. डमी/बैकएंड डेटा लोडर हेल्पर
@st.cache_data(ttl=60)
def load_mock_universe_data():
    """यूनिवर्स डेटा लोड या फेच करें"""
    symbols = [
        "ABB", "360ONE", "ABCAPITAL", "ADANIENSOL", "ADANIENT", "ADANIGREEN", 
        "ADANIPORTS", "ASTRAL", "AXISBANK", "BANKBARODA", "BANKINDIA", "BIOCON", 
        "BOSCHLTD", "COALINDIA", "COFORGE", "INOXWIND", "NAUKRI", "PNB", "SUZLON", "UNIONBANK"
    ]
    records = []
    for s in symbols:
        records.append({
            "Symbol": s,
            "Spot LTP": 1500.0,
            "Futures LTP": 1502.5,
            "Dif. %": 0.17,
            "Spot Change %": 1.25,
            "Futures Change %": 1.30,
            "Selected-Timeframe Volume": 1250000,
            "Volume Spike Ratio": "1.8x",
            "Futures OI Change %": 2.5,
            "PE Total OI": 450000,
            "Buildup Classification": "Long Buildup",
            "PCR": 1.6,
            "delivery_pct_curr": 45.0,
            "delivery_pct_prev": 40.0,
            "current_volume": 1500000,
            "avg_5d_volume": 1200000,
            "fii_inflow": True,
            "news_sentiment": "positive",
            "price_fresh": True,
            "oi_fresh": True,
            "volume_fresh": True,
            "indicators_fresh": True,
            "mtf_indicators": {
                "15m": {"ema35": 105, "ema100": 100, "ltp": 110, "vwap": 102, "volume_spike": True},
                "30m": {"ema35": 105, "ema100": 100, "ltp": 110, "vwap": 102, "volume_spike": True},
                "1h": {"ema35": 105, "ema100": 100, "ltp": 110, "vwap": 102, "volume_spike": True},
                "2h": {"ema35": 105, "ema100": 100, "ltp": 110, "vwap": 102, "volume_spike": True},
                "4h": {"ema35": 105, "ema100": 100, "ltp": 110, "vwap": 102, "volume_spike": True},
                "6h": {"ema35": 105, "ema100": 100, "ltp": 110, "vwap": 102, "volume_spike": True},
                "1d": {"ema35": 105, "ema100": 100, "ltp": 110, "vwap": 102, "volume_spike": True},
                "1w": {"ema35": 105, "ema100": 100, "ltp": 110, "vwap": 102, "volume_spike": True},
                "1M": {"ema35": 105, "ema100": 100, "ltp": 110, "vwap": 102, "volume_spike": True}
            }
        })
    return records

raw_stocks = load_mock_universe_data()

# मार्केट कॉन्टेक्स्ट (ग्लोबल, निफ्टी, ब्रेथ)
market_ctx = {
    "gift_nifty_pct": 0.44,
    "nifty50_pct": 0.52,
    "nifty_ltp": 23043.0,
    "nifty_nearest_supply": 23500.0,
    "nifty_nearest_demand": 22950.0,
    "breadth_advances": 140,
    "breadth_declines": 73,
    "macro_fresh": True
}

# AI स्कोर और स्टेटस गणना
processed_rows = []
for st_item in raw_stocks:
    res = calculate_ai_hypothesis_score(st_item, market_ctx)
    row = dict(st_item)
    row["AI Hypothesis Score"] = res["total_score"]
    row["Data Status"] = res["data_status"]
    processed_rows.append(row)

df_all = pd.DataFrame(processed_rows)

# ----------------- टैब 1: NSE ALL FUTURE STOCKS -----------------
with tab_futures:
    # टॉप 10 Strong Buy (+10) और Strong Sell (-10) कार्ड्स
    top_buy = df_all[df_all["AI Hypothesis Score"] == 10].head(10)
    top_sell = df_all[df_all["AI Hypothesis Score"] == -10].head(10)

    c1, c2 = st.columns(2)
    with c1:
        st.subheader("🟢 TOP 10 BUY (+10 स्कोर)")
        if not top_buy.empty:
            st.dataframe(
                top_buy[["Symbol", "Spot LTP", "Futures LTP", "Dif. %", "Spot Change %", "AI Hypothesis Score", "Data Status"]],
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("वर्तमान में कोई स्टॉक +10 स्कोर पर नहीं है।")

    with c2:
        st.subheader("🔴 TOP 10 SELL (-10 स्कोर)")
        if not top_sell.empty:
            st.dataframe(
                top_sell[["Symbol", "Spot LTP", "Futures LTP", "Dif. %", "Spot Change %", "AI Hypothesis Score", "Data Status"]],
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("वर्तमान में कोई स्टॉक -10 स्कोर पर नहीं है।")

    st.divider()

    # मास्टर तालिका हेडर और रिफ्रेश
    col_hdr, col_tf = st.columns([3, 1])
    with col_hdr:
        st.subheader(f"📊 मास्टर तालिका — NSE ALL FUTURES ({len(df_all)} स्टॉक्स)")
        st.caption("Final AI Bias पर टच करके संस्थागत विश्लेषण विवरण देखें।")
    with col_tf:
        active_tf = st.selectbox("टाइमफ्रेम चुनें:", options=DEFAULT_TIMEFRAMES, index=7)

    # मास्टर तालिका (जिसमें Data Status सबसे अंतिम कॉलम है)
    display_cols = [
        "Symbol", "Spot LTP", "Futures LTP", "Dif. %", "Spot Change %",
        "Futures Change %", "Selected-Timeframe Volume", "PE Total OI", 
        "Buildup Classification", "AI Hypothesis Score", "Data Status"
    ]
    st.dataframe(df_all[display_cols], use_container_width=True, hide_index=True)

# ----------------- टैब 2: Zone -----------------
with tab_zone:
    st.subheader("🎯 ZONE SCANNER — ALL NSE FUTURES (NEAREST ZONE)")
    st.caption("प्रत्येक स्टॉक के 15m, 30m, 75m, 1h, 2h, 4h, 6h, Daily, Weekly, Monthly टाइमफ्रेम का स्वतः नजदीकी ज़ोन स्कैन")
    
    selected_scan_tfs = st.multiselect(
        "स्कैन टाइमफ्रेम कॉन्फ़िगर करें (Default + Optional 3m, 5m, 10m):",
        options=ALL_AVAILABLE_TIMEFRAMES,
        default=DEFAULT_TIMEFRAMES
    )
    
    st.dataframe(
        df_all[["Symbol", "Spot LTP", "Futures LTP", "Buildup Classification", "Data Status"]],
        use_container_width=True,
        hide_index=True
    )

# ----------------- टैब 3: Global Zone -----------------
with tab_global:
    st.subheader("🌐 GLOBAL INSTRUMENTS — MULTI-TIMEFRAME ZONE SCANNER")
    st.caption("DXY, US10Y, Gold, Crude, US30, S&P 500 आदि के लिए इनबिल्ट ज़ोन स्कैन")
    global_df = pd.DataFrame([
        {"Symbol": "XAUUSD (Gold)", "LTP": 4138.00, "Demand/Supply": "Supply", "Near Timeframe": "1h", "Data Status": "Verified"},
        {"Symbol": "XAGUSD (Silver)", "LTP": 58.07, "Demand/Supply": "Supply", "Near Timeframe": "1h", "Data Status": "Verified"},
        {"Symbol": "SPOTCRUDE", "LTP": 98.13, "Demand/Supply": "Demand", "Near Timeframe": "1h", "Data Status": "Verified"},
        {"Symbol": "DXY (Dollar Index)", "LTP": 101.73, "Demand/Supply": "Demand", "Near Timeframe": "4h", "Data Status": "Verified"}
    ])
    st.dataframe(global_df, use_container_width=True, hide_index=True)

# ----------------- टैब 4: News & Events -----------------
with tab_news:
    st.subheader("📰 GLOBAL STRIP & INSTITUTIONAL EVENTS")
    st.caption("ग्लोबल मैक्रो, आरबीआई इवेंट्स और कॉर्पोरेट एक्शन (15m समय सीमा से मुक्त)")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("GIFT NIFTY", "23,043.30", "+0.44%")
    m2.metric("NIFTY 50", "22,888.95", "-1.11%")
    m3.metric("DXY", "101.73", "-0.25%")
    m4.metric("US 10Y YIELD", "5.12", "-0.37%")

# ----------------- टैब 5: Settings -----------------
with tab_settings:
    st.subheader("⚙️ TERMINAL & ZONE ENGINE SETTINGS")
    st.toggle("Enable EOD Range Filter", value=True)
    st.slider("Day Close High Buffer (%)", 1.0, 15.0, 10.0)
    st.slider("Day Close Low Buffer (%)", 1.0, 15.0, 10.0)
    st.number_input("Account Capital (₹)", value=25000.0)
    st.number_input("Risk Per Trade (%)", value=0.50)
