# Kalshi Weather V2 — Supabase capture trigger and calibration readiness

**Live scope:** dedicated Supabase project `kasbebndczmaopvcvfco`, isolated Render web
service `wow-kalshi-weather-v2.onrender.com`, Miami hourly contract lane first.

## Verified defect (2026-11-02)

The installed Supabase Cron job `kalshi-weather-v2-shadow-read-only-wake`
(`*/30 * * * *`) issued an unauthenticated **GET** to `/health`. That endpoint
proves the FastAPI process and the Supabase read path are reachable; it never
invokes the collection controller.

The isolated runtime runs with `WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_ENABLED=0`
because the shared 512-MiB WOW web process must never capture. Its
`/collector/readiness` therefore reported:

```json
{"trigger_installed": true, "startup_enabled": false, "collection_path": "AUTHENTICATED_POST_TRIGGER"}
```

The authenticated capture route existed and was installed, but **nothing called
it**. Cron receipts succeeded 78 times while the newest prediction stayed at
`2026-11-01 14:02:26Z`. Scheduler health was never evidence of data acquisition.

## Repair

`migrations/20261102_kalshi_weather_empirical_capture_cron.sql`:

- Adds `private.kalshi_weather_empirical_capture_tick()` (SECURITY DEFINER,
  empty `search_path`, execute revoked from `public`).
- Reads the cohort key from **Supabase Vault** (`vault.decrypted_secrets`,
  name `kalshi_weather_cohort_key`). It never embeds a key and never falls back
  to an unauthenticated wake.
- Issues `net.http_post` to
  `POST /internal/kalshi-weather/v17/empirical-cohort/cron-run` with the
  `X-WOW-Weather-Cohort-Key` header.
- Unschedules both old/current capture job names before scheduling one capture
  job (`*/30 * * * *`), so a second writer cannot be scheduled by accident.
- Keeps a distinctly non-collecting health wake at `15,45 * * * *`.

Runtime changes make the trigger result truthful:

- `bounded_cohort.derive_capture_status` separates `COLLECTED`,
  `PARTIAL`, `FAILED`, `NO_ELIGIBLE_TARGETS`, `NO_ELIGIBLE_CONTRACTS` and
  `DUPLICATE_SUPPRESSED`. A zero-row pass is never reported as a successful
  acquisition.
- The cron response carries `collection_diagnostics` with trigger, collector
  owner label, targets, predictions written, duplicate skips, snapshot counts,
  failure counts by stage, elapsed time, memory ratio,
  `latest_successful_capture_at` and `collection_fresh`.
- `/collector/readiness` exposes `collection_path`, `capture_trigger_installed`,
  `capture_trigger_path` and `capture_trigger_auth`.
- The external wake probe now requires the authenticated trigger rather than the
  optional in-process loop, and states that it does not prove a collection pass.

## Memory-safe operation on the 512 MiB service

Capture and settlement are separate workloads, and the capture path is what
runs on the shared small Render instance.

- `WOW_KALSHI_WEATHER_EMPIRICAL_LOW_MEMORY_MODE=1` keeps the cycle to one H0
  core capture, skipping market-microstructure recording, supplemental source
  snapshots, and the in-cycle settlement sweep.
- Deferred horizons are reported, never hidden: the run returns
  `EMPIRICAL_SHADOW_COHORT_DEFERRED_LOW_MEMORY_MODE` plus
  `low_memory_suppressed`, and the cron receipt exposes `low_memory_mode` and
  `low_memory_deferred_buckets`.
- No target, contract, or HTTP call is added by this mode; it only removes work.
- Do not enable low-memory mode on a process that is expected to produce full
  horizon coverage, and do not disable it on the 512 MiB service while the
  full side-feed path is unproven there.

## Bounded settlement backlog

Because capture suppresses settlement under memory pressure, settlement has its
own opt-in path: `POST /internal/kalshi-weather/v17/settlement-backlog/run`
(same `X-WOW-Weather-Cohort-Key` authentication).

- `WOW_KALSHI_WEATHER_SETTLEMENT_BACKLOG_ENABLED` defaults to `0`; while unset
  the route returns `409 KALSHI_WEATHER_SETTLEMENT_BACKLOG_DISABLED`. Enable it
  only on a deployment with headroom.
- `WOW_KALSHI_WEATHER_SETTLEMENT_BACKLOG_MAX_ATTEMPTS` defaults to `10` and is
  hard-clamped to `25` per call, regardless of configuration.
- Each run reports `pending_eligible`, `attempted`, `settled`,
  `remaining_after_run`, `truncated`, and per-prediction `failures` with status
  `SETTLEMENT_BACKLOG_CLEARED | COMPLETE | PARTIAL | FAILED`.
- Idempotent: a prediction that already has an official outcome is never
  settled again. Malformed settlement identity (empty index city) is excluded
  rather than attempted.
- The receipt is never evidence of capability; `probability_publishable=false`
  and `can_execute=false` are unconditional.

## Required owner action before the trigger can succeed

Supabase Vault currently holds **no secrets**. The migration fails closed with
`KALSHI_WEATHER_COHORT_KEY_MISSING_FROM_VAULT` until one is created:

```sql
-- Run in the Supabase SQL editor; paste the existing Render value when prompted.
select vault.create_secret('<existing WOW_KALSHI_WEATHER_COHORT_KEY value>',
                           'kalshi_weather_cohort_key',
                           'Authenticates the Supabase Cron capture trigger');
```

The value must equal the isolated runtime's `WOW_KALSHI_WEATHER_COHORT_KEY`
(Render → the weather service → Environment). Then apply the migration. Do not
commit the key, log it, or place it in chat.

## Verification (read only)

```sql
SELECT jobid, jobname, schedule, active FROM cron.job ORDER BY jobid;

SELECT runid, status, start_time, return_message
FROM cron.job_run_details
WHERE jobid = (SELECT jobid FROM cron.job
               WHERE jobname = 'kalshi-weather-v2-empirical-capture')
ORDER BY start_time DESC LIMIT 15;

SELECT id, status_code, timed_out, created
FROM net._http_response ORDER BY created DESC LIMIT 15;

SELECT COUNT(*) AS predictions, MAX(created_at) AS latest
FROM public.wow_kalshi_weather_predictions;
```

A pg_net `200` is still not collection proof. The capture endpoint body is the
evidence: read `collection_diagnostics.predictions_written`,
`collection_diagnostics.usable_capture` and
`collection_diagnostics.latest_successful_capture_at`. Expect Render logs:

```text
WOW_KALSHI_WEATHER_COLLECTION status=EMPIRICAL_SHADOW_COHORT_COLLECTED predictions_written=1 ...
```

`NO_ELIGIBLE_CONTRACTS`, `DUPLICATE_SUPPRESSED`, and
`DEFERRED_LOW_MEMORY_MODE` are non-collection outcomes, not evidence that a
new prediction was saved. A stale `latest_successful_capture_at` together with
repeated `FAILED` or `PARTIAL` statuses warrants investigation. A persistent
`NO_ELIGIBLE_CONTRACTS` or `DEFERRED_LOW_MEMORY_MODE` status warrants checking
market eligibility and the H0 time window.

**Rollback:** `SELECT cron.unschedule('kalshi-weather-v2-empirical-capture');`
then re-schedule the health wake only. Restore the previous migration if a full
revert is required. Do not drop shared extensions.

## Calibration gate

- One immutable forecast error per settlement station + hourly target time +
  lead-time bucket, from as-of forecasts and exact Kalshi index settlement.
  Threshold siblings are not independent samples.
- `fit_candidate_calibration_profile` requires ≥30 independent eligible samples
  per station/lane/horizon; at ≥40 the report also computes a chronological
  forward holdout. Research-only; neither certifies probability.
- `GET /internal/kalshi-weather/v17/calibration-report` now returns
  `dataset_integrity` (counts, per-bucket and per-station sample counts,
  duplicates collapsed, missing settlements, exclusion reasons, usable
  fraction, last decision time per station) alongside the candidate groups.
- Forward validation reports both the candidate-corrected MAE and the
  uncorrected `baseline_mae_f` on the same chronological holdout, plus
  `mae_improvement_f`, so an improvement claim stays falsifiable. Every
  validation block carries `certified=false`.
- Forecast availability timestamps remain unverified upstream. Strict
  point-in-time eligibility is not claimed where provenance is missing.
- `KALSHI_WEATHER_PROBABILITY` stays `UNAVAILABLE`, `probability_publishable=false`,
  `can_execute=false` until independent certification.

### Research progress SQL — no probability or write

```sql
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
SELECT settlement_station,
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

**Failure modes:** GitHub schedules can be dropped; Open-Meteo HTTP 429 fails
closed; Render Free may spin down or exhaust shared hours; Supabase cron may be
delayed; missing settlement points must not be silently imputed. All such
failures reduce readiness, never the safety gate.
