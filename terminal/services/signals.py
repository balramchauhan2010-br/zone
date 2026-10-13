"""Transparent rule-based scoring; scores are not predictive probabilities."""
from __future__ import annotations

from typing import Any

FEATURE_WEIGHTS: dict[str, int] = {
    "higher_timeframe_structure": 12,
    "intraday_structure": 10,
    "zone_quality": 12,
    "entry_confirmation": 10,
    "volume_expansion": 8,
    "delivery_context": 5,
    "futures_oi_behavior": 8,
    "options_context": 5,
    "ema_alignment": 7,
    "vwap_alignment": 5,
    "sector_relative_strength": 4,
    "nifty_context": 4,
    "global_macro": 3,
    "news_event_risk": 3,
    "risk_reward": 4,
}

REQUIRED_FOR_ENTRY = (
    "higher_timeframe_structure", "intraday_structure", "zone_quality",
    "entry_confirmation", "risk_reward",
)


def classify_price_oi(price_change: float | None, oi_change: float | None,
                      observation_interval_seconds: float | None,
                      same_contract: bool = True) -> dict[str, Any]:
    if not same_contract:
        return {"classification": "UNKNOWN", "reason": "Contract rollover/mismatch"}
    if price_change is None or oi_change is None or not observation_interval_seconds or observation_interval_seconds <= 0:
        return {"classification": "UNKNOWN", "reason": "Synchronized price/OI observations are not available"}
    if price_change > 0 and oi_change > 0:
        label = "POTENTIAL_LONG_BUILDUP"
    elif price_change < 0 and oi_change > 0:
        label = "POTENTIAL_SHORT_BUILDUP"
    elif price_change > 0 and oi_change < 0:
        label = "POTENTIAL_SHORT_COVERING"
    elif price_change < 0 and oi_change < 0:
        label = "POTENTIAL_LONG_UNWINDING"
    else:
        label = "NEUTRAL"
    return {
        "classification": label,
        "reason": "Descriptive price/OI behavior only; it does not identify market participants.",
        "observation_interval_seconds": float(observation_interval_seconds),
    }


def score_setup(
    features: dict[str, int | float | None],
    *,
    eligibility_status: str = "UNKNOWN",
    fno_ban_status: str = "UNAVAILABLE",
    setup_invalidated: bool = False,
    risk_reward_ratio: float | None = None,
    minimum_risk_reward: float = 1.5,
    weights: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Score evidence directionally and fail closed on missing hard requirements.

    Feature values must be +1 bullish, -1 bearish, 0 neutral, or None unknown.
    Returned scores summarize supplied evidence; they are not probabilities.
    """
    chosen_weights = {**FEATURE_WEIGHTS, **(weights or {})}
    bullish = 0.0
    bearish = 0.0
    known_weight = 0
    total_weight = sum(max(0, int(w)) for w in chosen_weights.values()) or 1
    evidence: list[str] = []
    missing: list[str] = []
    for name, weight in chosen_weights.items():
        value = features.get(name)
        if value not in (-1, 0, 1, -1.0, 0.0, 1.0):
            missing.append(name)
            continue
        known_weight += max(0, int(weight))
        if value > 0:
            bullish += max(0, int(weight))
            evidence.append(f"{name}: bullish evidence")
        elif value < 0:
            bearish += max(0, int(weight))
            evidence.append(f"{name}: bearish evidence")
        else:
            evidence.append(f"{name}: neutral/mixed")

    quality = round(known_weight / total_weight * 100)
    required_missing = [name for name in REQUIRED_FOR_ENTRY if features.get(name) not in (-1, 0, 1, -1.0, 0.0, 1.0)]
    blockers: list[str] = []
    if eligibility_status != "ELIGIBLE_VERIFIED":
        blockers.append("Futures eligibility has not been verified against the configured official source")
    if fno_ban_status != "CLEAR_VERIFIED":
        blockers.append("F&O ban status is not verified clear")
    if setup_invalidated:
        blockers.append("Setup invalidation level has been breached")
    if required_missing:
        blockers.append("Required evidence missing: " + ", ".join(required_missing))
    if risk_reward_ratio is None:
        blockers.append("Risk/Reward is unavailable")
    elif risk_reward_ratio < minimum_risk_reward:
        blockers.append(f"Risk/Reward {risk_reward_ratio:.2f} is below configured minimum {minimum_risk_reward:.2f}")

    net = bullish - bearish
    final = "NO TRADE"
    if not blockers:
        if bullish >= 75 and net >= 25:
            final = "STRONG BUY"
        elif bullish >= 55 and net >= 15:
            final = "BUY"
        elif bearish >= 75 and net <= -25:
            final = "STRONG SELL"
        elif bearish >= 55 and net <= -15:
            final = "SELL"
        else:
            final = "HOLD"

    return {
        "bullish_score": round(bullish, 1),
        "bearish_score": round(bearish, 1),
        "setup_quality_score": round(max(bullish, bearish), 1),
        "data_quality_score": quality,
        "data_quality_definition": "Weighted completeness of configured directional evidence; not confidence or probability.",
        "final_bias": final,
        "risk_status": "BLOCKED" if blockers else "CHECKED",
        "hard_filters": blockers,
        "missing_features": missing,
        "why_buy": [item for item in evidence if "bullish" in item],
        "why_sell": [item for item in evidence if "bearish" in item],
        "what_confirms": [item for item in evidence if "bullish" in item or "bearish" in item],
        "what_invalidates": "Configured zone/structure invalidation; no level is generated when source data is missing.",
        "main_risk": blockers[0] if blockers else "Market regime and gap risk remain; monitor source freshness.",
        "signal_explanation": "Transparent weighted rule score. This is not a calibrated probability and makes no profit guarantee.",
    }
