# Latency and freshness report

## Definitions used by the app

- **Source timestamp**: timestamp supplied by the provider for a particular quote/candle, where it exists.
- **Application receive timestamp**: IST time when this API process receives the provider response.
- **Data age**: application receive time minus the parsed provider timestamp. Invalid/default timestamps are `TIMESTAMP_UNAVAILABLE`, not `FRESH`.
- **Provider request latency**: HTTP round-trip time measured with `time.perf_counter()` around the provider request. It excludes deliberate rate-limit waiting.
- **API fetch duration**: total time for a batch, including sequential request gaps/rate-limit waits.
- **UI-to-API delay**: not yet separately instrumented; it is not presented as market-feed latency.

Live scanner responses expose provider request latency and API fetch duration; quote rows carry source timestamp, receive timestamp, freshness, and age.

## Actual validation in this build

A live Dhan Data API quote/option-chain latency cannot be reported until the user configures a valid Dhan Data API subscription and server-side credentials. No credential was provided to this workspace, so **market-feed latency is NOT MEASURED**. Do not read a missing latency field as zero.

During implementation on 2026-10-13, a GET of Dhan's public detailed instrument-master CSV returned HTTP 200. The downloaded master contained 207,801 rows; end-to-end HTTP fetch plus CSV parse measured about **5,031 ms** in this sandbox. The dynamic front-contract filter produced 213 rows, with 213/213 NSE cash mappings resolved in the test. This is a one-run instrument-master measurement, not a quote/feed latency or market-data SLA.

After credentials are configured, record a representative `GET /api/futures` response multiple times during a live NSE session; report median/p95 of `provider_latency_ms` and `api_fetch_duration_ms`, along with quote `data_age_seconds`. This app does not claim zero latency.
