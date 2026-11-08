# Kalshi Weather V2 — Supabase shadow wake and calibration readiness

**Live scope:** dedicated Supabase project `kasbebndczmaopvcvfco`, existing isolated Render web service `srv-dahijm5g1s2s73bbvkn0`, Miami hourly contract lane only.

## Status vocabulary

Every statement below carries exactly one of these states. Do not collapse them: a merged PR is not an applied migration, and an applied migration is not a verified production cycle.

| State | Meaning | Evidence required |
| --- | --- | --- |
| `IMPLEMENTED_IN_PR` | Code or migration exists in an open pull request | PR number and exact head SHA |
| `APPROVED_AND_MERGED` | Protected checks passed and the pull request merged | Merge SHA |
| `MIGRATION_APPLIED` | The SQL actually ran against Supabase `kasbebndczmaopvcvfco` | Read back the `cron.job` row and enabled extensions |
| `PRODUCTION_VERIFIED` | A real scheduled cycle authenticated, persisted new immutable evidence, and no duplicate writer ran | `cron.job_run_details`, `net._http_response.status_code = 200`, new immutable row ID, matching Render log line |

Current production state (2026-11-08): `IMPLEMENTED_IN_PR` only. The authenticated trigger exists in PR #2528 and is not applied to Supabase, not deployed to Render, and not verified. Production is unchanged: the health-only `kalshi-weather-v2-shadow-read-only-wake` job still fires, Render still runs `8857e488`, the empirical cohort still reports `status=DISABLED`, and there are still zero numeric probabilities.

## Why the previous wake never collected

The read-only wake issued an unauthenticated `GET /health`, which cannot start the empirical cohort. The only collection entrypoint is the authenticated `POST /internal/kalshi-weather/v17/empirical-cohort/cron-run`, so the cohort stayed at `status=DISABLED` and wrote no shadow rows no matter how reliably the job fired. A scheduled `GET /health` returning `200` is a liveness signal with no collection authority; it must never be read as a successful collection cycle.

## Intended configuration once the migration is applied

Status: `IMPLEMENTED_IN_PR`.

- Replacement job `kalshi-weather-v2-authenticated-collection-trigger`, UTC cadence `*/30 * * * *` (two times per hour). Applying the file twice is safe: it unschedules every existing row with that name before installing exactly one job.
- Only operation: authenticated HTTPS **POST** to `/internal/kalshi-weather/v17/empirical-cohort/cron-run` with header `X-WOW-Weather-Cohort-Key`; `90,000 ms` maximum HTTP timeout; User-Agent `kalshi-supabase-cron-collection/1`; empty JSON body.
- Credentials are read from Supabase Vault at dispatch time and are never embedded in `cron.job.command`: `wow_kalshi_weather_service_url` and `wow_kalshi_weather_cohort_key`.
- Three fail-closed guards run before any dispatch: missing service URL, missing cohort key, and a service URL that is not exactly `https://wow-kalshi-weather-v2.onrender.com`. The exact hostname was read from the live Render service configuration. A prefix allowlist is deliberately not used. Each blocked path emits a PostgreSQL `WARNING` with its typed reason (never the secret value), returns `BLOCKED`, and dispatches nothing.
- Destination identity: the migration accepts only the exact verified HTTPS origin above. Any other hostname, suffix, explicit port, path, embedded credentials or cleartext URL is blocked before the cohort key is attached.
- Required on the isolated Render service (out of band, dashboard-managed — this service is not declared in `render.yaml`): `WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER=1` (without it the trigger route is not installed and the POST returns 404), `WOW_KALSHI_WEATHER_COHORT_KEY` matching the Vault secret, `WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_ENABLED=0` so the in-process loop is not a second writer, and a deploy of the merged SHA — Render currently runs `8857e488`, which does not contain this trigger.
- No Supabase service key, Kalshi API secret, alternate collector, order controller, probability issuance, or capability promotion.
- The existing Render process retains sole bounded weather-capture and settlement responsibility, guarded by `WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER`. Keep the GitHub external wake workflow as *best-effort backup*, not an independent data writer.
- Unlike uninterrupted ten-minute pings, a 30-minute wake deliberately allows the Free Render process to sleep between bursts to limit consumption of the Render workspace's shared **750 monthly free instance hours**. It is a **cost-constrained research schedule**, not high-availability certification.

## Historical read-only evidence

On 2026-11-01 at approximately 02:57 UTC an explicit manual `net.http_get` against the same host returned **HTTP 200**, `DATABASE_READ_VERIFIED`, `can_execute=false` and `probability_publishable=false`. That is evidence the service answered a read-only request. It is **not** evidence of an authenticated collection cycle and says nothing about this migration.

## Migration definition (audit reference)

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
    RAISE WARNING 'KALSHI_WEATHER_COLLECTION_BLOCKED reason=%', 'KALSHI_WEATHER_SERVICE_URL_SECRET_MISSING';
    RETURN jsonb_build_object('status','BLOCKED',
      'reason','KALSHI_WEATHER_SERVICE_URL_SECRET_MISSING',
      'probability_publishable',false,'can_execute',false);
  END IF;

  IF nullif(trim(v_cohort_key), '') IS NULL THEN
    RAISE WARNING 'KALSHI_WEATHER_COLLECTION_BLOCKED reason=%', 'KALSHI_WEATHER_COHORT_KEY_SECRET_MISSING';
    RETURN jsonb_build_object('status','BLOCKED',
      'reason','KALSHI_WEATHER_COHORT_KEY_SECRET_MISSING',
      'probability_publishable',false,'can_execute',false);
  END IF;

  v_base_url := rtrim(btrim(v_base_url), '/');

  -- Destination guard: TLS on the isolated Render origin, before the key is sent.
  IF v_base_url !~* '^https://wow-kalshi-weather-v2\.onrender\.com$' THEN
    RAISE WARNING 'KALSHI_WEATHER_COLLECTION_BLOCKED reason=%', 'KALSHI_WEATHER_SERVICE_URL_NOT_APPROVED';
    RETURN jsonb_build_object('status','BLOCKED',
      'reason','KALSHI_WEATHER_SERVICE_URL_NOT_APPROVED',
      'probability_publishable',false,'can_execute',false);
  END IF;

  v_request_id := net.http_post(
    url := v_base_url || '/internal/kalshi-weather/v17/empirical-cohort/cron-run',
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
    'destination_host',split_part(v_base_url, '/', 3),
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

-- Idempotent install: remove every prior row with this name, then add exactly one.
DO $dedupe$
DECLARE
  v_job_id bigint;
BEGIN
  FOR v_job_id IN
    SELECT jobid FROM cron.job
    WHERE jobname = 'kalshi-weather-v2-authenticated-collection-trigger'
  LOOP
    PERFORM cron.unschedule(v_job_id);
  END LOOP;
END;
$dedupe$;

SELECT cron.schedule(
  'kalshi-weather-v2-authenticated-collection-trigger',
  '*/30 * * * *',
  $$SELECT public.wow_kalshi_weather_trigger_collection();$$
);
```

**Not applied.** Run this SQL manually against Supabase `kasbebndczmaopvcvfco` (not through GitHub Actions) only after the pull request merges and the owner authorizes the migration. Until then this document is the intended configuration and verification contract, not a description of production.

## Operator checklist — production verification

**Do not start this checklist until PR #2527 and PR #2528 are merged with their exact accepted heads.** The current production state is still `IMPLEMENTED_IN_PR`; these steps are a controlled release procedure, not permission to bypass protected acceptance.

### 1. GitHub release preflight

- [ ] Confirm PR #2527 and #2528 each show the expected merge commit and are based on the accepted `main` lineage.
- [ ] Confirm Independent QA and Release Authority are green on the exact final heads, not an earlier commit.
- [ ] Confirm required regressions, governed backend, CodeQL and applicable review findings are complete.
- [ ] Record both merge SHAs. Do not deploy a branch head or a stale pre-merge SHA.

### 2. Render — isolated service configuration

Open Render Dashboard → `wow-kalshi-weather-v2` → **Environment**.

- [ ] Confirm the service origin is exactly `https://wow-kalshi-weather-v2.onrender.com`.
- [ ] Set `WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER=1` so the authenticated collection route is installed.
- [ ] Set `WOW_KALSHI_WEATHER_COHORT_KEY` to a newly generated high-entropy secret. Do not paste it into GitHub, chat, source files, SQL text, or logs.
- [ ] Keep `WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_ENABLED=0`; the database scheduler remains the sole collection writer.
- [ ] Save the environment and manually deploy the accepted merged SHA. Auto-deploy is disabled for this service.
- [ ] Verify the completed live deploy reports the exact expected SHA. A successful deploy status without SHA parity is insufficient.

### 3. Supabase — Vault secrets

Open Supabase Dashboard → project `kasbebndczmaopvcvfco` → **Vault / Secrets**.

- [ ] Create `wow_kalshi_weather_service_url` with exactly `https://wow-kalshi-weather-v2.onrender.com` (no trailing path, port, query, or alternate hostname).
- [ ] Create `wow_kalshi_weather_cohort_key` with the exact same secret value configured on Render.
- [ ] Confirm both secret names exist. Never expose or query decrypted secret values for verification.

### 4. Supabase — apply and read back the migration

Only after the accepted Render SHA is live and both Vault secrets exist:

- [ ] Open Supabase SQL Editor for project `kasbebndczmaopvcvfco`.
- [ ] Run the complete `kalshi_weather_v2_collection_trigger_cron.sql` from the merged `main` SHA.
- [ ] Read back `cron.job`: exactly one active `kalshi-weather-v2-authenticated-collection-trigger` row must exist.
- [ ] Confirm `kalshi-weather-v2-shadow-read-only-wake` is absent.
- [ ] Confirm there is no duplicate job-name row and both `pg_cron` and `pg_net` extensions are present.
- [ ] If any check fails, stop. Do not manually create a second scheduler or enable the in-process loop as a workaround.

### 5. Verify the first real collection cycle

Wait for the next `*/30` scheduled run, then inspect:

- [ ] `cron.job_run_details` for a new run of the authenticated trigger.
- [ ] `net._http_response` for the corresponding request time and HTTP status. HTTP 401 means the Render and Vault keys differ; 404 means the route is not installed; 503 means the Render key/configuration is unavailable. A `BLOCKED` warning in PostgreSQL logs identifies a missing or unapproved Vault configuration.
- [ ] Render logs for `WOW_KALSHI_WEATHER_EMPIRICAL_COHORT status=EMPIRICAL_SHADOW_COHORT_PASS`.
- [ ] Supabase `wow_kalshi_weather_predictions` for a new immutable prediction ID with `created_at` after that collection cycle.
- [ ] Verify source-snapshot IDs and the prediction's settlement identity are present. A health response, pg_cron success, HTTP 200, or `DISPATCHED` request ID alone is not proof of collection.

### 6. Stability check

- [ ] Observe at least two further scheduled cycles.
- [ ] Confirm exactly one collection scheduler remains active and the GitHub health probe remains read-only.
- [ ] Confirm fresh immutable rows are added without duplicate target-hour residuals or a second writer.
- [ ] Confirm `probability_publishable=false` and `can_execute=false` remain in force. Collection restoration is not probability certification or trading authorization.

### Stop conditions

Stop and retain `NO_PLAY_DATA_INSUFFICIENT` if the destination is not the exact approved host, a credential mismatch occurs, the route returns 404/503, no new immutable row appears, deployed SHA differs, duplicate schedulers exist, or the cohort reports `DISABLED`. Do not weaken authentication, turn on a second writer, or infer success from a liveness check.

## Verification (read only)

These queries are expected to show the old health-only job until the migration is applied. A successful SQL client exit is not `MIGRATION_APPLIED`; read the rows back.

```sql
SELECT jobid,jobname,schedule,active,command
FROM cron.job WHERE jobname = 'kalshi-weather-v2-authenticated-collection-trigger';

-- After the migration the retired wake must be absent. Before it, this query
-- returns the job that is still firing.
SELECT jobid,jobname,active
FROM cron.job WHERE jobname = 'kalshi-weather-v2-shadow-read-only-wake';

-- Exactly one row per job name. More than one means the migration installed a
-- duplicate writer and must be repaired before trusting any cycle.
SELECT jobname,COUNT(*) FROM cron.job GROUP BY jobname HAVING COUNT(*) > 1;

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

Confirm the request was authenticated, not merely dispatched. `net._http_response.status_code` must be `200`; a `401` means the Vault secret and `WOW_KALSHI_WEATHER_COHORT_KEY` disagree, a `404` means `WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER=1` is not set on the isolated service, and a `503` means the Render key is unset. A `BLOCKED` receipt with `KALSHI_WEATHER_SERVICE_URL_NOT_APPROVED` means the Vault URL is not the approved TLS origin. Then verify that new Render logs show `WOW_KALSHI_WEATHER_EMPIRICAL_COHORT status=EMPIRICAL_SHADOW_COHORT_PASS` and a new immutable shadow row *after* the DB-driven trigger. A pg_net HTTP 200 alone does not certify collection.

**Rollback:** `SELECT cron.unschedule('kalshi-weather-v2-authenticated-collection-trigger');` (specific job only; do not drop shared extensions). Rolling back to the previous read-only wake restores a job that cannot collect, so treat any rollback as returning the lane to `NO_PLAY_DATA_INSUFFICIENT`.

## Calibration gate

- Candidate fitting requires at least **30 unique eligible residuals per settlement-identity key + lane + lead-time bucket**. Threshold sibling contracts are one target observation, not independent samples; revisions collapse to the earliest decision-time forecast.
- The authoritative forward-validation scorecard requires **50 total unique residuals**, with at least **30 chronologically earlier training residuals** and **20 later holdout residuals**. The split is by settlement time; boundary ties stay in holdout. It checks holdout absolute mean error `<= 0.75°F`, MAE `<= 2.5°F`, and one-sigma coverage of `50–85%`.
- The legacy `build_calibration_report()` validation fields use a separate holdout calculation. They are not the authoritative pass/fail result for the new forward-validation thresholds; use `forward_validation.py` and its explicit gate objects.
- A passing residual scorecard is research-only. It does not certify a probability, permit publication, promote a capability, or authorize execution. Separate probability-level review still requires Brier score, log loss, probability-bucket calibration, and comparison against a no-skill/base-rate and other precommitted baselines.
- Miami contracts currently carry `settlement_location_code = KALSHI_WEATHER_INDEX:MIAMI` with `settlement_station_id` and `settlement_station_name` null. The settlement object is the Kalshi Weather Index city, so the code can group index-city research residuals; it is **not** proof of an exact physical station. The scorecard must label this `INDEX_CITY`, and station-specific promotion stays blocked until a real station identity is recorded.
- Report MAE, signed bias, sample size, source outages/429, holdout scores and settlement identity; do not fit or backfill using information published after the prediction decision time.
- An approved probability model additionally requires calibrated probability-level evidence, appropriate coverage, governance capability, exact fees, executable prices and conservative lower-bound edge. `KALSHI_WEATHER_PROBABILITY` remains `UNAVAILABLE`, `probability_publishable=false`, `can_execute=false` until independently certified.

### Research progress SQL — no probability or write

```sql
-- One eligible pre-target as-of forecast per settlement identity, target hour and bucket.
-- Duplicate market threshold siblings and revisions must not multiply N.
WITH eligible AS (
  SELECT
    p.prediction_id,
    p.model_payload->'contract'->>'settlement_location_code' AS settlement_group_key,
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
           PARTITION BY settlement_group_key, target_window, lead_bucket
           ORDER BY decision_time ASC, prediction_id ASC
         ) AS target_rank
  FROM eligible
)
SELECT
  settlement_group_key,
  lead_bucket,
  COUNT(*) AS independent_settled_target_hours,
  GREATEST(0, 30 - COUNT(*)) AS remaining_to_30_per_bucket,
  ROUND(AVG(ABS(signed_error_f))::numeric, 3) AS mae_f,
  ROUND(AVG(signed_error_f)::numeric, 3) AS signed_bias_f
FROM independent_targets
WHERE target_rank = 1
GROUP BY settlement_group_key, lead_bucket
ORDER BY settlement_group_key, lead_bucket;
```

This is a retrospective research-only progress check, not probability certification.
The 30-sample candidate-fit and 50-sample forward-validation gates apply **within each** settlement-identity/lane/horizon bucket, not
across buckets or model-version groups. Recheck target identity, as-of evidence,
source quality, and chronological holdout through the governed calibration tool
before treating any result as a qualifying research cohort.

**Failure modes:** GitHub schedules can be dropped; Open-Meteo HTTP 429 fails closed; Render Free may spin down, restart or exhaust shared hours; Supabase cron may be delayed; missing settlement points must not be silently imputed. All such failures reduce readiness, not the safety gate.
