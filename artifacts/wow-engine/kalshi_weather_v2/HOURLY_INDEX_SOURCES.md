# Kalshi Weather V2 hourly-index evidence

This slice is grounded in the current Kalshi public API contract for the canonical weather index used by hourly temperature markets.

Current authoritative references checked during implementation:

- Kalshi API documentation index: `https://docs.kalshi.com/llms.txt`
- Get Weather Index: `https://docs.kalshi.com/api-reference/live-data/get-weather-index`
- Get Weather Index Calibrations: `https://docs.kalshi.com/api-reference/live-data/get-weather-index-calibrations`
- Production REST base URL: `https://external-api.kalshi.com/trade-api/v2`

Governed interpretation:

- `GET /live_data/weather/{city}` is the canonical minute-resolution city temperature index behind hourly temperature markets.
- Index values are Fahrenheit; this adapter requires the response to identify Fahrenheit exactly.
- Missing-quorum minutes are real gaps. This implementation never interpolates, forward-fills, zero-fills, or reconstructs a missing canonical minute from member-station observations.
- `normal`, `degraded`, and `incomplete` are preserved as distinct index states. An `incomplete` point cannot be promoted into a canonical value by this adapter.
- Detailed member-station readings are preserved as source/QC evidence only. They do not replace the Kalshi-computed index value.
- Any `receipt_basis` metadata is preserved verbatim. Settlement eligibility is not inferred from that field by the acquisition layer.
- `GET /live_data/weather/{city}/calibrations` is treated as append-only configuration evidence. Calibration rows are frozen raw until a separate parser validates configuration versions, effective-time semantics, station weights, offsets, and city-reference fields.

Safety invariant: this adapter is GET-only analytical evidence acquisition. `can_execute=false`; no order placement, amendment, decrease, or cancellation capability is introduced.
