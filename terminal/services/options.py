"""Normalize an actual Dhan option-chain payload and calculate scoped PCR."""
from __future__ import annotations

from typing import Any

import math


def normalize_option_chain(
    response: dict[str, Any],
    symbol: str,
    expiry: str,
    pcr_scope: str = "selected_atm_5_each_side",
) -> dict[str, Any]:
    data = response.get("data", {}) if isinstance(response, dict) else {}
    chain = data.get("oc", {}) if isinstance(data, dict) else {}
    try:
        spot = float(data.get("last_price"))
    except (TypeError, ValueError):
        spot = None
    if not isinstance(chain, dict) or not chain or spot is None:
        return {"status": "UNAVAILABLE", "symbol": symbol, "expiry": expiry,
                "reason": "Dhan option-chain response did not include strike data and underlying last price."}

    parsed: list[tuple[float, dict[str, Any]]] = []
    for strike_text, sides in chain.items():
        try:
            strike = float(strike_text)
        except (TypeError, ValueError):
            continue
        if isinstance(sides, dict):
            parsed.append((strike, sides))
    parsed.sort(key=lambda item: item[0])
    if not parsed:
        return {"status": "UNAVAILABLE", "symbol": symbol, "expiry": expiry,
                "reason": "No valid listed strikes were returned by the provider."}

    atm_index = min(range(len(parsed)), key=lambda idx: abs(parsed[idx][0] - spot))
    selected_start = max(0, atm_index - 5)
    selected_end = min(len(parsed), atm_index + 6)
    selected = parsed[selected_start:selected_end]
    strike_rows = [_strike_row(strike, sides, spot, expiry) for strike, sides in selected]
    full_rows = [_strike_row(strike, sides, spot, expiry) for strike, sides in parsed]

    if pcr_scope == "all_strikes_selected_expiry":
        scoped_rows = full_rows
        scope_label = "All provider-returned strikes in the selected expiry"
    elif pcr_scope == "selected_atm_5_each_side":
        scoped_rows = strike_rows
        scope_label = "Actual listed ATM ± up to 5 strikes on each side"
    else:
        return {"status": "UNAVAILABLE", "symbol": symbol, "expiry": expiry,
                "reason": "Unsupported PCR calculation scope."}

    ce_oi = sum(row["ce_oi"] or 0 for row in scoped_rows)
    pe_oi = sum(row["pe_oi"] or 0 for row in scoped_rows)
    pcr = (pe_oi / ce_oi) if ce_oi else None
    return {
        "status": "AVAILABLE",
        "symbol": symbol,
        "expiry": expiry,
        "spot": spot,
        "atm_strike": parsed[atm_index][0],
        "selected_strike_count": len(strike_rows),
        "total_available_strikes": len(full_rows),
        "pcr": pcr,
        "pcr_scope": pcr_scope,
        "pcr_scope_label": scope_label,
        "ce_total_oi": ce_oi,
        "pe_total_oi": pe_oi,
        "strikes": strike_rows,
        "source": "DhanHQ option-chain response",
        "previous_oi_semantics": "Dhan previous_oi field; change is shown against provider-reported previous_oi, not assumed intraday delta.",
    }


def _strike_row(strike: float, sides: dict[str, Any], spot: float, expiry: str) -> dict[str, Any]:
    ce = sides.get("ce") or {}
    pe = sides.get("pe") or {}
    ce_oi = _num(ce.get("oi"))
    pe_oi = _num(pe.get("oi"))
    ce_prev = _num(ce.get("previous_oi"))
    pe_prev = _num(pe.get("previous_oi"))
    return {
        "strike": strike,
        "expiry": expiry,
        "distance_from_spot": strike - spot,
        "distance_pct": ((strike - spot) / spot * 100) if spot else None,
        "ce_oi": ce_oi,
        "ce_oi_change_vs_previous_oi": (ce_oi - ce_prev) if ce_oi is not None and ce_prev is not None else None,
        "ce_volume": _num(ce.get("volume")),
        "ce_ltp": _num(ce.get("last_price")),
        "pe_oi": pe_oi,
        "pe_oi_change_vs_previous_oi": (pe_oi - pe_prev) if pe_oi is not None and pe_prev is not None else None,
        "pe_volume": _num(pe.get("volume")),
        "pe_ltp": _num(pe.get("last_price")),
        "ce_security_id": ce.get("security_id"),
        "pe_security_id": pe.get("security_id"),
    }


def _num(value: Any) -> float | None:
    try:
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None
