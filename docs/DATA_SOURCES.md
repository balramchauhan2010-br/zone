# Data-source matrix (current build)

| Data field | Provider/adapter | Current state | Integrity behavior |
|---|---|---|---|
| NSE FUTSTK instrument master | Dhan public detailed scrip-master CSV | Implemented; fetched/cached for 6 hours | Dynamic contracts; provider membership is not presented as official ban-clearance |
| NSE equity/Futures LTP, OHLC snapshot, volume, OI | DhanHQ v2 Market Quote | Implemented; requires Dhan Data API access + valid Streamlit server-side secrets | Provider timestamps parsed; default/invalid timestamps are not marked fresh |
| NSE cash-equity daily bars | Yahoo Finance via optional `yfinance` fallback | Implemented; disabled by default with `YAHOO_RESEARCH_ENABLED=false` | Research-only; bar date only, exact timestamp/freshness unknown; cash price is never labeled Futures |
| Intraday OHLCV | DhanHQ v2 Historical Intraday | Implemented; native 1-minute candles retrieved for custom interval resampling | No placeholder candles; request errors return UNAVAILABLE |
| Daily OHLCV | DhanHQ v2 Historical Data | Implemented | Provider data only |
| Futures OI change | Actual quote observations in this API process | Partial | First observation has no delta baseline; subsequent delta is labeled with interval and is not persisted |
| Option expiry list and chain | DhanHQ v2 Option Chain | Implemented; rate-limited | Actual returned strikes; PCR scope is explicit |
| Delivery percentage | No configured verified source | UNAVAILABLE | No daily/intraday values are fabricated |
| F&O ban status | No configured official NSE ban source | UNAVAILABLE | New entries blocked while ban status is unknown |
| Official Futures eligibility | Dhan master only in this build | PARTIAL | Marked provider-listed, not official eligible/clear |
| NIFTY/sector indices | No verified mapping/provider in this build | UNAVAILABLE | Not estimated from stocks |
| Global macro, FX, commodities, crypto | Instrument catalog only; no licensed provider mapping | UNMAPPED | No live quotes/candles; Global Zone can scan user-uploaded CSV only, labeled unverified |
| FII/FPI and DII activity | No verified published-data adapter | UNAVAILABLE | Not labeled live |
| News/corporate announcements/events | No verified feed configured | UNAVAILABLE | No invented headlines or summaries |
| Gemini evidence explanation | Optional Google Gemini adapter | Requires `GEMINI_API_KEY` and `APP_ADMIN_TOKEN` | Can summarize submitted evidence only; cannot supply data or override deterministic risk filters |
| Market session/holiday | Local IST clock only | PARTIAL | Session clock does not verify exchange holidays |

## Fallback boundaries

- NSEPython and `jugaad-data` are not configured for unattended hosted polling because they access NSE website endpoints and the NSE Terms of Use prohibit systematic or automated data collection.
- OpenBB is a provider framework, not an independent market-data feed. The research fallback uses `yfinance` directly; using OpenBB with its Yahoo provider would not add independent data or F&O coverage.
- Yahoo research mode cannot populate NSE Futures LTP/OI, option PCR, delivery %, or official F&O-ban status. Missing fields remain unavailable and signals stay blocked.

## Provider documents

- [DhanHQ market quote](https://dhanhq.co/docs/v2/market-quote/)
- [DhanHQ historical data](https://dhanhq.co/docs/v2/historical-data/)
- [DhanHQ option chain](https://dhanhq.co/docs/v2/option-chain/)
- [DhanHQ instrument list](https://dhanhq.co/docs/v2/instruments/)
- [DhanHQ authentication](https://dhanhq.co/docs/v2/authentication/)
- [NSE Terms of Use](https://www.nseindia.com/static/nse-terms-of-use)
- [yfinance documentation](https://ranaroussi.github.io/yfinance/)
- [OpenBB provider selection](https://docs.openbb.co/odp/cli/data-sources)
