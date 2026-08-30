# WOW governed probability backend — Render + Custom GPT Action release runbook

This runbook describes the current governed WOW production topology. The backend is analytical only: it never places, routes, modifies, or cancels wagers, and `can_execute=false` is invariant.

## 1. Authoritative Render service

Repository: `gregoryharper84-ship-it/WOW-Dashboard`

Service definition: repository-root `render.yaml`.

Production web service contract:

- service: `wow-governed-probability-engine`
- branch: `main`
- runtime: Python 3.11
- root directory: `artifacts/wow-engine`
- build: `pip install -r requirements.txt`
- start: `uvicorn api_ncaaf_acceptance:app --host 0.0.0.0 --port $PORT`
- health path: `/health`
- automatic deploys: off
- `WOW_CAN_EXECUTE=false`
- `WOW_DRY_RUN_ONLY=true`

Required secrets remain backend-only except for the Action credential:

- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `WOW_ACTION_API_KEY`

Never place secret values in Git, issue comments, chat messages, screenshots, logs, or command-line arguments. The Supabase service-role key and `WOW_ACTION_API_KEY` must be different credentials. The Custom GPT receives only the Action bearer credential, never the Supabase service-role key.

Because automatic deploys are off, every release follows:

1. reviewed PR,
2. required GitHub CI,
3. merge to `main`,
4. manual Render deploy,
5. exact deployed-SHA verification,
6. startup and `/health` verification.

Do not treat a green GitHub commit as production until Render reports that exact commit live.

## 2. Canonical Pick Request boundary

The governed source-agnostic prop ingress is:

`POST /score-pick-request`

Operation ID:

`scoreWowPickRequest`

The endpoint is authenticated with `Authorization: Bearer <WOW_ACTION_API_KEY>` and is designed for normalized candidates originating from screenshots, PDFs, pasted boards, or autonomous discovery.

Caller-owned fields are candidate identity and optional auditable raw pregame evidence. Probability, fitted-model identity, calibration output, lower bounds, edge, money qualification, terminal approval labels, and execution are backend-owned.

The current P0 automatic-evidence route is intentionally narrow:

- sport: MLB
- stat: `PITCHER_STRIKEOUTS`
- provider: official MLB StatsAPI evidence acquisition
- required evidence: exact player identity, target event/probable-pitcher confirmation, and ten prior regular-season starts
- output evidence contract: `PROP_EVIDENCE_V1`

Automatic hydration does not calculate a probability. It validates and freezes governed evidence, then delegates to the same certified `/score-prop` fitted-model path.

Unsupported exact routes remain `MODEL_UNAVAILABLE`. Missing evidence never authorizes a generic model, qualitative fallback, raw L5/L10 hit-rate probability, or market-implied probability to be relabeled as governed model output.

Rows terminate independently and reconcile exactly:

`rows_in = rows_completed + rows_held + rows_rejected`

Run controller status is `COMPLETE`, `DEGRADED`, or `BLOCKED`. One broken row may not erase valid siblings.

## 3. Direct single-row prop boundary

`POST /score-prop` remains available for callers that already have an immutable governed evidence snapshot ID.

Its required ordering is:

1. controlling specialist,
2. aggregate prop capability,
3. exact certified fitted-model artifact,
4. governed evidence hydration/repair,
5. fitted model,
6. calibration/bounds,
7. prediction persistence.

Unsupported model routes must terminate before evidence acquisition. Evidence repair may use only exact-identity governed PASS snapshots captured strictly before event start and must revalidate them through the existing evidence RPC.

## 4. Custom GPT Action schemas

Two reviewed schemas are retained in the backend directory:

### Core governed probability Action

`openapi.custom-gpt.template.yaml`

Includes health/governance, single-row prop scoring, event scoring, and settlement operations.

### Canonical Pick Request Action

`openapi.pick-request-action.yaml`

Includes only `/score-pick-request` and is the preferred prop-ingress contract for screenshot/PDF/self-discovery workflows because evidence may be backend-hydrated on certified routes.

For each schema installed in the GPT editor:

1. replace only `https://REPLACE_WITH_RENDER_SERVICE_HOST` with the exact HTTPS Render service origin,
2. configure authentication as API key → Bearer,
3. enter the same `WOW_ACTION_API_KEY` stored in Render,
4. never enter the Supabase service-role key,
5. preserve all returned fail-closed statuses and blockers.

Installing or editing a Custom GPT Action is a ChatGPT product configuration step; backend deployment does not silently modify the GPT editor configuration.

## 5. Live acceptance boundary

Minimum release evidence:

- Render build checks out the expected merged SHA.
- Render reports that deploy `live`.
- Uvicorn reports `Application startup complete`.
- repeated `GET /health` requests return 200.
- unauthenticated `POST /score-pick-request` returns 401, proving the route is mounted and the bearer gate is active without exposing the Action secret.
- that unauthenticated probe creates zero evidence rows and zero prediction rows.
- Supabase `PROP_PROBABILITY` capability and all `can_execute=false` invariants remain unchanged unless a separately governed promotion explicitly changes them.

An authenticated scoring call must never be manufactured by rotating or exposing the production Action secret merely for testing. Use the configured GPT Action or another already-authorized client when authenticated live-path evidence is required.

## 6. Model-coverage boundary

Infrastructure readiness is not model coverage.

At the time this runbook was updated, the certified prop artifact registry contains the MLB pitcher-strikeout fitted model. Other sports/stat families, including WNBA props, must remain `MODEL_UNAVAILABLE` until their own fitted artifacts, calibration path, and required governance gates are actually certified.

`can_execute=false` remains invariant regardless of model coverage.
