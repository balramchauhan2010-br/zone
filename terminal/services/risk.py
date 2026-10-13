"""Non-executing position-risk arithmetic; no margin or order placement."""
from __future__ import annotations

import math
from typing import Any


def estimate_futures_position(account_capital: float, risk_pct: float,
                              entry_reference: float, stop_reference: float,
                              lot_size: float | int | None) -> dict[str, Any]:
    try:
        capital = float(account_capital)
        risk = float(risk_pct)
        entry = float(entry_reference)
        stop = float(stop_reference)
        lot = float(lot_size) if lot_size is not None else 0.0
    except (TypeError, ValueError):
        return {"status": "UNAVAILABLE", "reason": "Position-risk inputs are missing or invalid."}
    if capital <= 0 or risk <= 0 or risk > 100 or entry <= 0 or stop <= 0 or lot <= 0:
        return {"status": "UNAVAILABLE", "reason": "Capital, risk %, entry, stop and lot size must be valid positive values."}
    per_unit_risk = abs(entry - stop)
    if per_unit_risk <= 0:
        return {"status": "UNAVAILABLE", "reason": "Entry and stop reference are equal."}
    risk_budget = capital * risk / 100.0
    risk_per_lot = per_unit_risk * lot
    lots = math.floor(risk_budget / risk_per_lot)
    quantity = lots * int(lot) if float(lot).is_integer() else lots * lot
    return {
        "status": "ESTIMATE_ONLY" if lots >= 1 else "RISK_BUDGET_BELOW_ONE_LOT",
        "account_capital": capital,
        "risk_pct": risk,
        "risk_budget": risk_budget,
        "entry_reference": entry,
        "stop_reference": stop,
        "risk_per_unit": per_unit_risk,
        "lot_size": lot,
        "risk_per_lot": risk_per_lot,
        "suggested_lots": lots,
        "suggested_quantity": quantity,
        "assumptions": "Reference prices only. Excludes brokerage, taxes, slippage, margin, gaps and contract-specific adjustments. Not an order instruction.",
    }
