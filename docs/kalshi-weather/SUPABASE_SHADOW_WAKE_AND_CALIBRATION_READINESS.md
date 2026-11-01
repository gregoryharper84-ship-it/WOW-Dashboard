# Kalshi Weather V2 — Supabase shadow wake and calibration readiness

**Live scope:** dedicated Supabase project `kasbebndczmaopvcvfco`, existing isolated Render web service `srv-dahijm5g1s2s73bbvkn0`, Miami hourly contract lane only.

## Production configuration established October 31, 2026

- Database migration recorded in Supabase under `kalshi_weather_shadow_external_wake_cron`.
- Supabase `pg_cron` (1.6.4) and `pg_net` (0.20.4) enabled.
- Job name: `kalshi-weather-v2-shadow-read-only-wake`; UTC cadence: `*/30 * * * *` (two times per hour), active.
- Only operation: unauthenticated HTTPS **GET** to `https://wow-kalshi-weather-v2.onrender.com/health`; `90,000 ms` maximum HTTP timeout; User-Agent `kalshi-supabase-cron-wake/1`.
- No Supabase service key, Kalshi API secret, alternate collector, order controller, probability issuance, or capability promotion.
- The existing Render process retains sole bounded weather-capture and settlement responsibility, guarded by `WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER`. Keep the GitHub external wake workflow as *best-effort backup*, not an independent data writer.
- Unlike uninterrupted ten-minute pings, a 30-minute wake deliberately allows the Free Render process to sleep between bursts to limit consumption of the Render workspace's shared **750 monthly free instance hours**. It is a **cost-constrained research schedule**, not high-availability certification.
- An explicit manual `net.http_get` with the same fixed URL/headers returned **HTTP 200**, `DATABASE_READ_VERIFIED`, `can_execute=false` and `probability_publishable=false` on 2026-11-01 at approximately 02:57 UTC. Scheduled `cron.job_run_details` receipts and resulting weather-row freshness must be checked separately: job installation and a manual GET are insufficient proof of an unattended cycle.

## Installed job definition (audit reference)

```sql
CREATE EXTENSION IF NOT EXISTS pg_cron WITH SCHEMA pg_catalog;
CREATE EXTENSION IF NOT EXISTS pg_net;
SELECT cron.schedule(
  'kalshi-weather-v2-shadow-read-only-wake',
  '*/30 * * * *',
  $job$
    SELECT net.http_get(
      url := 'https://wow-kalshi-weather-v2.onrender.com/health',
      headers := '{"Accept":"application/json","User-Agent":"kalshi-supabase-cron-wake/1"}'::jsonb,
      timeout_milliseconds := 90000
    );
  $job$
);
```

The real migration was applied through the connected Supabase deployment, not by GitHub Actions. This document is the recoverable intended configuration and verification contract.

## Verification (read only)

```sql
SELECT jobid,jobname,schedule,active,command
FROM cron.job WHERE jobname = 'kalshi-weather-v2-shadow-read-only-wake';

SELECT runid,status,start_time,end_time,return_message
FROM cron.job_run_details
WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname = 'kalshi-weather-v2-shadow-read-only-wake')
ORDER BY start_time DESC LIMIT 15;

SELECT id,status_code,timed_out,created,error_msg
FROM net._http_response
ORDER BY created DESC LIMIT 15;

SELECT COUNT(*) AS n, MAX(created_at) AS latest
FROM public.wow_kalshi_weather_predictions;

SELECT COUNT(*) AS settled,COUNT(*) FILTER (WHERE brier_score IS NOT NULL) AS brier_scored
FROM public.wow_kalshi_weather_outcomes;
```

Verify that new Render logs show `WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_PASS` and a new immutable shadow row *after* the DB-driven wake. A pg_net HTTP 200 alone does not certify collection.

**Rollback:** `SELECT cron.unschedule('kalshi-weather-v2-shadow-read-only-wake');` (specific job only; do not drop shared extensions).

## Calibration gate

- Use **one immutable forecast error per settlement station + hourly target time + lead-time bucket**, from as-of weather forecasts and exact Synoptic Data index settlement. Threshold sibling contracts are *not* independent samples.
- The current `fit_candidate_calibration_profile` requires at least **30 independent eligible samples per station/lane/horizon**. At **40 or more**, `build_calibration_report` also permits a chronological forward holdout calculation. Both are research-only; neither certifies probability automatically.
- Report MAE, signed bias, sample size, source outages/429, holdout scores and station identity; do not fit or backfill using information published after the prediction decision time.
- An approved probability model additionally requires calibration evidence, appropriate coverage, governance capability, exact fees, executable prices and conservative lower-bound edge. `KALSHI_WEATHER_PROBABILITY` remains `UNAVAILABLE`, `probability_publishable=false`, `can_execute=false` until independently certified.

### Research progress SQL — no probability or write

```sql
SELECT
  p.model_payload->'contract'->>'settlement_location_code' AS settlement_station,
  p.model_payload->>'lead_time_bucket' AS lead_bucket,
  p.model_version,
  COUNT(*) AS settled_predictions,
  ROUND(AVG(ABS(p.central_estimate_f - o.settled_value))::numeric,3) AS mae_f,
  ROUND(AVG(p.central_estimate_f - o.settled_value)::numeric,3) AS signed_bias_f
FROM public.wow_kalshi_weather_predictions p
JOIN public.wow_kalshi_weather_outcomes o USING (prediction_id)
WHERE p.central_estimate_f IS NOT NULL AND o.settled_value IS NOT NULL
GROUP BY 1,2,3 ORDER BY 1,2,3;
```

**Failure modes:** GitHub schedules can be dropped; Open-Meteo HTTP 429 fails closed; Render Free may spin down, restart or exhaust shared hours; Supabase cron may be delayed; missing settlement points must not be silently imputed. All such failures reduce readiness, not the safety gate.
