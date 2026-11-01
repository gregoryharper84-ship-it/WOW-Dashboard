# Kalshi Weather — Trigger.dev Release B (research only)

**Scope:** manual recovery of existing Miami hourly settlement outcomes. No
historic forecast fabrication, new scheduler, model promotion, trades or
independent probability publication. Release A's browser key remains read-only.

## Boundaries

- `POST /research/backfill` accepts only `{"scope":"miami"}`, requires
  `WOW_KALSHI_WEATHER_BACKFILL_REQUEST_KEY`, returns a Trigger run ID (202).
- Render triggers `weather-research-backfill` using a **Trigger-only**
  `WOW_KALSHI_WEATHER_TRIGGER_SECRET_KEY` on the isolated Render service.
  Launches share a five-minute bucket idempotency key; this is **not** a
  global rate limiter. Task concurrency is one, with three retry attempts.
- Trigger.dev holds only `WOW_KALSHI_WEATHER_BACKFILL_WORKER_KEY`. Render holds
  the same key and validates it at its fixed worker endpoint.
- No Trigger task receives Supabase, writer API or trading secrets. Do not
  reuse `WOW_KALSHI_WEATHER_API_KEY` or the Release A research password.
- Render evaluates **pre-existing** Miami hourly predictions only (500-row
  scan, 90-day lookback, up to three attempts, 10-minute grace). Canonical
  Kalshi Weather Index observations alone may settle. Missing/non-final points
  remain unresolved; an advancing deterministic cursor avoids starvation.
- Immutable Supabase outcome identities prevent duplicate writes on retries.
  All receipts preserve `NO_PLAY_DATA_INSUFFICIENT`, null model probability,
  `probability_publishable=false`, and `can_execute=false`.

## Release and rollback

The Trigger.dev project ID is `proj_ywxmmbamgelgqoxbpbql`. From the **exact
accepted merge commit** in a trusted pnpm checkout:

```sh
pnpm install --frozen-lockfile
pnpm dlx trigger.dev@4.8.3 login
pnpm dlx trigger.dev@4.8.3 deploy
```

The owner must authorize CLI login and provision separately scoped production
credentials in Trigger.dev Cloud and Render. Never commit or print secrets.
Verify the deployed Trigger **production** environment, task version, and source
SHA, then deploy isolated Render (its `autoDeploy=off`) and verify Render SHA.

Request one run:

```sh
curl -fsS -X POST https://wow-kalshi-weather-v2.onrender.com/research/backfill \
  -H "Authorization: Bearer ${WOW_KALSHI_WEATHER_BACKFILL_REQUEST_KEY}" \
  -H 'Content-Type: application/json' --data '{"scope":"miami"}'
```

The 202 response means **accepted**, not completed. Confirm Cloud terminal
run receipt, actual Supabase outcome IDs, canonical source/target provenance,
and unchanged unique-prediction outcome count on a retry. Also verify missing
official index point, wrong credential and disabled-secret failure paths.
Until that is proved, do not call Release B operational.

Rollback: disable only `WOW_KALSHI_WEATHER_BACKFILL_REQUEST_KEY` on Render
and disable the one Trigger.dev task. Leave existing Supabase Cron, shadow
collector and append-only historical outcomes untouched.
