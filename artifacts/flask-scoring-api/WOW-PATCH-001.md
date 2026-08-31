# WOW-PATCH-001 — Kalshi Daily High Temperature Weather Lane

## Patch ID
`WOW-PATCH-001`

## Author / date
External Claude planning thread + legacy platform agent conflict-check · 2026-06-30

## Status
`SHIPPED`

---

## 1. Problem statement

WOW has no ability to evaluate Kalshi daily high temperature (NHIGH) contracts.
Five city series are live and tradeable — NYC, LA, Miami, Chicago, Austin — but
the engine has no station mapping, no NWS data fetcher, and no endpoint to score
a bracket. A manual pre-build audit (see `kalshi-weather-station-verification.md`)
verified all five settlement stations from live Kalshi contract rule text. Two
false-positive station codes (PBI for Miami, BUR for LA) were caught and rejected
before reaching implementation. The verified table is safe to hardcode.

This patch adds:
- A hardcoded station mapping table (city → Kalshi series ticker → NWS station code → timezone)
- A NWS Climatological Report (CLI) fetcher using the verified `issuedby` URL pattern
- A `/wow/kalshi/weather/evaluate` POST endpoint that scores a bracket against forecast + live NWS data
- A `/wow/kalshi/weather/stations` GET endpoint for health-checking the mapping table
- WEATHER_* internal labels (upstream model stage only) that resolve to the existing KALSHI_* terminal labels

No new terminal labels. No changes to LLP badge logic, decision logic, or any existing field.

---

## 2. Affected spec sections

| Section | Change type | Description |
|---------|-------------|-------------|
| §8 — Data-source map | ADD | New row: NWS CLI product, base URL, auth (none), used for |
| New §12 — Kalshi weather lane | ADD | Station table, WEATHER_* label definitions, endpoint contract, LST/DST rule, settlement revision rule |
| §7 — Per-record field contract | NO CHANGE | Weather lane has its own response shape; it does not emit the §7 LLP Pro field set |

`LLP_GROUND_TRUTH.md` update required: add §8 row + new §12 after patch ships.

---

## 3. Exact delta

### 3a. Station mapping table (hardcoded, `_KALSHI_WEATHER_STATIONS`)

```python
_KALSHI_WEATHER_STATIONS = {
    "NYC": {
        "series":   "KXHIGHNY",
        "name":     "Central Park, New York",
        "station":  "KNYC",
        "nws_site": "OKX",
        "nws_issuedby": "NYC",
        "tz":       "America/New_York",
    },
    "LA": {
        "series":   "KXHIGHLAX",
        "name":     "Los Angeles Airport, CA",
        "station":  "KLAX",
        "nws_site": "LOX",
        "nws_issuedby": "LAX",
        "tz":       "America/Los_Angeles",
    },
    "MIA": {
        "series":   "KXHIGHMIA",
        "name":     "Miami International Airport",
        "station":  "KMIA",
        "nws_site": "MFL",
        "nws_issuedby": "MIA",
        "tz":       "America/New_York",
    },
    "CHI": {
        "series":   "KXHIGHCHI",
        "name":     "Chicago Midway, IL",
        "station":  "KMDW",
        "nws_site": "LOT",
        "nws_issuedby": "MDW",
        "tz":       "America/Chicago",
    },
    "AUS": {
        "series":   "KXHIGHAUS",
        "name":     "Austin Bergstrom, TX",
        "station":  "KAUS",
        "nws_site": "EWX",
        "nws_issuedby": "AUS",
        "tz":       "America/Chicago",
    },
}
```

**Do not substitute or infer station codes.** O'Hare (KORD) ≠ Midway (KMDW).
PBI ≠ KMIA. BUR ≠ KLAX. These are verified from live Kalshi contract rule text.

### 3b. NWS CLI fetcher

```
GET https://forecast.weather.gov/product.php
  ?site={nws_site}&product=CLI&issuedby={nws_issuedby}&format=txt
```

Parse the max temperature line. Return:
```
{ "observed_high": int | None, "report_date": str, "status": "FINAL" | "PRELIMINARY" | "NOT_YET_ISSUED" }
```

**Settlement revision rule:** if `status == "PRELIMINARY"`, include a
`"revision_risk": true` flag. Revisions issued BEFORE contract expiration count
toward settlement. Revisions AFTER expiration do not.

**LST/DST rule:** NWS CLI reports use Local Standard Time windows even during
Daylight Saving Time. Validate `forecast_timestamp` against LST, not naive
calendar-day clock time.

### 3c. WEATHER_* internal labels (upstream only, never terminal)

| Label | Meaning |
|-------|---------|
| `WEATHER_MODEL_READY` | NWS data fetched, bracket scored, edge computable |
| `WEATHER_WATCH` | Data fetched but edge below threshold or revision risk present |
| `WEATHER_SCOUT` | NWS report not yet issued; forecast data only |
| `WEATHER_REJECT_DATA` | NWS fetch failed or station mismatch detected |
| `WEATHER_REJECT_SETTLEMENT` | Bracket structure ambiguous or mutual-exclusivity violated |

These resolve to terminal labels: `KALSHI_PLAYABLE_LIMIT_ONLY`,
`KALSHI_WATCH`, or `KALSHI_REJECT_*` via the existing decision mapper.

### 3d. New endpoints

**`POST /wow/kalshi/weather/evaluate`**
```json
Body: {
  "city":     "NYC",            // required — one of NYC/LA/MIA/CHI/AUS
  "date":     "2026-07-01",     // required — YYYY-MM-DD, event date
  "brackets": [                 // required — Kalshi bracket prices
    { "label": "≤85", "yes_price": 0.32, "no_price": 0.68 },
    { "label": "86–89", "yes_price": 0.41, "no_price": 0.59 },
    { "label": "≥90", "yes_price": 0.27, "no_price": 0.73 }
  ]
}

Response: {
  "ok": true,
  "city": "NYC",
  "series": "KXHIGHNY",
  "station": "KNYC",
  "date": "2026-07-01",
  "observed_high": 88,             // null if report not yet issued
  "report_status": "PRELIMINARY",  // FINAL | PRELIMINARY | NOT_YET_ISSUED
  "revision_risk": true,
  "forecast_high": 91,             // from NWS forecast (if available)
  "weather_label": "WEATHER_MODEL_READY",
  "terminal_label": "KALSHI_PLAYABLE_LIMIT_ONLY",
  "brackets_scored": [
    { "label": "≤85", "yes_price": 0.32, "model_prob": 0.08, "edge": -0.24, "verdict": "NO_EDGE" },
    { "label": "86–89", "yes_price": 0.41, "model_prob": 0.55, "edge": 0.14, "verdict": "PLAYABLE" },
    { "label": "≥90", "yes_price": 0.27, "model_prob": 0.37, "edge": 0.10, "verdict": "WATCH" }
  ],
  "mutual_exclusivity_check": true,  // model_probs sum to 1.00 ± 0.01
  "lst_dst_note": "Report uses LST window; DST active — validate window alignment",
  "data_sources": ["nws_cli", "nws_forecast"]
}
```

**`GET /wow/kalshi/weather/stations`**
```json
Response: {
  "ok": true,
  "stations": { ...copy of _KALSHI_WEATHER_STATIONS... },
  "count": 5
}
```
No auth required. Used for deploy health-check.

---

## 4. Test case

```bash
# T1 — Happy path: valid city, date, brackets
curl -s -X POST http://localhost:80/api/wow/kalshi/weather/evaluate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $SCORING_API_KEY" \
  -d '{
    "city": "CHI",
    "date": "2026-07-01",
    "brackets": [
      {"label": "≤79",  "yes_price": 0.20, "no_price": 0.80},
      {"label": "80–84","yes_price": 0.45, "no_price": 0.55},
      {"label": "≥85",  "yes_price": 0.35, "no_price": 0.65}
    ]
  }'

# Expected: ok=true, station="KMDW" (NOT KORD), mutual_exclusivity_check=true
# Confirms Chicago uses Midway not O'Hare

# T2 — Unknown city → 400
curl -s -X POST http://localhost:80/api/wow/kalshi/weather/evaluate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $SCORING_API_KEY" \
  -d '{"city":"DEN","date":"2026-07-01","brackets":[]}'
# Expected: { "ok": false, "error": "Unknown city: DEN. Supported: NYC, LA, MIA, CHI, AUS" }

# T3 — Brackets don't sum to 1.00 → flag, not reject
curl -s -X POST http://localhost:80/api/wow/kalshi/weather/evaluate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $SCORING_API_KEY" \
  -d '{
    "city": "NYC",
    "date": "2026-07-01",
    "brackets": [
      {"label": "≤85","yes_price": 0.50,"no_price": 0.50},
      {"label": "≥86","yes_price": 0.60,"no_price": 0.40}
    ]
  }'
# Expected: ok=true but terminal_label="KALSHI_REJECT_STRUCTURE",
#           weather_label="WEATHER_REJECT_SETTLEMENT" (yes_prices sum to 1.10)

# T4 — Stations health-check (no auth, used post-deploy)
curl -s http://localhost:80/api/wow/kalshi/weather/stations
# Expected: { "ok": true, "count": 5, "stations": { "NYC":..., "CHI":... } }
# Confirm KMDW (not KORD), KMIA (not KPBI), KLAX (not KBUR)
```

---

## 5. Conflict check

| Question | Answer |
|----------|--------|
| Does this change any existing badge/ceiling rule? | **NO.** WEATHER_* labels are upstream model-stage only. Badge logic in `_llp_decision` and `_llp_apply_spec_badge_ceiling` is not touched. |
| Does this add, rename, or remove a top-level field from §7's field contract? | **NO.** Weather endpoint has its own response shape. The §7 LLP Pro field contract (38 fields) is unchanged. |
| Does this change the set of hard vs. advisory failure-path tags (§6)? | **NO.** WEATHER_REJECT_* are internal labels only, not `_LLP_PRO_FAILURE_TAGS` entries. |
| Does this alter `_llp_decision` logic or its input thresholds (§3)? | **NO.** Completely separate decision path for weather brackets. |
| Does this change any Odds API market alias or sport-key mapping (§5, §8)? | **NO.** NWS CLI, not Odds API. §8 gets a new row for NWS; existing rows untouched. |
| Does this affect the odds-snapshot cron, snapshot kinds, or CLV grading (§11)? | **NO.** Weather contracts settle on NWS data, not Odds API odds. Cron is unchanged. |
| Does this require a DB migration? | **NO** for v1 (stateless scoring). If bracket history tracking is added later, a `kalshi_weather_scores` table would be needed — that is a separate patch. |
| Does this add a new route the Express proxy must forward? | **YES — HIGH RISK.** New routes are `/wow/kalshi/weather/evaluate` (POST) and `/wow/kalshi/weather/stations` (GET). The existing `router.use("/wow", makeForwarder("wow"))` wildcard would cover these, but **explicit routes must be added** following the same pattern as `/wow/kalshi/scan`. The wildcard has caused silent 502s on this project before when the build order matters. Add explicit routes first, then confirm via T4 health-check after each deploy. |
| Could gunicorn's 2-worker setup cause a race on shared state? | **NO.** `_KALSHI_WEATHER_STATIONS` is a read-only module-level dict. NWS fetches are stateless HTTP. No shared mutable state introduced. |

---

## 6. Ground-truth doc update

_To be filled in after status = SHIPPED:_

```
LLP_GROUND_TRUTH.md changes required:
  §8 Data-source map — ADD row:
    | NWS CLI product | forecast.weather.gov/product.php?site=...&product=CLI&issuedby=... | Kalshi weather settlement | none (free) |

  New §12 — Kalshi weather lane:
    Station table (5 cities)
    WEATHER_* label definitions and resolution to KALSHI_* terminals
    LST/DST rule
    Settlement revision rule
    Endpoint contract summary
```

---

## Build order (for legacy platform)

1. Add `_KALSHI_WEATHER_STATIONS` dict to `app.py` (read-only, no side effects)
2. Add NWS CLI fetcher `_fetch_nws_cli(city)` — returns observed_high, status, revision_risk
3. Add bracket scorer `_score_weather_brackets(observed_or_forecast, brackets)` — enforces mutual exclusivity
4. Add `/wow/kalshi/weather/stations` GET endpoint (no auth, health-check)
5. Add `/wow/kalshi/weather/evaluate` POST endpoint
6. Add **explicit** proxy routes in `scoring-proxy.ts` before any wildcard
7. Restart both workflows
8. Run T1–T4 against dev; confirm CHI → KMDW in T1
9. Deploy; re-run T4 health-check against production URL
10. Update `LLP_GROUND_TRUTH.md` §8 + add §12
