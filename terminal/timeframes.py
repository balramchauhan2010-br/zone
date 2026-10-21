"""
Timeframe constants and groupings for AI Pro Institutional Trading Terminal
"""

DEFAULT_TIMEFRAMES = [
    "15m", "30m", "75m", "1h", "2h", "4h", "6h", "1d", "1w", "1M"
]

OPTIONAL_TIMEFRAMES = [
    "3m", "5m", "10m"
]

ALL_AVAILABLE_TIMEFRAMES = OPTIONAL_TIMEFRAMES + DEFAULT_TIMEFRAMES

# Multi-Timeframe Scoring Groups (नियम 1 के लिए)
MTF_INDICATOR_GROUPS = {
    "lower": ["15m", "30m"],
    "intermediate": ["1h", "2h"],
    "higher": ["4h", "6h", "1d", "1w", "1M"]
}
