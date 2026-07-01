---
name: Kalshi weather NWS CLI date-mismatch guard
description: NWS REST API always returns the most recently issued CLI, not the one for the requested date — endpoint must verify dates match before using observed_high.
---

## The problem

`_fetch_nws_cli(city)` fetches the latest CLI product via:
`api.weather.gov/products?type=CLI&location={nws_issuedby}&limit=1`

The NWS API returns the most recently issued CLI regardless of what date the
caller is asking about. If today's CLI hasn't been issued yet and the user
requests tomorrow's date, the June 30 FINAL CLI (93°F) gets returned and
the endpoint uses it verbatim as `observed_high` — misclassifying a future
date as FINAL binary mode.

## The fix (in wow_kalshi_weather_evaluate)

After extracting `observed_high` from `cli_result`, compare dates:

```python
cli_issuance = cli_result.get("issuance_time") or ""
if observed_high is not None and cli_issuance:
    cli_date = cli_issuance[:10]   # "YYYY-MM-DD"
    if cli_date != date_str:
        observed_high = None
        report_status = "NOT_YET_ISSUED"
        revision_risk = False
```

This discards the mismatched CLI and falls through to the Gaussian forecast path.

**Why:** Without this guard, TF-WX-14 (non-binary probs) and TF-WX-17 (horizon-based label) both fail — a 51h-horizon future date gets `scoring_mode=binary_final_cli` and `weather_label=WEATHER_MODEL_READY` instead of `WEATHER_SCOUT`.

**How to apply:** Any time the weather evaluate endpoint extracts NWS CLI data for a date other than "today." Always validate `cli_issuance_time[:10] == date_str` before trusting `observed_high`.
