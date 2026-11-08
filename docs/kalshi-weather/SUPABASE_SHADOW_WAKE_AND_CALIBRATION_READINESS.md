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

Current production state (2026-11-08): `IMPLEMENTED_IN_PR` only. The durable dispatch queue exists in PR #2528 and is not applied to Supabase, not deployed to Render, and not verified. Production is unchanged: the health-only `kalshi-weather-v2-shadow-read-only-wake` job still fires, Render still runs `8857e488`, the empirical cohort still reports `status=DISABLED`, and there are still zero numeric probabilities.

## Why the previous wake never collected

The old read-only wake issued an unauthenticated `GET /health`, which cannot start the empirical cohort. The later authenticated POST design was rejected during security review: pg_net v0.12.0 follows redirects for POST and does not expose a documented per-request redirect-off switch, so a custom cohort-key header could be forwarded to a redirect target.

The replacement removes the outbound HTTP hop entirely. pg_cron inserts a durable, non-secret dispatch row into Postgres. The isolated Render process claims that row atomically with a service-role RPC and runs the existing bounded capture-only cycle locally. No URL, cohort key, bearer token, or provider credential is sent through pg_net or any redirect-following transport.

## Intended configuration once the migration is applied

Status: `IMPLEMENTED_IN_PR`.

- Replacement job: `kalshi-weather-v2-durable-collection-dispatch`, UTC cadence `*/30 * * * *`. Reapplying the migration removes the old health-only wake, the rejected authenticated HTTP trigger, and any prior copy of the durable job before installing exactly one schedule.
- Cron operation: insert one queue row per half-hour slot into `public.wow_kalshi_weather_collection_dispatch_queue`. A unique `scheduled_slot` plus `ON CONFLICT DO NOTHING` makes retries idempotent. The cron function returns only a dispatch ID, slot, and research-only authority flags.
- Transport security: no `pg_net` extension, outbound HTTP request, Vault URL lookup, cohort-key header, or secret in the cron command. The redirect risk is removed rather than mitigated with URL substring checks.
- Queue access: row-level security is enabled. `anon`, `authenticated`, and `public` receive no table privileges. The existing server-side `service_role` client receives only `SELECT` and `UPDATE`; its claim RPC is `SECURITY INVOKER`, has an empty search path, and is executable only by `service_role`.
- Single-consumer behavior: `claim_wow_kalshi_weather_collection_dispatch()` uses `FOR UPDATE SKIP LOCKED`. One Render replica can claim a given row. A claim older than 15 minutes can be recovered; three abandoned claims move the row to `FAILED` with `DISPATCH_MAX_ATTEMPTS_EXCEEDED`.
- Required on the existing isolated Render service (out of band, dashboard-managed; this service is not declared in `render.yaml`): `WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER=1`, `WOW_KALSHI_WEATHER_EMPIRICAL_DISPATCH_QUEUE_ENABLED=1`, `WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_ENABLED=0`, and optionally `WOW_KALSHI_WEATHER_EMPIRICAL_DISPATCH_POLL_SECONDS=60`. The queue consumer clamps polling to 15–300 seconds. Do not set both scheduler flags to 1; the app blocks that configuration.
- No cohort-key environment variable is needed for the scheduled queue path. The existing Render process continues to use its already configured server-side database credential; no database credential is written to SQL or sent to another host.
- The isolated Render process remains the sole bounded weather-capture and settlement owner. Queue results persist only bounded counts and typed status, not raw provider payloads. Every result remains `probability_publishable=false` and `can_execute=false`.
- The 30-minute schedule uses the existing Render process and existing Supabase database; it does not create another paid Render service. It is a cost-constrained research schedule, not high-availability certification.
- Until the SQL is applied, the Render queue flag is configured, and a completed queue row is read back, production remains unchanged. Do not claim a successful cycle from a green PR or a successful cron enqueue.

## Historical read-only evidence

On 2026-11-01 at approximately 02:57 UTC an explicit manual `net.http_get` against the same host returned **HTTP 200**, `DATABASE_READ_VERIFIED`, `can_execute=false` and `probability_publishable=false`. That is evidence the service answered a read-only request. It is **not** evidence of an authenticated collection cycle and says nothing about this migration. The former pg_net transport is historical only and is not part of the replacement.

## Migration definition (audit reference)

The source of truth is `artifacts/wow-engine/kalshi_weather_v2_collection_trigger_cron.sql`. Its security-relevant structure is:

```sql
CREATE TABLE public.wow_kalshi_weather_collection_dispatch_queue (
  id bigint generated always as identity primary key,
  scheduled_slot timestamptz not null unique,
  status text not null default 'PENDING',
  attempts integer not null default 0,
  claimed_at timestamptz,
  completed_at timestamptz,
  last_error_code text,
  result jsonb
);

ALTER TABLE public.wow_kalshi_weather_collection_dispatch_queue
  ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.wow_kalshi_weather_collection_dispatch_queue
  FROM public, anon, authenticated;
GRANT SELECT, UPDATE ON TABLE public.wow_kalshi_weather_collection_dispatch_queue
  TO service_role;

-- Claim is SECURITY INVOKER, service_role-only, and uses:
-- SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1
-- stale claim recovery: 15 minutes; abandoned claim ceiling: 3.

-- The cron function inserts one non-secret row per half-hour slot:
INSERT INTO public.wow_kalshi_weather_collection_dispatch_queue (scheduled_slot)
VALUES (v_slot)
ON CONFLICT (scheduled_slot) DO NOTHING;

SELECT cron.schedule(
  'kalshi-weather-v2-durable-collection-dispatch',
  '*/30 * * * *',
  $$select public.wow_kalshi_weather_trigger_collection();$$
);
```

The full migration additionally revokes the enqueue function from Data API roles, grants only the claim RPC to `service_role`, and retires both legacy wake names before installing the single durable schedule.

## Operator checklist — production verification

### 1. GitHub release preflight

- PR #2528 must pass exact-head CodeQL, SQL contract tests, runtime queue tests, independent QA, and protected Release Authority checks.
- Merge only the exact reviewed head. A later push invalidates prior acceptance.
- The migration is not applied by CI.

### 2. Render — isolated service configuration

- Confirm the isolated service identity and exact deployed revision.
- Set `WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER=1`.
- Set `WOW_KALSHI_WEATHER_EMPIRICAL_DISPATCH_QUEUE_ENABLED=1`.
- Keep `WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_ENABLED=0` to prevent a second writer.
- Use the default 60-second queue poll or an explicitly approved value from 15 to 300 seconds.
- No `WOW_KALSHI_WEATHER_COHORT_KEY` is required for scheduled dispatch. Do not add one for this queue design.
- Deploy the merged SHA. Render currently runs `8857e488`, which predates the queue consumer.

### 3. Supabase — queue permissions

- No Vault secrets are required for this scheduler path.
- The migration creates the durable queue table, enables RLS, revokes Data API user-role access, and grants the existing server-side `service_role` client the consumer permissions.
- Verify the claim RPC is executable only by `service_role` and uses `SECURITY INVOKER` with an empty search path.

### 4. Supabase — apply and read back the migration

- Apply the reviewed SQL file to project `kasbebndczmaopvcvfco` only after protected merge approval.
- Verify exactly one active `kalshi-weather-v2-durable-collection-dispatch` row with cadence `*/30 * * * *`.
- Verify both legacy wake job names are absent.
- Verify the queue table, RLS state, table grants, and claim-function grants.
- Confirm the cron command contains only the schema-qualified queue function call; it must contain no URL, header, token, or secret.

### 5. Verify the first real collection cycle

- Wait for a cron slot to insert a `PENDING` row.
- Confirm the isolated Render process logs `WOW_KALSHI_WEATHER_DISPATCH_QUEUE status=STARTED`.
- Confirm exactly one worker claims the row and logs `WOW_KALSHI_WEATHER_DISPATCH status=COMPLETED` with its dispatch ID and bounded counts.
- Read back the matching queue row as `COMPLETED`, with one claim attempt, a completion timestamp, and a count-only result summary.
- Confirm a matching immutable shadow-evidence row was created by the bounded collector. Record its exact row ID and timestamp.
- Confirm `probability_publishable=false` and `can_execute=false`. A completed dispatch is not model certification or permission to trade.

### 6. Stability check

- Observe at least two consecutive half-hour slots.
- Confirm one queue row per slot, no duplicate claims, no stuck `CLAIMED` rows, no max-attempt failures, no second in-process writer, and no memory-pressure regression.
- Confirm no unbounded history replay, calibration mutation, model promotion, or production probability issuance occurred.

### Stop conditions

- Missing Render ownership or queue flag: leave rows pending; do not enable the legacy loop automatically.
- Both scheduler flags enabled: startup must log `BLOCKED_MULTIPLE_WRITERS`; stop and correct configuration.
- A claim remains `CLAIMED` for more than 15 minutes: inspect worker health before retrying.
- `FAILED` with `DISPATCH_MAX_ATTEMPTS_EXCEEDED`: investigate before manually requeueing.
- No new immutable evidence row: dispatch completion alone is insufficient; investigate acquisition/persistence.
- Any probability-publication or execution-authority field becomes true: stop immediately and preserve evidence.

## Verification (read only)

After the migration is applied, these read-only queries establish the scheduler and queue state:

```sql
-- Exactly one active durable schedule; neither old wake may remain.
select jobid, jobname, schedule, active, command
from cron.job
where jobname in (
  'kalshi-weather-v2-shadow-read-only-wake',
  'kalshi-weather-v2-authenticated-collection-trigger',
  'kalshi-weather-v2-durable-collection-dispatch'
)
order by jobname;

-- Recent queue state; result contains bounded counts only.
select id, scheduled_slot, status, attempts, created_at, claimed_at,
       completed_at, last_error_code, result
from public.wow_kalshi_weather_collection_dispatch_queue
order by scheduled_slot desc
limit 10;

-- Cron execution history.
select jobid, runid, status, start_time, end_time, return_message
from cron.job_run_details
where jobid in (
  select jobid from cron.job
  where jobname = 'kalshi-weather-v2-durable-collection-dispatch'
)
order by start_time desc
limit 10;

-- Table grants for the queue.
select grantee, privilege_type
from information_schema.role_table_grants
where table_schema = 'public'
  and table_name = 'wow_kalshi_weather_collection_dispatch_queue'
order by grantee, privilege_type;

-- Claim/enqueue function grants.
select routine_name, grantee, privilege_type
from information_schema.routine_privileges
where routine_schema = 'public'
  and routine_name in (
    'claim_wow_kalshi_weather_collection_dispatch',
    'wow_kalshi_weather_trigger_collection'
  )
order by routine_name, grantee, privilege_type;
```

Do not use `net._http_response` as evidence for the new design; there is no outbound HTTP request. The queue row, Render claim/completion log, and matching immutable evidence row are the end-to-end proof.

Rollback is explicit: disable `WOW_KALSHI_WEATHER_EMPIRICAL_DISPATCH_QUEUE_ENABLED` on the isolated Render service and unschedule `kalshi-weather-v2-durable-collection-dispatch`. Keep the queue table as an audit record; do not silently restore the old health-only wake or credential-bearing pg_net POST.

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
