# Kalshi Weather V2 — Supabase shadow wake and calibration readiness

**Live scope:** dedicated Supabase project `kasbebndczmaopvcvfco`, existing isolated Render web service `srv-dahijm5g1s2s73bbvkn0`, Miami hourly contract lane only.

## Production configuration established October 31, 2026

- Supabase `pg_cron` (1.6.4) and `pg_net` (0.20.4) enabled.
- **2026-11-08: the read-only wake was retired.** Job `kalshi-weather-v2-shadow-read-only-wake` issued an unauthenticated `GET /health`, which cannot start collection. The only collection entrypoint is the authenticated `POST /internal/kalshi-weather/v17/empirical-cohort/cron-run`, so the empirical cohort stayed at `status=DISABLED` and wrote no shadow rows no matter how reliably the job fired.
- Replacement job: `kalshi-weather-v2-authenticated-collection-trigger`; UTC cadence `*/30 * * * *` (unchanged, two times per hour), active. Installed by `artifacts/wow-engine/kalshi_weather_v2_collection_trigger_cron.sql`.
- Only operation: authenticated HTTPS **POST** to `/internal/kalshi-weather/v17/empirical-cohort/cron-run` with header `X-WOW-Weather-Cohort-Key`; `90,000 ms` maximum HTTP timeout; User-Agent `kalshi-supabase-cron-collection/1`; empty JSON body.
- Credentials are read from Supabase Vault at dispatch time and are never embedded in `cron.job.command`: `wow_kalshi_weather_service_url` and `wow_kalshi_weather_cohort_key`. A missing secret returns `BLOCKED` and dispatches no HTTP request.
- Required on the isolated Render service (out of band, dashboard-managed — this service is not declared in `render.yaml`): `WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER=1` (without it the trigger route is not installed and the POST returns 404), `WOW_KALSHI_WEATHER_COHORT_KEY` matching the Vault secret, and `WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_ENABLED=0` so the in-process loop is not a second writer.
- No Supabase service key, Kalshi API secret, alternate collector, order controller, probability issuance, or capability promotion.
- The existing Render process retains sole bounded weather-capture and settlement responsibility, guarded by `WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER`. Keep the GitHub external wake workflow as *best-effort backup*, not an independent data writer.
- Unlike uninterrupted ten-minute pings, a 30-minute wake deliberately allows the Free Render process to sleep between bursts to limit consumption of the Render workspace's shared **750 monthly free instance hours**. It is a **cost-constrained research schedule**, not high-availability certification.
- An explicit manual `net.http_get` with the same fixed URL/headers returned **HTTP 200**, `DATABASE_READ_VERIFIED`, `can_execute=false` and `probability_publishable=false` on 2026-11-01 at approximately 02:57 UTC. Scheduled `cron.job_run_details` receipts and resulting weather-row freshness must be checked separately: job installation and a manual GET are insufficient proof of an unattended cycle.

## Installed job definition (audit reference)

```sql
CREATE EXTENSION IF NOT EXISTS pg_cron WITH SCHEMA pg_catalog;
CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions;

CREATE OR REPLACE FUNCTION public.wow_kalshi_weather_trigger_collection()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path TO ''
AS $function$
DECLARE
  v_base_url text;
  v_cohort_key text;
  v_request_id bigint;
BEGIN
  SELECT decrypted_secret INTO v_base_url
  FROM vault.decrypted_secrets
  WHERE name = 'wow_kalshi_weather_service_url'
  ORDER BY created_at DESC LIMIT 1;

  SELECT decrypted_secret INTO v_cohort_key
  FROM vault.decrypted_secrets
  WHERE name = 'wow_kalshi_weather_cohort_key'
  ORDER BY created_at DESC LIMIT 1;

  IF nullif(trim(v_base_url), '') IS NULL THEN
    RETURN jsonb_build_object('status','BLOCKED',
      'reason','KALSHI_WEATHER_SERVICE_URL_SECRET_MISSING',
      'probability_publishable',false,'can_execute',false);
  END IF;

  IF nullif(trim(v_cohort_key), '') IS NULL THEN
    RETURN jsonb_build_object('status','BLOCKED',
      'reason','KALSHI_WEATHER_COHORT_KEY_SECRET_MISSING',
      'probability_publishable',false,'can_execute',false);
  END IF;

  v_request_id := net.http_post(
    url := rtrim(v_base_url, '/') || '/internal/kalshi-weather/v17/empirical-cohort/cron-run',
    headers := jsonb_build_object(
      'Content-Type','application/json',
      'Accept','application/json',
      'User-Agent','kalshi-supabase-cron-collection/1',
      'X-WOW-Weather-Cohort-Key', trim(v_cohort_key)
    ),
    body := '{}'::jsonb,
    timeout_milliseconds := 90000
  );

  RETURN jsonb_build_object('status','DISPATCHED','request_id',v_request_id,
    'collection_mode','BOUNDED_ROTATING_CAPTURE_ONLY_SHADOW',
    'probability_publishable',false,'can_execute',false);
END;
$function$;

REVOKE ALL PRIVILEGES ON FUNCTION public.wow_kalshi_weather_trigger_collection()
  FROM public, anon, authenticated, service_role;

DO $retire$
BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'kalshi-weather-v2-shadow-read-only-wake') THEN
    PERFORM cron.unschedule('kalshi-weather-v2-shadow-read-only-wake');
  END IF;
END;
$retire$;

SELECT cron.schedule(
  'kalshi-weather-v2-authenticated-collection-trigger',
  '*/30 * * * *',
  $$SELECT public.wow_kalshi_weather_trigger_collection();$$
);
```

The real migration was applied through the connected Supabase deployment, not by GitHub Actions. This document is the recoverable intended configuration and verification contract.

## Verification (read only)

```sql
SELECT jobid,jobname,schedule,active,command
FROM cron.job WHERE jobname = 'kalshi-weather-v2-authenticated-collection-trigger';

-- The retired health-only wake must no longer be scheduled.
SELECT jobid,jobname,active
FROM cron.job WHERE jobname = 'kalshi-weather-v2-shadow-read-only-wake';

SELECT runid,status,start_time,end_time,return_message
FROM cron.job_run_details
WHERE jobid = (SELECT jobid FROM cron.job WHERE jobname = 'kalshi-weather-v2-authenticated-collection-trigger')
ORDER BY start_time DESC LIMIT 15;

SELECT id,status_code,timed_out,created,error_msg
FROM net._http_response
ORDER BY created DESC LIMIT 15;

SELECT COUNT(*) AS n, MAX(created_at) AS latest
FROM public.wow_kalshi_weather_predictions;

SELECT COUNT(*) AS settled,COUNT(*) FILTER (WHERE brier_score IS NOT NULL) AS brier_scored
FROM public.wow_kalshi_weather_outcomes;
```

Confirm the request was authenticated, not merely dispatched. `net._http_response.status_code` must be `200`; a `401` means the Vault secret and `WOW_KALSHI_WEATHER_COHORT_KEY` disagree, a `404` means `WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER=1` is not set on the isolated service, and a `503` means the Render key is unset. Then verify that new Render logs show `WOW_KALSHI_WEATHER_EMPIRICAL_COHORT status=EMPIRICAL_SHADOW_COHORT_PASS` and a new immutable shadow row *after* the DB-driven trigger. A pg_net HTTP 200 alone does not certify collection.

**Rollback:** `SELECT cron.unschedule('kalshi-weather-v2-authenticated-collection-trigger');` (specific job only; do not drop shared extensions). Rolling back to the previous read-only wake restores a job that cannot collect, so treat any rollback as returning the lane to `NO_PLAY_DATA_INSUFFICIENT`.

## Calibration gate

- Use **one immutable forecast error per settlement station + hourly target time + lead-time bucket**, from as-of weather forecasts and exact Synoptic Data index settlement. Threshold sibling contracts are *not* independent samples.
- The current `fit_candidate_calibration_profile` requires at least **30 independent eligible samples per station/lane/horizon**. At **40 or more**, `build_calibration_report` also permits a chronological forward holdout calculation. Both are research-only; neither certifies probability automatically.
- Report MAE, signed bias, sample size, source outages/429, holdout scores and station identity; do not fit or backfill using information published after the prediction decision time.
- An approved probability model additionally requires calibration evidence, appropriate coverage, governance capability, exact fees, executable prices and conservative lower-bound edge. `KALSHI_WEATHER_PROBABILITY` remains `UNAVAILABLE`, `probability_publishable=false`, `can_execute=false` until independently certified.

### Research progress SQL — no probability or write

```sql
-- One eligible pre-target as-of forecast per station, target hour and bucket.
-- Duplicate market threshold siblings and revisions must not multiply N.
WITH eligible AS (
  SELECT
    p.prediction_id,
    p.model_payload->'contract'->>'settlement_location_code' AS settlement_station,
    p.model_payload->'contract'->>'observation_window' AS target_window,
    p.model_payload->>'lead_time_bucket' AS lead_bucket,
    p.decision_time,
    p.central_estimate_f - o.settled_value AS signed_error_f
  FROM public.wow_kalshi_weather_predictions p
  JOIN public.wow_kalshi_weather_outcomes o USING (prediction_id)
  WHERE p.central_estimate_f IS NOT NULL
    AND o.settled_value IS NOT NULL
    AND p.decision_time IS NOT NULL
    AND o.settled_at > p.decision_time
    AND p.model_payload->'contract'->>'lane' = 'HOURLY_TEMPERATURE'
    AND p.model_payload->'contract'->>'observation_window' LIKE 'POINT_IN_TIME:%'
    AND p.model_payload->>'lead_time_bucket' IS NOT NULL
), independent_targets AS (
  SELECT *,
         ROW_NUMBER() OVER (
           PARTITION BY settlement_station, target_window, lead_bucket
           ORDER BY decision_time ASC, prediction_id ASC
         ) AS target_rank
  FROM eligible
)
SELECT
  settlement_station,
  lead_bucket,
  COUNT(*) AS independent_settled_target_hours,
  GREATEST(0, 30 - COUNT(*)) AS remaining_to_30_per_bucket,
  ROUND(AVG(ABS(signed_error_f))::numeric, 3) AS mae_f,
  ROUND(AVG(signed_error_f)::numeric, 3) AS signed_bias_f
FROM independent_targets
WHERE target_rank = 1
GROUP BY settlement_station, lead_bucket
ORDER BY settlement_station, lead_bucket;
```

This is a retrospective research-only progress check, not probability certification.
The 30/40 sample gates apply **within each** station/lane/horizon bucket, not
across buckets or model-version groups. Recheck target identity, as-of evidence,
source quality, and chronological holdout through the governed calibration tool
before treating any result as a qualifying research cohort.

**Failure modes:** GitHub schedules can be dropped; Open-Meteo HTTP 429 fails closed; Render Free may spin down, restart or exhaust shared hours; Supabase cron may be delayed; missing settlement points must not be silently imputed. All such failures reduce readiness, not the safety gate.
