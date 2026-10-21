"""
Market data services, breadth calculation, and freshness validation logic.
"""

from datetime import datetime, time
from typing import Dict, Any, List
import pytz

IST = pytz.timezone("Asia/Kolkata")

def is_market_open(now_ist: datetime = None) -> bool:
    """
    जाँचें कि भारतीय शेयर बाजार (NSE/BSE) खुला है या बंद।
    शनिवार और रविवार को बाजार बंद रहता है।
    ट्रेडिंग घंटे: सुबह 09:15 से दोपहर 03:30 IST तक।
    """
    if now_ist is None:
        now_ist = datetime.now(IST)
    
    # शनिवार (5) और रविवार (6)
    if now_ist.weekday() >= 5:
        return False
        
    market_start = time(9, 15)
    market_end = time(15, 30)
    current_time = now_ist.time()
    return market_start <= current_time <= market_end

def check_data_freshness(timestamp: datetime, data_type: str = "quote") -> bool:
    """
    15-मिनट डेटा सत्यापन नियम:
    1. EOD, Delivery %, FII/DII, Corporate Action, Macro / RBI Events दिन में एक बार मिलते हैं, 
       इसलिए इन पर 15-मिनट की कोई सीमा लागू नहीं होगी (सदैव Fresh)।
    2. यदि मार्केट बंद है, तो अंतिम ट्रेडिंग सत्र के डेटा (Last Close) को फ्रेश माना जाएगा।
    3. केवल लाइव मार्केट के दौरान 15 मिनट से पुराना डेटा 'Unverified' होगा।
    """
    exempt_types = [
        "eod", "delivery", "fii_dii", "macro_events", 
        "corporate_action", "global_instruments", "rbi_events"
    ]
    if data_type.lower() in exempt_types:
        return True

    now_ist = datetime.now(IST)
    if not is_market_open(now_ist):
        return True  # बाजार बंद रहने पर अंतिम डेटा पूर्ण रूप से मान्य है

    if timestamp is None:
        return False

    if timestamp.tzinfo is None:
        timestamp = IST.localize(timestamp)
    else:
        timestamp = timestamp.astimezone(IST)

    delta_seconds = (now_ist - timestamp).total_seconds()
    return delta_seconds <= (15 * 60)

def calculate_market_breadth(stock_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    213 NSE Futures स्टॉक्स का मार्केट ब्रेथ सारांश।
    """
    advances = 0
    declines = 0
    unchanged = 0
    total_volume = 0
    
    for s in stock_records:
        chg = s.get("change_pct", s.get("spot_change_pct", 0.0))
        vol = s.get("volume", s.get("current_volume", 0))
        total_volume += vol
        if chg > 0:
            advances += 1
        elif chg < 0:
            declines += 1
        else:
            unchanged += 1

    total = advances + declines + unchanged
    return {
        "total_stocks": total,
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "advance_pct": round((advances / total) * 100, 2) if total > 0 else 0.0,
        "decline_pct": round((declines / total) * 100, 2) if total > 0 else 0.0,
        "total_volume": total_volume,
        "timestamp": datetime.now(IST)
    }
