# Kalshi Weather / Trigger.dev — Release B

**Research only.** Reconcile pre-existing Miami hourly prediction outcomes against
the exact Kalshi Weather Index. No historical forecast generation, schedule,
trades, probability certification or source substitution.

## Auth and isolation

- `POST /research/backfill`: scoped
  `WOW_KALSHI_WEATHER_BACKFILL_REQUEST_KEY`; JSON `{"scope":"miami"}`.
  Returns a Trigger run ID, **not** a completed job.
- Render holds Trigger-only `WOW_KALSHI_WEATHER_TRIGGER_SECRET_KEY`.
- Trigger Cloud holds only `WOW_KALSHI_WEATHER_BACKFILL_WORKER_KEY`, also
  configured on Render for the fixed worker endpoint. No Supabase/writer/trade
  credentials are given to Trigger.
- Five-minute launch deduplication is **not** a global rate limiter.
  Trigger concurrency=1, retries=3. Each worker call: maximum 3 attempts,
  500-row scan, 90-day lookback, 10-minute settlement grace.
- Missing/non-final official index observations remain missing; the scan rotates
  past stalled candidates. Immutable Supabase outcomes prevent duplicates.
- Every result remains `NO_PLAY_DATA_INSUFFICIENT`, null P(YES)/P(NO),
  `probability_publishable=false`, `can_execute=false`.

## Deploy from accepted exact source

Project: `proj_ywxmmbamgelgqoxbpbql`. The owner must authenticate and supply
the above separately scoped secrets in Render and Trigger Cloud.

```sh
pnpm install --frozen-lockfile
pnpm dlx trigger.dev@4.8.3 login
pnpm dlx trigger.dev@4.8.3 deploy
```

Record accepted GitHub merge SHA, Trigger **production** deployment ID/version
and Render live SHA. Render `autoDeploy=off`; deploy it explicitly after merge.

```sh
curl -fsS -X POST https://wow-kalshi-weather-v2.onrender.com/research/backfill \
 -H "Authorization: Bearer ${WOW_KALSHI_WEATHER_BACKFILL_REQUEST_KEY}" \
 -H 'Content-Type: application/json' --data '{"scope":"miami"}'
```

Independently verify Cloud terminal receipt, new Supabase outcome IDs, original
pre-event prediction times, exact index target and retry deduplication. Check
wrong credentials and missing/non-final official observations. No operational
completion claim before production proof.

**Rollback:** disable only the Render request key and Trigger task. Do not
change existing Cron/shadow capture or delete immutable evidence.
