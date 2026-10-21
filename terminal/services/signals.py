from typing import Dict, Any

def evaluate_mtf_rule1(mtf_data: Dict[str, Dict[str, Any]]) -> int:
    """
    नियम 1:
    Lower (15m, 30m), Intermediate (1h, 2h), Higher (4h, 6h, 1d, 1w, 1M)
    प्रत्येक ग्रुप में: EMA35 > EMA100, LTP > VWAP और Volume Spike हो।
    तीनों ग्रुप Bullish हों तो +1, तीनों Bearish हों तो -1, अन्यथा 0।
    """
    def check_group(tf_list):
        bull, bear, count = 0, 0, 0
        for tf in tf_list:
            if tf in mtf_data:
                d = mtf_data[tf]
                count += 1
                if d.get("ema35", 0) > d.get("ema100", 0) and d.get("ltp", 0) > d.get("vwap", 0) and d.get("volume_spike", False):
                    bull += 1
                elif d.get("ema35", 0) < d.get("ema100", 0) and d.get("ltp", 0) < d.get("vwap", 0) and d.get("volume_spike", False):
                    bear += 1
        if count > 0 and bull == count:
            return 1
        if count > 0 and bear == count:
            return -1
        return 0

    lower = check_group(["15m", "30m"])
    inter = check_group(["1h", "2h"])
    higher = check_group(["4h", "6h", "1d", "1w", "1M"])

    if lower == 1 and inter == 1 and higher == 1:
        return 1
    elif lower == -1 and inter == -1 and higher == -1:
        return -1
    return 0

def calculate_ai_hypothesis_score(stock: dict, market: dict) -> dict:
    """
    10-Point Institutional AI Hypothesis Calculator
    """
    s = {}
    verified_status = []

    # 1. MTF EMA/VWAP/Volume Spike
    s['r1_mtf'] = evaluate_mtf_rule1(stock.get('mtf_indicators', {}))
    verified_status.append(stock.get('indicators_fresh', True))

    # 2. Spot LTP vs Futures LTP
    spot = stock.get('spot_ltp', 0.0)
    fut = stock.get('futures_ltp', 0.0)
    s['r2_fut_premium'] = 1 if fut > spot else (-1 if fut < spot else 0)
    verified_status.append(stock.get('price_fresh', True))

    # 3. Delivery %
    deliv_c = stock.get('delivery_pct_curr', 0.0)
    deliv_p = stock.get('delivery_pct_prev', 0.0)
    s['r3_delivery'] = 1 if deliv_c > deliv_p else (-1 if deliv_c < deliv_p else 0)
    verified_status.append(True)

    # 4. PCR
    pcr = stock.get('pcr', 1.0)
    s['r4_pcr'] = 1 if pcr > 1.5 else (-1 if pcr < 1.0 else 0)
    verified_status.append(stock.get('oi_fresh', True))

    # 5. Buildup Classification
    buildup = str(stock.get('buildup', '')).lower()
    s['r5_buildup'] = 1 if 'long buildup' in buildup else (-1 if 'short buildup' in buildup else 0)
    verified_status.append(stock.get('oi_fresh', True))

    # 6. Global/Macro Finance + FII Inflow + Stock News
    fii_inflow = stock.get('fii_inflow', False)
    sentiment = stock.get('news_sentiment', 'neutral')
    if fii_inflow and sentiment in ['positive', 'neutral']:
        s['r6_macro_fii'] = 1
    elif not fii_inflow and sentiment in ['negative', 'neutral']:
        s['r6_macro_fii'] = -1
    else:
        s['r6_macro_fii'] = 0
    verified_status.append(True)

    # 7. Gift Nifty & NIFTY50
    gift = market.get('gift_nifty_pct', 0.0)
    nifty = market.get('nifty50_pct', 0.0)
    s['r7_market_trend'] = 1 if (gift > 0 and nifty > 0) else (-1 if (gift < 0 and nifty < 0) else 0)
    verified_status.append(market.get('macro_fresh', True))

    # 8. Nifty 50 Proximity to Zones (1% Limit)
    nifty_ltp = market.get('nifty_ltp', 0.0)
    sup = market.get('nifty_nearest_supply', float('inf'))
    dem = market.get('nifty_nearest_demand', 0.0)
    sup_within_1pct = (sup <= nifty_ltp * 1.01)
    dem_within_1pct = (dem >= nifty_ltp * 0.99)
    if not sup_within_1pct and dem_within_1pct:
        s['r8_nifty_zones'] = 1
    elif not dem_within_1pct and sup_within_1pct:
        s['r8_nifty_zones'] = -1
    else:
        s['r8_nifty_zones'] = 0
    verified_status.append(True)

    # 9. All Futures Stocks Market Breadth
    green = market.get('breadth_advances', 0)
    red = market.get('breadth_declines', 0)
    tot = green + red
    s['r9_breadth'] = 1 if tot > 0 and (green / tot > 0.5) else (-1 if tot > 0 and (red / tot > 0.5) else 0)
    verified_status.append(True)

    # 10. Volume vs 5-Day Average
    vol = stock.get('current_volume', 0)
    avg_vol = stock.get('avg_5d_volume', 0)
    s['r10_vol_5d'] = 1 if vol > avg_vol else (-1 if vol < avg_vol else 0)
    verified_status.append(stock.get('volume_fresh', True))

    total = sum(s.values())
    is_verified = all(verified_status)

    return {
        "score": total,
        "details": s,
        "data_status": "Verified" if is_verified else "Unverified"
    }
