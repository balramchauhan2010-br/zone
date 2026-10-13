# Provider sources, fallback boundaries, and safe degradation

## Provider order

1. **DhanHQ v2** is primary for authenticated NSE Futures/spot quotes, candles, OI and options fields when the account has the required data access. Store credentials in Streamlit Community Cloud Secrets (server-side) or a local `.env`; never in source code.
2. **Yahoo Finance through `yfinance`** is an optional research-only fallback. The current adapter covers daily NSE cash-equity bars only (`SYMBOL.NS`). It is disabled by default. Enable it only for a private personal research app after reviewing Yahoo's terms. It never substitutes cash prices for Futures or creates verified F&O/OI/PCR/delivery data. The yfinance project states the Yahoo Finance API is intended for personal use: https://github.com/ranaroussi/yfinance#legal-stuff
3. **OpenBB** is a provider-extension framework, not a standalone market-data vendor. A specific OpenBB provider extension, vendor API key/entitlement, and verified provider-symbol mapping are needed. Using OpenBB with Yahoo does not make Yahoo data independent or turn it into official/live F&O data. See https://docs.openbb.co/odp/python/extensions/providers.
4. **NSEPython and jugaad-data** are not configured for automatic polling in the hosted dashboard. They access NSE website/API endpoints; NSE's Terms of Use prohibit systematic or automated data collection without permission. Use Dhan or a licensed/authorized feed for unattended hosted refresh. The repositories named in the original request appear to have moved/been mistyped: current public pages are https://github.com/aeron7/nsepython and https://github.com/jugaad-py/jugaad-data.

## Research fallback scope

Yahoo's current adapter uses daily returned cash-equity bars and records the bar date, not a fabricated exchange timestamp. Exact freshness remains `UNKNOWN`; the source is marked `RESEARCH_ONLY_UNVERIFIED`.

It cannot populate:

- NSE Futures LTP/OI, option-chain PCR, or verified F&O ban status;
- official delivery percentage or intraday Dhan-equivalent volumes;
- live/official global Futures, FX, bond-yield, or news/event values without appropriate provider access and mappings.

No values are estimated to fill these fields; missing values remain `None`/`UNAVAILABLE`, and new-entry signals stay `NO TRADE` where required data is missing.

## Enabling private research fallback

In a private Streamlit app, add this in **Manage app → Settings → Secrets** only if you accept the Yahoo research-only scope:

```toml
YAHOO_RESEARCH_ENABLED = "true"
```

Reboot the Streamlit app after changing the secret. Keep the flag `false` for a public/shareable app. Do not publish or redistribute provider data unless the applicable terms permit it.

## Why NSEPython/jugaad-data are not hosted polling fallbacks

Their libraries communicate with NSE public website/API endpoints. NSE's current Terms of Use prohibit systematic or automated data-collection activities (including scraping/data mining/data extraction) without permission: https://www.nseindia.com/static/nse-terms-of-use. This project therefore does not use them for unattended refresh in Streamlit Cloud. For entitled, authorized production data, use DhanHQ or an exchange-approved/licensed provider.

## Failure behavior

- Missing/expired Dhan credentials, Data API subscription errors, schema changes, rate limits and timeouts remain visible in provider status.
- Yahoo fallback stays disabled unless explicitly enabled; it never labels a cash close as a Futures price.
- Unresolved global provider mappings stay visible and are not silently guessed. An editable mapping is not considered verified until the selected provider returns a valid series.
- Zone scans and Futures detail/candle calls use Dhan; research cash bars are not passed into Futures zone logic.
- No sample or guessed rows are generated.
