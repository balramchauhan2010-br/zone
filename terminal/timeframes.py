"""
Timeframe constants and groupings for AI Pro Institutional Trading Terminal
"""

# डिफ़ॉल्ट टाइमफ्रेम जो सीधे स्कैन होंगे
DEFAULT_TIMEFRAMES = [
    "15m", "30m", "75m", "1h", "2h", "4h", "6h", "1d", "1w", "1M"
]

# वैकल्पिक टाइमफ्रेम
OPTIONAL_TIMEFRAMES = [
    "3m", "5m", "10m"
]

ALL_AVAILABLE_TIMEFRAMES = OPTIONAL_TIMEFRAMES + DEFAULT_TIMEFRAMES

# मल्टी-टाइमफ्रेम (Rule 1) के लिए समूह
MTF_INDICATOR_GROUPS = {
    "lower": ["15m", "30m"],
    "intermediate": ["1h", "2h"],
    "higher": ["4h", "6h", "1d", "1w", "1M"]
}
