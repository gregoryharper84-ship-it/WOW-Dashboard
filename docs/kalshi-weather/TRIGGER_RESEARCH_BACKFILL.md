# Release B — Trigger Miami settlement research

Reconcile existing Miami hourly forecasts to **exact** Kalshi Weather Index.
No substitute station, new historical forecast, P(YES) or trading.
Missing/nonfinal points stay pending; an atomic **durable** cursor claims
successive three-attempt slices across launches (even when no official value
is available); immutable outcomes dedupe retries. Always `NO_PLAY_DATA_INSUFFICIENT`,
null P(YES)/P(NO), `probability_publishable=false`, `can_execute=false`.

Render secrets: `WOW_KALSHI_WEATHER_BACKFILL_REQUEST_KEY`,
`WOW_KALSHI_WEATHER_TRIGGER_SECRET_KEY`,
`WOW_KALSHI_WEATHER_BACKFILL_WORKER_KEY`. Trigger holds **only** worker key,
no database/trade credentials. Authenticated `POST /research/backfill`
with `{"scope":"miami"}` returns run ID, not completion. 5-min launch
dedupe is not rate limiting. Trigger concurrency 1/retries 3;
worker attempts 3, 500 rows/90 days, 10-minute finality grace.

**Required schema-first rollout (no auto-apply):** Independently review
`artifacts/wow-engine/migrations/20261103_kalshi_weather_research_progress.sql`
and apply it to the isolated **Kalshi Weather** database through the authorized
migration/release procedure **before** deploying the new Render worker.
The migration adds an RLS-protected, service-role-only progress table and one
security-invoker RPC that atomically claims the next eligible slice. It does
**not** modify predictions/outcomes or grant `anon`/`authenticated` access.
Missing RPC, invalid receipts, or database unavailability return HTTP 503
without attempting any settlement; do not bypass or substitute a clock-hash
cursor. A transactionally rolled-back test verified 0→3→6 claims and role
permissions in an isolated validation database; that is **not** production
deployment evidence.

After independent security/adoption and protected merge, deploy Trigger
`proj_ywxmmbamgelgqoxbpbql` (`pnpm dlx trigger.dev@4.8.3 deploy`)
and accepted main to Render (auto-deploy off). Independently verify
SHA, Trigger production terminal receipt, canonical target, original
prediction time, new Supabase outcome IDs, retry dedupe and error holds.
Rollback: disable request key/task and restore the prior accepted Render SHA; retain the
progress ledger for audit unless an authorized rollback plan explicitly removes it.
Preserve Cron and immutable prediction/outcome evidence.
