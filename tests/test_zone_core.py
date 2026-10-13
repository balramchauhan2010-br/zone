from datetime import date

import pandas as pd

from terminal.services.instruments import build_nse_futures_universe
from terminal.zone_core import Box, Zone, ZoneEngine, latest_active_zones, settings


def make_zone(is_demand=True, state="Fresh", prox=100.0, dist=90.0, touches=0):
    return Zone(
        proxVal=prox, distVal=dist, slVal=dist - 1 if is_demand else dist + 1,
        tpVal=prox + 10 if is_demand else prox - 10, isDemand=is_demand,
        isHQ=False, densityScore=50, patternType="RBR" if is_demand else "DBD",
        zoneCategory="Continuation", state=state, touchCount=touches,
        startBarIndex=0, createdBarIndex=0, baseCount=1, legOutHigh=105,
        legOutLow=95, legOutMidLevel=100, isOvernight=False, legInTR=2,
        legOutTR=3, zoneBox=Box(0, prox, 4, dist, "green", "green"),
    )


def engine_for_bar(low, high, zone):
    engine = ZoneEngine.__new__(ZoneEngine)
    engine.l = [95.0, low]
    engine.h = [105.0, high]
    engine.df = pd.DataFrame(index=pd.date_range("2026-10-12 09:15", periods=2, freq="1min", tz="Asia/Kolkata"))
    engine.live_zones = [zone]
    engine.maxTestedCount = 1
    return engine


def test_demand_fresh_transitions_to_tested_on_proximal_touch():
    zone = make_zone(is_demand=True, prox=100, dist=90)
    engine = engine_for_bar(low=99, high=101, zone=zone)
    engine._update_states(1)
    assert zone.state == "Tested"
    assert zone.touchCount == 1
    assert zone.entryBarIndex == 1


def test_demand_tested_stays_tested_after_more_than_max_test_count_until_distal_break():
    zone = make_zone(is_demand=True, state="Tested", prox=100, dist=90, touches=1)
    engine = engine_for_bar(low=98, high=101, zone=zone)
    engine._update_states(1)
    assert zone.state == "Tested"
    assert zone.touchCount == 2


def test_demand_distal_break_before_proximal_is_broken():
    zone = make_zone(is_demand=True, prox=100, dist=90)
    engine = engine_for_bar(low=89, high=101, zone=zone)
    engine._update_states(1)
    assert zone.state == "Broken"
    assert zone.breakReason == "distal_break_before_test"
    assert not engine.live_zones


def test_supply_zone_breaks_only_when_distal_is_crossed():
    zone = make_zone(is_demand=False, state="Tested", prox=100, dist=110, touches=1)
    engine = engine_for_bar(low=99, high=101, zone=zone)
    engine._update_states(1)
    assert zone.state == "Tested"
    zone2 = make_zone(is_demand=False, state="Tested", prox=100, dist=110, touches=1)
    engine2 = engine_for_bar(low=99, high=111, zone=zone2)
    engine2._update_states(1)
    assert zone2.state == "Broken"
    assert zone2.breakReason == "distal_break"


def test_eod_range_buffers_are_configurable_and_defaults_are_ten_percent():
    defaults = settings()
    assert defaults["eodHighBufferPct"] == 10.0
    assert defaults["eodLowBufferPct"] == 10.0
    assert settings(eodHighBufferPct=15, eodLowBufferPct=8)["eodHighBufferPct"] == 15
    assert settings(eodHighBufferPct=15, eodLowBufferPct=8)["eodLowBufferPct"] == 8


def test_latest_active_zones_only_returns_fresh_or_tested():
    fresh = make_zone(state="Fresh")
    tested = make_zone(state="Tested")
    broken = make_zone(state="Broken")
    assert latest_active_zones([fresh, tested, broken]) == [fresh, tested]


def test_dynamic_futures_master_selection_handles_next_contract_and_hyphen_symbol():
    master = pd.DataFrame([
        {"EXCH_ID": "NSE", "SEGMENT": "E", "SECURITY_ID": "100", "INSTRUMENT": "EQUITY",
         "SYMBOL_NAME": "M&M", "DISPLAY_NAME": "Mahindra & Mahindra", "SERIES": "EQ"},
        {"EXCH_ID": "NSE", "SEGMENT": "D", "SECURITY_ID": "201", "INSTRUMENT": "FUTSTK",
         "UNDERLYING_SYMBOL": "M&M", "SYMBOL_NAME": "M&M-Nov2026-FUT", "DISPLAY_NAME": "M&M Nov FUT",
         "SM_EXPIRY_DATE": "2026-11-26", "LOT_SIZE": "100", "TICK_SIZE": "0.05", "BUY_SELL_INDICATOR": "A"},
        {"EXCH_ID": "NSE", "SEGMENT": "D", "SECURITY_ID": "202", "INSTRUMENT": "FUTSTK",
         "UNDERLYING_SYMBOL": "M&M", "SYMBOL_NAME": "M&M-Dec2026-FUT", "DISPLAY_NAME": "M&M Dec FUT",
         "SM_EXPIRY_DATE": "2026-12-31", "LOT_SIZE": "100", "TICK_SIZE": "0.05", "BUY_SELL_INDICATOR": "A"},
        {"EXCH_ID": "NSE", "SEGMENT": "D", "SECURITY_ID": "999", "INSTRUMENT": "FUTSTK",
         "UNDERLYING_SYMBOL": "NSETEST", "SYMBOL_NAME": "NSETEST-Nov2026-FUT", "DISPLAY_NAME": "Test",
         "SM_EXPIRY_DATE": "2026-11-26", "LOT_SIZE": "1", "TICK_SIZE": "0.05", "BUY_SELL_INDICATOR": "A"},
    ])
    front = build_nse_futures_universe(master, contract="front", as_of=date(2026, 10, 13))
    next_contract = build_nse_futures_universe(master, contract="next", as_of=date(2026, 10, 13))
    assert len(front) == 1
    assert front[0]["symbol"] == "M&M"
    assert front[0]["expiry"] == "2026-11-26"
    assert front[0]["spot_security_id"] == "100"
    assert front[0]["fno_ban_status"] == "UNAVAILABLE"
    assert next_contract[0]["expiry"] == "2026-12-31"
