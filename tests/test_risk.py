from terminal.services.risk import estimate_futures_position


def test_position_size_rounds_down_to_whole_futures_lots():
    result = estimate_futures_position(100000, 0.5, 100, 95, 20)
    assert result["status"] == "ESTIMATE_ONLY"
    assert result["risk_budget"] == 500
    assert result["risk_per_lot"] == 100
    assert result["suggested_lots"] == 5
    assert result["suggested_quantity"] == 100
    assert "Not an order instruction" in result["assumptions"]


def test_position_size_blocks_when_risk_budget_is_below_one_lot():
    result = estimate_futures_position(10000, 0.1, 1000, 990, 100)
    assert result["status"] == "RISK_BUDGET_BELOW_ONE_LOT"
    assert result["suggested_lots"] == 0
    assert result["suggested_quantity"] == 0


def test_position_size_returns_unavailable_for_bad_inputs():
    assert estimate_futures_position(0, 0.5, 100, 90, 1)["status"] == "UNAVAILABLE"
    assert estimate_futures_position(10000, 0.5, 100, 100, 1)["status"] == "UNAVAILABLE"
