# AI PRO INSTITUTIONAL TRADING TERMINAL

**Build:** 2.0 · **UI/runtime:** Streamlit + Python in one process · **Default timezone:** Asia/Kolkata

> यह read-only analysis terminal है; इसमें order placement नहीं है। Missing, stale, unverified या unsupported data को `UNAVAILABLE`/`NO TRADE` बताया जाता है—कोई sample quote, synthetic zone या invented confidence नहीं।

## 1. Current deployment choice

यह version **GitHub + Streamlit Community Cloud** पर single Streamlit app के रूप में चलता है। अलग Render/AWS/Hostinger API service या `API_BASE_URL` की जरूरत नहीं। Streamlit app के Python process में मौजूद handlers ही data/analysis काम चलाते हैं; UI किसी बाहरी API address या `localhost` को call नहीं करती।

**Implementation note:** पुराने Python handlers `api/main.py` में हैं। `ui/local_backend.py` उन्हें in-process ASGI test client के जरिए चलाता है—अलग FastAPI/Uvicorn web server deploy या start नहीं होता। FastAPI package अभी internal route/validation dependency के रूप में requirements में रहता है; इसे पूरी तरह निकालने के लिए handlers का अलग direct-Python rewrite चाहिए।

इस workspace में अभी कोई Dhan credentials, Gemini key, global-market subscription या official NSE feeds उपलब्ध नहीं हैं। इसलिए real quote/option-chain/zone results और GitHub push/Streamlit deployment यहाँ से live-verify नहीं किए गए।

## 2. Features and safeguards

- Five navigation tabs: **NSE ALL FUTURE STOCKS**, **Zone**, **Global Zone**, **News & Events**, **Settings**.
- DhanHQ v2 read-only adapter: public detailed instrument master, market quote, historical candles, option expiry/chain.
- Optional, disabled-by-default Yahoo research fallback for daily NSE cash-equity bars only; it never substitutes cash prices for Futures or enables trade signals.
- NSEPython/jugaad-data automated polling is not included; no unattended NSE website scraping.
- NSE `FUTSTK` universe uses master-based front/next contract selection; no permanent hard-coded stock universe.
- Dhan native 1-minute intraday history can be resampled to user-selected custom bars.
- Supported timeframes: 3/5/10/15/30/75m, 1/2/4/6h, daily/weekly/monthly, plus configurable 1–390m intervals.
- Completed-candle volume spike default; comparable-candle baseline; partial-candle mode time-adjustment.
- User-supplied zone state machine preserved: Fresh → Tested on proximal touch; Tested → Broken on distal break; distal break first means direct Broken.
- EOD high/low buffers default to +10%/−10%, independently configurable.
- EMA/volume settings are configurable. Risk sizing is an estimate, not an order; costs, margin and slippage are excluded.
- F&O eligibility/ban/risk data are fail-closed: where required verification is missing, new entries remain `NO TRADE`.
- Global provider symbols are editable under Settings and can be exported/imported as JSON; edits remain visibly unverified until a configured provider returns data.
- Optional Gemini explanation is evidence-only; it does not generate market data or override deterministic signals.

## 3. Navigation

1. **NSE ALL FUTURE STOCKS** — dynamic front/next/all NSE stock Futures universe, freshness/status, filterable table, selectable columns and on-demand symbol detail.
2. **Zone** — selected-symbol or universe scan. Long scans may take time and depend on available provider data.
3. **Global Zone** — normalized catalog with editable mappings. Without an authorized data provider, live quote/zone stays unavailable; CSV upload is labeled unverified user data.
4. **News & Events** — explicit unavailable state until an official/authorized feed is configured.
5. **Settings** — session settings, EOD buffers, standard/custom timeframes, EMA lengths, volume baseline/threshold, risk inputs, PCR scope, column visibility and JSON export/import. Settings are not a persistent multi-user database.

## 4. Architecture

```text
Browser
  │
  ▼
Streamlit Community Cloud (root app.py)
  ├── ui/app.py                 dashboard and controls
  ├── ui/local_backend.py       in-process calls; no remote API URL
  ├── api/main.py              Python handlers (not a separately hosted server)
  └── terminal/                Dhan provider, indicators, zones and risk logic
           │
           └── DhanHQ v2 (only when valid server-side credentials/data access exist)
```

The app uses Streamlit Secrets server-side. Dhan/Gemini keys are not included in browser code or Git. Do not publish a personal data feed without checking your provider's permissions/terms.

## 5. Important files

```text
ai-pro-institutional-zone-terminal/
├── app.py                      # Streamlit Community Cloud entrypoint
├── ui/app.py
├── ui/local_backend.py         # in-process Python handler adapter
├── ui/runtime_config.py        # loads Streamlit Secrets before provider config
├── api/main.py                 # existing Python handlers
├── terminal/
│   ├── config.py
│   ├── timeframes.py
│   ├── zone_core.py
│   ├── providers/
│   └── services/
├── data/global_instruments.json
├── tests/
├── requirements.txt
├── .env.example
└── .streamlit/config.toml
```

## 6. Deploy on Streamlit Community Cloud

1. Commit the updated project files to your GitHub repository's `main` branch.
2. In Streamlit Community Cloud, connect that repository and set **Main file path** to `app.py` (repository root).
3. Open the app's **Manage app → Settings → Secrets**. Add only credentials you actually have:

   ```toml
   DHAN_CLIENT_ID = "your-client-id"
   DHAN_ACCESS_TOKEN = "your-current-token"
   YAHOO_RESEARCH_ENABLED = "false"
   ```

   Optional Gemini settings can also be added there. Never put real values in GitHub, `.env.example`, screenshots, or chat.
4. Save and reboot/redeploy the Streamlit app. There is **no `API_BASE_URL` to configure** in this single-app version.

Without valid Dhan credentials/data permissions, futures LTP/OI may remain unavailable. The app must not fill those cells with estimates. If you turn on Yahoo research mode, it is for private personal research only and shows daily cash-equity data—not live Futures/OI/PCR. Keep it off for a public app.

### Streamlit Secrets and local `.env`

| Setting | Purpose |
|---|---|
| `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN` | Dhan read-only market data; keep server-side and renew tokens as required |
| `DHAN_BASE_URL`, `DHAN_MASTER_URL` | Optional provider URL overrides; defaults are in `.env.example` |
| `YAHOO_RESEARCH_ENABLED` | Optional; defaults to `false`; private research only |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Optional evidence-only explanations; not a source of prices/signals |
| `APP_ADMIN_TOKEN` | Optional protection for the billable explanation feature |
| `TZ` | Defaults to `Asia/Kolkata` |

Local development: copy `.env.example` to `.env`; `.env` is git-ignored.

## 7. Local run

Python **3.12.8** recommended.

```bash
cd ai-pro-institutional-zone-terminal
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

The same Streamlit process runs the Python data/analysis handlers; no second API terminal is needed.

## 8. Data limitations

- Dhan instrument-master membership is not proof of NSE ban clearance. When official F&O-ban status is unavailable, new entries remain blocked.
- OI change needs multiple actual snapshots in the current process; otherwise it is unavailable.
- Delivery history, official ban list, FII/DII, NSE sector indices, licensed global feeds, verified news and events are not connected yet.
- Market session is inferred from IST weekday/time; an official exchange-holiday feed is not connected.
- Provider time and receive time are separate. Missing/invalid timestamps are not labeled fresh.
- Option PCR uses actual returned strikes and labels the configured scope; missing chain data is not fabricated.
- A live or valid Futures price requires an authorized provider response. Yahoo daily cash bars are not a Futures substitute.
- OpenBB is a connector framework, not a data feed by itself; an installed provider extension, vendor credentials and verified symbols are required. `nsepython`/`jugaad-data` are not used for unattended NSE website polling in the hosted app.

## 9. Zone engine notes

`terminal/zone_core.py` user-supplied detection/scoring/state-transition logic is preserved. The adapter normalizes/resamples candles and passes user settings; no separate `zone_validation.py` is added.

- EOD/session high buffer: `+10%`; low buffer: `−10%`; both configurable.
- Fresh → Tested on proximal-line touch.
- Tested → Broken only on distal-line break; distal break before a first proximal touch goes directly to Broken.
- Full-calendar-day high/low values can affect earlier intraday bars in historical replay. This known look-ahead limitation is surfaced; do not treat it as look-ahead-safe backtesting.

## 10. Global instrument list

`data/global_instruments.json` preserves the supplied catalog. `provider_symbol` mappings remain blank/unresolved until verified against an authorized provider. Suspected corrections stay visible for review; no symbol orientation/provider mapping is guessed.

## 11. Tests

From the project root:

```bash
python -m pytest -q
```

Tests cover core zone states, EOD settings, timeframe resampling, volume baselines, dynamic futures mapping, option-chain scope, fail-closed signal/risk rules, provider fallback labels, in-process UI handler calls, and unverified CSV labeling. Passing tests do not imply a live Dhan connection or successful Streamlit deployment.

## 12. Known limitations

1. No authenticated user/role system or persistent multi-user settings/job database; keep a personal credentials-backed app private.
2. Full-universe scans depend on Dhan quotas/latency and share the Streamlit process resources.
3. Official NSE ban, delivery, FII/DII, sector, news/events and authorized global feeds still need a verified provider.
4. No order execution, profitability guarantee, verified live data claim, or calibrated probability output.
5. GitHub push and Streamlit Community Cloud deployment have not been performed from this workspace.
