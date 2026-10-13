# Zone engine adapter

## Parity and state rules

`terminal/zone_core.py` contains the rules supplied by the user. The integration layer does not replace the pattern detector, scoring, duplicate detection, SL/TP references, or state machine.

- Demand/supply zones retain the core's proximal/distal levels.
- Fresh → Tested on proximal touch.
- A distal cross while Fresh produces Broken with `distal_break_before_test`.
- Tested remains Tested until distal break. `maxTestedCount` is not an auto-break rule.
- The EOD/session range filter can be enabled/disabled and uses separate `eodHighBufferPct` and `eodLowBufferPct` values. Defaults are 10.0 and 10.0.
- Available scanner intervals are resampled before the core runs; selected timeframe is included in the adapter output.

## EOD/session range semantics

The user-supplied core derives day/session high and low from rows sharing a normalized calendar date and checks that the zone proximal line lies in:

```text
[day_low × (1 − eodLowBufferPct / 100), day_high × (1 + eodHighBufferPct / 100)]
```

The Settings tab defaults to 10% below the low and 10% above the high and permits independent adjustment.

## Important limitation preserved rather than silently changed

For intraday historical replay/backtesting, the current supplied core uses the full day's grouped high/low at every bar from that day. If a full day of candles is passed while evaluating earlier bars, later same-day extremes can affect earlier filtering (look-ahead risk). The adapter reports this warning. A causal mode would change behavior/parity and requires a separate approved change plus comparison tests.

## Why no separate `zone_validation.py`

The user explicitly said a separate validation module was not required. The app only reports the zone-core outputs; it does not invent a secondary validation layer.
