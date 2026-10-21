"""
Timeframe constants, parsing helpers, and institutional grouping for AI Pro Institutional Trading Terminal.
"""

from typing import List

# 1. सभी टाइमफ्रेम कॉन्स्टेंट्स (3m से लेकर 1M तक)
DEFAULT_TIMEFRAMES: List[str] = [
    "15m", "30m", "75m", "1h", "2h", "4h", "6h", "1d", "1w", "1M"
]

OPTIONAL_TIMEFRAMES: List[str] = [
    "3m", "5m", "10m"
]

ALL_AVAILABLE_TIMEFRAMES: List[str] = OPTIONAL_TIMEFRAMES + DEFAULT_TIMEFRAMES

# 2. मल्टी-टाइमफ्रेम नियम 1 के लिए ग्रुपिंग
MTF_INDICATOR_GROUPS = {
    "lower": ["15m", "30m"],
    "intermediate": ["1h", "2h"],
    "higher": ["4h", "6h", "1d", "1w", "1M"]
}

# 3. टाइमफ्रेम को मिनटों में बदलने का हेल्पर (कैंडल और एग्रीगेशन के लिए)
TIMEFRAME_MINUTES_MAP = {
    "3m": 3,
    "5m": 5,
    "10m": 10,
    "15m": 15,
    "30m": 30,
    "75m": 75,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "6h": 360,
    "1d": 1440,
    "1w": 10080,
    "1M": 43200
}

def timeframe_to_minutes(tf: str) -> int:
    """दिए गए टाइमफ्रेम स्ट्रिंग को मिनटों (Minutes) में बदलता है।"""
    return TIMEFRAME_MINUTES_MAP.get(tf, 15)

def normalize_timeframe(tf: str) -> str:
    """टाइमफ्रेम स्ट्रिंग को स्टैंडर्ड प्रारूप में सामान्य करता है।"""
    t = tf.strip().lower()
    if t in ["d", "1d", "day", "daily"]:
        return "1d"
    if t in ["w", "1w", "week", "weekly"]:
        return "1w"
    if t in ["m", "1m", "month", "monthly"]:
        return "1M"
    if t in ["h", "1h", "hour", "60m"]:
        return "1h"
    return t
