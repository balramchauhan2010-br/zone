from datetime import datetime, time
import pytz

IST = pytz.timezone("Asia/Kolkata")

def is_market_open(now_ist: datetime = None) -> bool:
    """जाँचें कि भारतीय शेयर बाजार खुला है या बंद"""
    if now_ist is None:
        now_ist = datetime.now(IST)
    # शनिवार (5) और रविवार (6)
    if now_ist.weekday() >= 5:
        return False
    market_start = time(9, 15)
    market_end = time(15, 30)
    return market_start <= now_ist.time() <= market_end

def check_data_freshness(timestamp: datetime, data_type: str = "quote") -> bool:
    """
    नियम:
    1. EOD, Delivery %, FII/DII, Corporate/RBI Events दिन में 1 बार आते हैं, इसलिए ये हमेशा Fresh हैं।
    2. मार्केट बंद होने के दौरान अंतिम क्लोजिंग डेटा हमेशा Verified (Fresh) माना जाएगा।
    3. केवल लाइव मार्केट के दौरान 15 मिनट से पुराना डेटा Unverified होगा।
    """
    if data_type in ["eod", "delivery", "fii_dii", "macro_events", "corporate_action"]:
        return True
    
    now_ist = datetime.now(IST)
    if not is_market_open(now_ist):
        return True  # मार्केट बंद होने पर डेटा फ्रेश माना जाएगा
    
    if timestamp is None:
        return False

    if timestamp.tzinfo is None:
        timestamp = IST.localize(timestamp)
    else:
        timestamp = timestamp.astimezone(IST)

    delta_seconds = (now_ist - timestamp).total_seconds()
    return delta_seconds <= (15 * 60)
