# Kalshi Weather — Trigger.dev research backfill (Release B, research-only)

Scope: **manual Miami hourly settlement reconciliation**. The previously merged
Release A dashboard remains read-only. Existing isolated Render service and
Supabase prediction/outcome ledgers remain the only system of record.

## What this actually does

1. A caller with a **dedicated request credential** sends `POST /research/backfill`
   to the isolated Render weather API (not to Trigger.dev directly).
2. Render accepts only `{"scope":"miami"}` and triggers
   `weather-research-backfill` in Trigger.dev. Repeated launch requests within
   the same five-minute bucket use the same Trigger idempotency key.
3. Trigger.dev runs **one task, maximum concurrency 1**, retries transient
   failures, and sends a fixed-host POST to
   `/internal/research/miami-settlement-reconcile` with a **different**
   worker credential.
4. Render examines only already-recorded Miami hourly index predictions, requires
   a pre-target decision timestamp, checks a 10-minute settlement grace period,
   and bounds each run to three attempts and a 90-day lookback over 500 rows.
5. The existing canonical settlement resolver fetches the exact Kalshi Weather
   Index observation and writes the existing immutable outcome row, with raw
   source evidence and reference to the frozen rule/prediction. Existing
   outcomes are skipped; missing/non-final observations stay unresolved.
6. Trigger.dev returns a receipt with outcome IDs or typed gaps. A successful
   Trigger run is **not** model certification, a recommendation, or proof of a
   newly available Kalshi settlement observation.

**No historical forecast generation.** Current/revised archives are not used to
manufacture point-in-time predictions. No new scheduler, writer, training policy,
model promotion, trades, order API, or probability endpoint is installed.
All output explicitly preserves `NO_PLAY_DATA_INSUFFICIENT`,
`probability_publishable=false`, and `can_execute=false`.

## Required credentials — separate scopes, never commit values

| Location | Environment variable | Role |
|---|---|---|
| Render isolated Weather API | `WOW_KALSHI_WEATHER_BACKFILL_REQUEST_KEY` | Authorize user/plugin launch only |
| Render isolated Weather API | `WOW_KALSHI_WEATHER_TRIGGER_SECRET_KEY` | Trigger.dev **production Trigger-only** API key |
| Render isolated Weather API | `WOW_KALSHI_WEATHER_BACKFILL_WORKER_KEY` | Authorize only internal settlement reconciliation |
| Trigger.dev Cloud production environment | `WOW_KALSHI_WEATHER_BACKFILL_WORKER_KEY` | Identical limited worker key; no Supabase service role |

Do not reuse `WOW_KALSHI_WEATHER_API_KEY` (live shadow-writer key),
`WOW_KALSHI_WEATHER_RESEARCH_KEY` (read-only dashboard Basic password),
or any Trigger.dev root/admin key. Do not add secrets to GitHub Actions.

If a credential is missing, the corresponding endpoint fails closed with
503; wrong credentials give 401.

## Setup and release

The Trigger.dev project ID is `proj_ywxmmbamgelgqoxbpbql`, reused from
the earlier **closed, unmerged** Trigger proof-of-concept PR #2359.
Only this single task is carried forward. At the repository root:

```bash
pnpm install --frozen-lockfile
pnpm dlx trigger.dev@4.8.3 login
# Checkout the exact reviewed merge SHA before cloud deployment.
git rev-parse HEAD
pnpm dlx trigger.dev@4.8.3 deploy
```

Deploy into the verified **production** environment after the corresponding
protected GitHub PR has passed checks and merged. Save the exact local commit
SHA, Trigger.dev deployment ID/version and environment in the release receipt;
independently compare that SHA to the protected GitHub merge. Do not accept an
unattributed cloud run or claim deployment parity from a developer smoke test. Verify the task is visible
under the correct Trigger.dev project/environment and review its run permissions.
The CLI login must be completed by the account owner. Trigger.cloud development
smoke runs or a GitHub merge do **not** establish production deployment.

Deploy the isolated Render weather service from the verified merge SHA, with the
three Render environment variables above. It has `autoDeploy=off`, so merging
does not automatically deploy it. Keep the existing Supabase Cron wake and the
current bounded shadow collector unchanged.

Example owner launch after deployments/configuration:

```bash
curl -fsS -X POST https://wow-kalshi-weather-v2.onrender.com/research/backfill \
  -H "Authorization: Bearer ${WOW_KALSHI_WEATHER_BACKFILL_REQUEST_KEY}" \
  -H 'Content-Type: application/json' \
  --data '{"scope":"miami"}'
```

The response is HTTP 202 with a Trigger run ID, **not** a completed backfill.
Verify that run in Trigger.dev Cloud and independently read Supabase
`wow_kalshi_weather_outcomes` for newly persisted outcome IDs. Replay the
same request and verify unique `prediction_id` rows do not multiply. Confirm
raw indexed evidence, target timestamps, source identity, and absence of
probability publication or trades. Test a missing/unfinished index point and a
missing-secret configuration as separate negative cases.

Release acceptance requires: targeted Python and frozen pnpm tests passing at
exact PR head, correct credential scoping, independent review, registered cloud
production task, Render SHA parity, a real bounded Miami run receipt, and
independently validated unique Supabase outcome records. Until all are verified
**do not claim a production deployment or a completed backfill.**

## Rollback

Disable launch by removing only `WOW_KALSHI_WEATHER_BACKFILL_REQUEST_KEY`
from the isolated Render service; disable/cancel this task in Trigger.dev.
Do not disable existing weather shadow capture/Supabase Cron. Existing immutable
outcomes remain as provenance records and must not be deleted to "rollback"
research activity.

## Readiness gate

The previous strict **30 unique settled target-hours per lead bucket**
minimum remains unchanged. This job only recovers missing settlement evidence
from predictions that were already captured before target time. It cannot
retroactively create the missing historical forecast snapshots or independently
certify forecast issue-time availability.
