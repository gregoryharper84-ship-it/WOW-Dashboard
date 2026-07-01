# WOW-PATCH-004 — Summer-Only Sigma Calibration

## Patch ID
`WOW-PATCH-004`

## Author / date
User + Replit agent — 2026-07-01

## Status
`DRAFT`

---

## 1. Problem statement

The `/wow/kalshi/weather/calibration` endpoint (connector 3, NCEI CDO) estimates `sigma_f` as `annual_TMAX_std × 0.55`. For northern cities (CHI, NYC, AUS), annual TMAX standard deviation is ~21°F due to large seasonal swings — pushing the recommended `sigma_f` to 6.0°F (the cap). Kalshi NHIGH markets are daily contracts scored against a single city's observed high; they are effectively active May–September when weather markets are most liquid. The Gaussian bracket model produces meaningful probability separation only when `sigma_f` matches the actual day-to-day forecast error in the season of interest, not the full annual temperature range. The current calibration over-widens the Gaussian, flattening bracket probability distributions and reducing edge detection sensitivity.

**Observed values (2026-07-01 deploy):**
- CHI annual TMAX std = 21.32°F → σ_f_rec = 6.0 (capped)
- NYC annual TMAX std ≈ 20°F → σ_f_rec = 6.0 (capped)
- AUS annual TMAX std ≈ 18°F → σ_f_rec = 6.0 (capped)
- LA annual TMAX std ≈ 11°F → σ_f_rec = 3.23 (uncapped, likely accurate)
- MIA annual TMAX std ≈ 12°F → σ_f_rec = 3.81 (uncapped, likely accurate)

The real quantity we want is NWS 24-hour forecast MAE for summer months. Historical TMAX std × 0.55 is a placeholder; true calibration needs paired (NWS_forecast_high, observed_TMAX) data — exactly what the WEATHER_SCOUT ledger accumulates.

## 2. Affected spec sections

| Section | Change type | Description |
|---------|-------------|-------------|
| §12 — Weather Lane (new) | MODIFY | `sigma_f` default and calibration methodology |
| Calibration endpoint | MODIFY | Add `months` param; compute std over June–Aug only |
| WEATHER_SCOUT ledger | MODIFY | After 25 settled rows, compute empirical MAE and feed back as σ_f |

## 3. Exact delta

### Phase 1 — Summer-window NCEI CDO query (quick win, no WEATHER_SCOUT data needed)

`/wow/kalshi/weather/calibration` gains an optional `months` param:

```
months  str  — comma-separated month numbers to include (default: "1,2,3,...,12")
              Recommended for NHIGH season: "5,6,7,8,9"
```

On `months=5,6,7,8,9`: filter GHCND TMAX records to those months before computing std.
`sigma_f_recommended` is then based on seasonal std only.

Expected result for CHI summer window: TMAX std ≈ 6–8°F → σ_f_rec ≈ 3.3–4.4°F (no cap).

### Phase 2 — Empirical calibration from WEATHER_SCOUT ledger (requires ≥25 settled rows)

After Milestone 1 (25 settled rows with Brier scores), add a `/wow/kalshi/weather/scout/sigma-calibrate` endpoint that:
- Pulls all settled rows where `forecast_high IS NOT NULL` and `observed_high IS NOT NULL`
- Computes `MAE = mean(|forecast_high − observed_high|)` per city
- Returns `sigma_f_empirical = MAE × 1.25` (MAE-to-sigma conversion for Gaussian)
- Feeds directly into the default `sigma_f` for the evaluate endpoint

This replaces the NCEI proxy entirely once sufficient data exists.

## 4. Test case

```bash
# Phase 1 happy path — summer window for CHI
curl "http://localhost:80/api/wow/kalshi/weather/calibration?city=CHI&months=5,6,7,8,9"

# Expected response (key fields):
# { "ok": true, "sigma_f_recommended": <value between 2.5 and 5.0>, "months_filter": [5,6,7,8,9], "records_used": <40–100> }

# Full-year (current default) still works:
curl "http://localhost:80/api/wow/kalshi/weather/calibration?city=CHI"
# { "ok": true, "sigma_f_recommended": 6.0, "months_filter": null }

# Edge case — invalid months param
curl "http://localhost:80/api/wow/kalshi/weather/calibration?city=CHI&months=13,0"
# { "ok": false, "error": "months must be 1–12" }
```

## 5. Conflict check

| Question | Answer |
|----------|--------|
| Does this change any existing badge/ceiling rule? | No. sigma_f is a scoring input, not a badge gate. |
| Does this add, rename, or remove a top-level field from §7's field contract? | No — pure weather lane change. |
| Does this change the set of hard vs. advisory failure-path tags (§6)? | No. |
| Does this alter `_llp_decision` logic or its input thresholds (§3)? | No — weather lane is separate from LLP. |
| Does this change any Odds API market alias or sport-key mapping (§5, §8)? | No. |
| Does this affect the odds-snapshot cron, snapshot kinds, or CLV grading (§11)? | No. |
| Does this require a DB migration (new table, new column, new index)? | Phase 1: No. Phase 2: adds `sigma_f_empirical` column to weather_scout_log (ALTER TABLE, non-destructive). |
| Does this add a new route that the Express proxy in `scoring-proxy.ts` must forward? | Phase 2: `/wow/kalshi/weather/scout/sigma-calibrate` needs a GET proxy route. Phase 1 only modifies existing calibration endpoint. |
| Could gunicorn's 2-worker setup cause a race condition on any shared state this adds? | No. Phase 1 is a pure read from NCEI CDO. Phase 2 reads from DB only. |

## 6. Ground-truth doc update

_Leave blank until status = SHIPPED._

---

## Patch log

| Patch ID | Date | Status | Summary |
|----------|------|--------|---------|
| WOW-PATCH-001 | 2026-06-30 | SHIPPED | Kalshi NHIGH weather lane — 5-city station map, NWS CLI fetcher, bracket scorer, `/wow/kalshi/weather/evaluate` + `/stations` |
| WOW-PATCH-002 | 2026-07-01 | SHIPPED | Gaussian bracket probabilities — `_score_weather_brackets_gaussian`, `math.erf` CDF, sigma_f=3.5 default, full normalization, CLI date-mismatch guard |
| WOW-PATCH-003 | 2026-07-01 | SHIPPED | Price-source staleness gate — `_apply_weather_price_gate`, `_weather_terminal_label_v2`, synthetic/operator_supplied capped at KALSHI_WATCH |
| WOW-PATCH-004 | 2026-07-01 | DRAFT | Summer-only sigma calibration — filter NCEI window to summer months; Phase 2: empirical MAE from WEATHER_SCOUT ledger |
