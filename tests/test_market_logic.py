from terminal.services.options import normalize_option_chain
from terminal.services.signals import classify_price_oi, score_setup


def test_oi_buildup_names_are_potential_not_claimed_as_participant_intent():
    cases = [
        (1, 2, "POTENTIAL_LONG_BUILDUP"),
        (-1, 2, "POTENTIAL_SHORT_BUILDUP"),
        (1, -2, "POTENTIAL_SHORT_COVERING"),
        (-1, -2, "POTENTIAL_LONG_UNWINDING"),
    ]
    for price, oi, expected in cases:
        result = classify_price_oi(price, oi, 60)
        assert result["classification"] == expected
        assert "does not identify" in result["reason"]


def test_oi_classification_refuses_mismatched_contract_or_missing_interval():
    assert classify_price_oi(1, 2, 60, same_contract=False)["classification"] == "UNKNOWN"
    assert classify_price_oi(1, 2, None)["classification"] == "UNKNOWN"


def test_rule_engine_blocks_signal_when_fno_ban_is_not_verified():
    features = {
        "higher_timeframe_structure": 1,
        "intraday_structure": 1,
        "zone_quality": 1,
        "entry_confirmation": 1,
        "risk_reward": 1,
    }
    result = score_setup(features, eligibility_status="ELIGIBLE_VERIFIED",
                         fno_ban_status="UNAVAILABLE", risk_reward_ratio=2.0)
    assert result["final_bias"] == "NO TRADE"
    assert result["risk_status"] == "BLOCKED"
    assert any("F&O ban" in reason for reason in result["hard_filters"])
    assert "probability" in result["signal_explanation"].lower()


def test_option_chain_uses_real_returned_strikes_and_explicit_pcr_scope():
    response = {
        "data": {
            "last_price": 100,
            "oc": {
                str(strike): {
                    "ce": {"oi": strike, "previous_oi": strike - 1, "volume": 5, "security_id": int(strike)},
                    "pe": {"oi": strike * (3 if strike == 160 else 2), "previous_oi": strike * (3 if strike == 160 else 2) - 1, "volume": 7, "security_id": int(strike + 1000)},
                }
                for strike in [50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160]
            },
        }
    }
    selected = normalize_option_chain(response, "TEST", "2026-10-29", "selected_atm_5_each_side")
    all_strikes = normalize_option_chain(response, "TEST", "2026-10-29", "all_strikes_selected_expiry")
    assert selected["status"] == "AVAILABLE"
    assert selected["atm_strike"] == 100
    assert len(selected["strikes"]) == 11
    assert selected["pcr_scope"] == "selected_atm_5_each_side"
    assert "ATM" in selected["pcr_scope_label"]
    assert all_strikes["pcr_scope"] == "all_strikes_selected_expiry"
    assert all_strikes["total_available_strikes"] == 12
    assert all_strikes["pcr"] != selected["pcr"]
