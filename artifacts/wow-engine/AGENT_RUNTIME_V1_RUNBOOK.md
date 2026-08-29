# WOW Agent Runtime V1 — Staging/Recovery Runbook

Status: `SPECIALIST_CONTRACTS_PRESENT / PERSISTENT_MULTI_AGENT_RUNTIME_NOT_YET_CERTIFIED`

Safety invariants:

- `can_execute=false`
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`
- no LLM, market line, L5/L10 rate, or third-party probability may replace a missing fitted model
- zero survivors is valid
- no downstream result may upgrade an upstream blocker

## Deployment boundary

This branch prepares Phases 1–5 in code. It does not authorize production deployment, production Supabase mutation, probability publication, or live execution. The private `wow` orchestration migration must be reviewed and applied in staging before any production migration is considered.

## Staging order

1. Create a staging Supabase database/branch or other approved isolated database target.
2. Apply `agent_runtime_v1/migration.sql` only to that isolated target.
3. Verify the `wow` schema is not exposed by the Supabase Data API; `anon` and `authenticated` must have no privileges on internal tables.
4. Provision Render Key Value `wow-jobs` on a persistent paid plan with `maxmemoryPolicy=noeviction` and external access disabled.
5. Deploy both API and worker from the exact same branch SHA. Keep auto-deploy OFF until CI gates and staging acceptance are complete.
6. Set `SUPABASE_DB_URL`, `WOW_ACTION_API_KEY`, and internal `REDIS_URL` through Render secrets/service references only. Never expose these values to clients or logs.
7. Run `/health/live`, then `/health/ready`.
8. Create an authenticated `POST /wow/runs` request with an Idempotency-Key and `can_execute=false`.
9. Poll `/wow/runs/{id}/manifest`. A nonterminal manifest with zero survivors is still nonterminal.
10. Exercise duplicate delivery, worker restart, transient retry, missing-model, and reconciliation fixtures before considering Phase 2 complete.

## Fitted-model rule

The first production-eligible fitted lane is not selected by this runtime scaffolding. `wow.controlling-model` must return `MODEL_UNAVAILABLE` until exactly one active capability record points to a hash-verified certified fitted artifact and eligible calibrator. No synthetic/staging provider may be promoted as production evidence.

## Polling contract

Create returns HTTP 202 with stable `run_id` for the same `(Idempotency-Key, canonical request hash)` pair. Polling returns run status/stage plus rows discovered, terminal, and pending. Terminal manifests must include balanced reconciliation:

`rows_in = rows_completed + rows_held + rows_rejected`

All protected `/wow/runs*` endpoints reuse the existing `WOW_ACTION_API_KEY` Bearer authentication boundary. `/health/live` and `/health/ready` expose no secrets.

## Rollback

Application rollback: redeploy the previous known-good Render deploy/SHA for the web service and worker. Do not destructively roll back prediction/evidence history.

Database rollback: the Agent Runtime migration is additive. If staging must revert, stop API/worker traffic first. Prefer leaving the private `wow` schema in place but unused while application code is rolled back. Do not drop tables containing run/audit evidence merely to make rollback look clean.

Queue recovery: stop workers before changing broker configuration. Preserve durable Postgres job state as authority; Valkey is transport/orchestration state, not the immutable run ledger. Requeue only jobs whose persisted states make retry legal.

## Production prerequisites

Before production mutation/deployment: full repository regression CI, Agent Runtime unit/contract/integration tests, migration validation, Supabase allow/deny tests and advisor review, Render Blueprint validation, dependency/secret scans, artifact hash fixtures, staging restart/retry acceptance, and explicit production approval.

Until all Definition-of-Done gates pass, report:

`SPECIALIST_CONTRACTS_PRESENT`

`PERSISTENT_MULTI_AGENT_RUNTIME_NOT_YET_CERTIFIED`
