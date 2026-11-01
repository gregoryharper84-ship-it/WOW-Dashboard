# Release B — Trigger Miami settlement research

Reconcile existing Miami hourly forecasts to **exact** Kalshi Weather Index.
No substitute station, new historical forecast, P(YES) or trading.
Missing/nonfinal points stay pending; rotating scans avoid starvation;
immutable outcomes dedupe retries. Always `NO_PLAY_DATA_INSUFFICIENT`,
null P(YES)/P(NO), `probability_publishable=false`, `can_execute=false`.

Render secrets: `WOW_KALSHI_WEATHER_BACKFILL_REQUEST_KEY`,
`WOW_KALSHI_WEATHER_TRIGGER_SECRET_KEY`,
`WOW_KALSHI_WEATHER_BACKFILL_WORKER_KEY`. Trigger holds **only** worker key,
no database/trade credentials. Authenticated `POST /research/backfill`
with `{"scope":"miami"}` returns run ID, not completion. 5-min launch
dedupe is not rate limiting. Trigger concurrency 1/retries 3;
worker attempts 3, 500 rows/90 days, 10-minute finality grace.

After independent security/adoption and protected merge, deploy Trigger
`proj_ywxmmbamgelgqoxbpbql` (`pnpm dlx trigger.dev@4.8.3 deploy`)
and accepted main to Render (auto-deploy off). Independently verify
SHA, Trigger production terminal receipt, canonical target, original
prediction time, new Supabase outcome IDs, retry dedupe and error holds.
Rollback: disable request key/task; preserve Cron and immutable evidence.
