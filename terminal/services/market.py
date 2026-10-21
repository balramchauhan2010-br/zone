"""
Market data freshness, market hours check and institutional data helpers.
"""

from datetime import datetime, time
import pytz

IST = pytz.timezone("Asia/Kolkata")

def is_market_open(now_ist: datetime = None) -> bool:
    """जाँचें कि भारतीय बाजार खुला है या बंद"""
    if now_ist is None:
        now_ist = datetime.now(IST)
    if now_ist.weekday() >= 5:  # शनिवार और रविवार बंद
        return False
    market_start = time(9, 15)
    market_end = time(15, 30)
    return market_start <= now_ist.time() <= market_end

def check_data_freshness(timestamp: datetime, data_type: str = "quote") -> bool:
    """
    डेटा सत्यापन नियम:
    - EOD डेटा, Delivery %, FII/DII, Corporate actions, Global events पर 15m का बंधन नहीं होगा।
    - मार्केट बंद रहने पर अंतिम क्लोजिंग डेटा हमेशा Verified (Fresh) रहेगा।
    - केवल लाइव मार्केट घंटों में 15 मिनट से पुराना डेटा Unverified होगा।
    """
    exempt_types = [
        "eod", "delivery", "fii_dii", "macro_events", 
        "corporate_action", "global_instruments", "rbi_events"
    ]
    if data_type.lower() in exempt_types:
        return True

    now_ist = datetime.now(IST)
    if not is_market_open(now_ist):
        return True  # बाजार बंद होने पर अंतिम डेटा मान्य है

    if timestamp is None:
        return False

    if timestamp.tzinfo is None:
        timestamp = IST.localize(timestamp)
    else:
        timestamp = timestamp.astimezone(IST)

    delta_seconds = (now_ist - timestamp).total_seconds()
    return delta_seconds <= (15 * 60)
