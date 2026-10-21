"""
Institutional Signal Engine with 10-Point AI Hypothesis Scoring System (-10 to +10)
"""

from typing import Dict, Any, List

def evaluate_mtf_rule1(mtf_data: Dict[str, Dict[str, Any]]) -> int:
    """
    नियम 1:
    Lower (15m, 30m), Intermediate (1h, 2h), Higher (4h, 6h, 1d, 1w, 1M)
    प्रत्येक समूह में EMA35 > EMA100, LTP > VWAP और Volume Spike हो।
    तीनों समूह Bullish हों तो +1, तीनों Bearish हों तो -1, अन्यथा 0।
    """
    def check_group(tf_list: List[str]) -> int:
        bull, bear, count = 0, 0, 0
        for tf in tf_list:
            if tf in mtf_data:
                d = mtf_data[tf]
                count += 1
                ema35 = d.get("ema35", 0.0)
                ema100 = d.get("ema100", 0.0)
                ltp = d.get("ltp", 0.0)
                vwap = d.get("vwap", 0.0)
                vol_spike = d.get("volume_spike", False)

                if (ema35 > ema100) and (ltp > vwap) and vol_spike:
                    bull += 1
                elif (ema35 < ema100) and (ltp < vwap) and vol_spike:
                    bear += 1

        if count > 0 and bull == count:
            return 1
        if count > 0 and bear == count:
            return -1
        return 0

    lower_dir = check_group(["15m", "30m"])
    inter_dir = check_group(["1h", "2h"])
    higher_dir = check_group(["4h", "6h", "1d", "1w", "1M"])

    if lower_dir == 1 and inter_dir == 1 and higher_dir == 1:
        return 1
    elif lower_dir == -1 and inter_dir == -1 and higher_dir == -1:
        return -1
    return 0

def calculate_ai_hypothesis_score(stock: Dict[str, Any], market: Dict[str, Any]) -> Dict[str, Any]:
    """
    10-Point AI Hypothesis Calculator (पूरा 10 में से 10 स्कोर)
    """
    scores = {}
    verified_list = []

    # 1. MTF EMA35, EMA100, VWAP और Volume Spike
    scores['r1_mtf'] = evaluate_mtf_rule1(stock.get('mtf_indicators', {}))
    verified_list.append(stock.get('indicators_fresh', True))

    # 2. Spot LTP vs Futures LTP
    spot = stock.get('spot_ltp', 0.0)
    fut = stock.get('futures_ltp', 0.0)
    if fut > spot:
        scores['r2_fut_premium'] = 1
    elif fut < spot:
        scores['r2_fut_premium'] = -1
    else:
        scores['r2_fut_premium'] = 0
    verified_list.append(stock.get('price_fresh', True))

    # 3. Delivery % vs Previous Day
    deliv_curr = stock.get('delivery_pct_curr', 0.0)
    deliv_prev = stock.get('delivery_pct_prev', 0.0)
    if deliv_curr > deliv_prev:
        scores['r3_delivery'] = 1
    elif deliv_curr < deliv_prev:
        scores['r3_delivery'] = -1
    else:
        scores['r3_delivery'] = 0
    verified_list.append(True)

    # 4. PCR (Put-Call Ratio)
    pcr = stock.get('pcr', 1.0)
    if pcr > 1.5:
        scores['r4_pcr'] = 1
    elif pcr < 1.0:
        scores['r4_pcr'] = -1
    else:
        scores['r4_pcr'] = 0
    verified_list.append(stock.get('oi_fresh', True))

    # 5. Buildup Classification
    buildup = str(stock.get('buildup', '')).lower()
    if 'long buildup' in buildup:
        scores['r5_buildup'] = 1
    elif 'short buildup' in buildup:
        scores['r5_buildup'] = -1
    else:
        scores['r5_buildup'] = 0
    verified_list.append(stock.get('oi_fresh', True))

    # 6. Global & Macro FII Inflow + Stock News Sentiment
    fii_inflow = stock.get('fii_inflow', False)
    sentiment = stock.get('news_sentiment', 'neutral').lower()
    if fii_inflow and sentiment in ['positive', 'neutral']:
        scores['r6_macro_fii'] = 1
    elif not fii_inflow and sentiment in ['negative', 'neutral']:
        scores['r6_macro_fii'] = -1
    else:
        scores['r6_macro_fii'] = 0
    verified_list.append(True)

    # 7. Gift Nifty & NIFTY50 Positive/Negative
    gift_pct = market.get('gift_nifty_pct', 0.0)
    nifty_pct = market.get('nifty50_pct', 0.0)
    if gift_pct > 0 and nifty_pct > 0:
        scores['r7_market_trend'] = 1
    elif gift_pct < 0 and nifty_pct < 0:
        scores['r7_market_trend'] = -1
    else:
        scores['r7_market_trend'] = 0
    verified_list.append(market.get('macro_fresh', True))

    # 8. Nifty50 LTP Proximity to Supply/Demand (1% Band)
    nifty_ltp = market.get('nifty_ltp', 0.0)
    sup = market.get('nifty_nearest_supply', float('inf'))
    dem = market.get('nifty_nearest_demand', 0.0)
    sup_within_1pct = (sup <= nifty_ltp * 1.01)
    dem_within_1pct = (dem >= nifty_ltp * 0.99)
    if not sup_within_1pct and dem_within_1pct:
        scores['r8_nifty_zones'] = 1
    elif not dem_within_1pct and sup_within_1pct:
        scores['r8_nifty_zones'] = -1
    else:
        scores['r8_nifty_zones'] = 0
    verified_list.append(True)

    # 9. NSE All Futures Market Breadth (>50% Green/Red)
    advances = market.get('breadth_advances', 0)
    declines = market.get('breadth_declines', 0)
    tot = advances + declines
    if tot > 0:
        if (advances / tot) > 0.50:
            scores['r9_breadth'] = 1
        elif (declines / tot) > 0.50:
            scores['r9_breadth'] = -1
        else:
            scores['r9_breadth'] = 0
    else:
        scores['r9_breadth'] = 0
    verified_list.append(True)

    # 10. Volume vs 5-Day Average Volume
    curr_vol = stock.get('current_volume', 0)
    avg_5d_vol = stock.get('avg_5d_volume', 0)
    if curr_vol > avg_5d_vol:
        scores['r10_vol_5d'] = 1
    elif curr_vol < avg_5d_vol:
        scores['r10_vol_5d'] = -1
    else:
        scores['r10_vol_5d'] = 0
    verified_list.append(stock.get('volume_fresh', True))

    total_score = sum(scores.values())
    is_verified = all(verified_list)

    return {
        "total_score": total_score,
        "details": scores,
        "data_status": "Verified" if is_verified else "Unverified"
    }
