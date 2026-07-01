# WOW-PATCH-002: Gaussian Probability Distribution for Weather Bracket Scoring

**Status:** SHIPPED  
**Patch ID:** WOW-PATCH-002-WEATHER-GAUSSIAN-PROB  
**Depends on:** WOW-PATCH-001 (NWS CLI fetcher, station map, bracket parser)  
**Implemented:** 2026-07-01  
**Acceptance tests:** TF-WX-14 through TF-WX-18 (+ TF-WX-15, TF-WX-16)

---

## Problem

PATCH-001 used a binary model_prob (0.0 or 1.0) based on whether the observed
or forecast temperature fell inside a bracket. This is only valid for FINAL CLI
data (settled temperature). For pre-settlement scoring — where only an NWS
gridpoint forecast exists — a distribution must be used, because the actual
high temperature is uncertain.

---

## Solution

Replace the binary scorer with a Gaussian CDF-based probability distribution
centered on the NWS forecast high, with a configurable standard deviation σ_f.

### New functions

| Function | Description |
|---|---|
| `_gaussian_cdf(x, mean, sigma)` | Φ((x−μ)/σ) via `math.erf`; no external deps |
| `_parse_bracket_bounds(label)` | Extracted from old scorer; shared by both scorers |
| `_score_weather_brackets_gaussian(forecast_high, sigma_f, brackets)` | PATCH-002 Gaussian scorer |
| `_score_weather_brackets_binary(model_high, brackets)` | Retained for FINAL CLI audit path |
| `_compute_forecast_horizon_hours(date_str, tz_name)` | Hours until event-date midnight in local tz |

### Probability formulas (per spec)

```
closed bracket [lo, hi]:  P = Φ((hi − μ) / σ) − Φ((lo − μ) / σ)
open-ended low  (≤ hi):   P = Φ((hi − μ) / σ)
open-ended high (≥ lo):   P = 1 − Φ((lo − μ) / σ)
```

After computing raw probabilities, the full bracket set is **normalized** so
`sum(model_prob) == 1.00`.

### Default parameters

- `sigma_f = 3.5` °F (NWS 24h gridpoint MAE approximation)
- User-overridable via `sigma_f` field in request body

### Confidence rules → WEATHER_* label

| Condition | Label |
|---|---|
| `observed_high` present (CLI issued for requested date) | `WEATHER_MODEL_READY` (binary mode) |
| `forecast_horizon_hours ≤ 24` AND `sigma_f < 4.5` | `WEATHER_MODEL_READY` (Gaussian) |
| `forecast_horizon_hours > 24 AND ≤ 48` OR `sigma_f ≥ 4.5` | `WEATHER_WATCH` |
| `forecast_horizon_hours > 48` OR no forecast available | `WEATHER_SCOUT` |

### Date-mismatch guard

The NWS API always returns the most recently issued CLI regardless of the
requested date. The endpoint now checks that `cli_issuance_time[:10]` matches
`date_str`; if not, `observed_high` is discarded and `report_status` is set to
`NOT_YET_ISSUED`, falling through to the Gaussian forecast path.

---

## Request / Response changes

### New request fields

| Field | Type | Default | Description |
|---|---|---|---|
| `sigma_f` | float | 3.5 | Gaussian standard deviation in °F |

### New response fields

| Field | Description |
|---|---|
| `scoring_mode` | `"gaussian_forecast"` or `"binary_final_cli"` or `"no_data"` |
| `sigma_f` | Echo of sigma used (TF-WX-16) |
| `forecast_horizon_hours` | Hours until event-date midnight in station local tz |
| `model_prob_sum` | Sum of all bracket model_probs — must be ≈ 1.00 (TF-WX-15) |

---

## Acceptance test results

| Test | Assertion | Result |
|---|---|---|
| TF-WX-14 | Forecast/gridpoint mode returns non-binary model_probs | PASS |
| TF-WX-15 | Full 6-bracket model_prob_sum normalizes to 1.00 | PASS |
| TF-WX-16 | `sigma_f` returned in response | PASS |
| TF-WX-17 | WEATHER_MODEL_READY requires horizon≤24h AND sigma_f<4.5 | PASS |
| TF-WX-18 | WEATHER_WATCH/SCOUT blocks KALSHI_PLAYABLE_LIMIT_ONLY | PASS |

---

## Invariants preserved

- Station map unchanged: KMDW / KMIA / KLAX / KNYC / KAUS
- Binary scorer retained for FINAL CLI audit path (informational only; PATCH-003 gates terminal label)
- No new external dependencies — uses `math.erf` from stdlib
