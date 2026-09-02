# WOW governed probability backend — Render + Custom GPT Action release runbook

This runbook describes the current governed WOW **V17-active** production topology. The backend is analytical only: it never places, routes, modifies, or cancels wagers, and `can_execute=false` is invariant.

Canonical current-state pointer: `V17_PRODUCTION_STATUS.md`.

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
- `WOW_V17_ACTIVE=1`
- `WOW_CAN_EXECUTE=false`
- `WOW_DRY_RUN_ONLY=true`
- global terminal authority: `V17_TERMINAL_REDUCER`

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
6. startup/runtime-status and `/health` verification.

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
- required core evidence: exact player identity, target event/probable-pitcher confirmation, and ten prior regular-season starts
- optional live opponent evidence: confirmed batting order plus sufficiently supported hitter strikeout-rate splits against the starter throwing hand; when available this is persisted as `opponent_context`
- output evidence contract: `PROP_EVIDENCE_V1`

Automatic hydration does not calculate a probability. It validates and freezes governed evidence, then delegates to the same certified `/score-prop` fitted-model path.

For MLB pitcher strikeouts, optional opponent-context acquisition is **neutral-on-missing**: if official lineup/split evidence is unavailable or insufficient, the hydrator omits `opponent_context` rather than inventing a rate or converting an otherwise valid route into a false model-capability failure. When supported opponent K evidence is present, the certified adapter may move the fitted distribution through its reviewed opponent factor; the calibration bootstrap uses the same single-source `opponent_k_factor()` so bounds remain centered on the same adjusted distribution.

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

Includes `/score-pick-request` and is the preferred prop-ingress contract for screenshot/PDF/self-discovery workflows because evidence may be backend-hydrated on certified routes.

For each schema installed in the GPT editor:

1. replace only `https://REPLACE_WITH_RENDER_SERVICE_HOST` with `https://wow-governed-probability-engine.onrender.com`,
2. configure authentication as API key → Bearer,
3. enter the same `WOW_ACTION_API_KEY` stored in Render,
4. never enter the Supabase service-role key,
5. preserve all returned fail-closed statuses and blockers.

Installing or editing a Custom GPT Action is a ChatGPT product configuration step; backend deployment does not silently modify the GPT editor configuration. Use `V17_CUSTOM_GPT_EDITOR_SYNC.md` as the canonical live-editor handoff. Until completed externally, report `LIVE_EDITOR_SYNC_EXTERNAL` rather than misclassifying editor drift as a backend/model failure.

## 5. Live acceptance boundary

Minimum release evidence:

- final PR head passes `wow-engine-verify`.
- final PR head passes `wow-verify`.
- Render build checks out the expected merged SHA.
- Render reports that deploy `live`.
- Uvicorn reports `Application startup complete`.
- runtime reports `WOW_V17_RUNTIME status=ACTIVE`, `global_terminal_authority=V17_TERMINAL_REDUCER`, `can_execute=false`.
- repeated `GET /health` requests return 200.
- unauthenticated `POST /score-pick-request` returns 401, proving the route is mounted and the bearer gate is active without exposing the Action secret.
- that unauthenticated probe creates zero evidence rows and zero prediction rows.
- required Supabase migrations for the release are applied and verified before new schema fields are depended upon.
- all `can_execute=false` invariants remain unchanged unless a separately governed architecture explicitly changes them.

An authenticated scoring call must never be manufactured by rotating or exposing the production Action secret merely for testing. Use the configured GPT Action or another already-authorized client when authenticated live-path evidence is required.

## 6. Model-coverage boundary

Infrastructure/V17 runtime readiness is not universal model coverage.

At the time this runbook was updated, certified governed prop capability includes the MLB pitcher-strikeout fitted model and the separately governed certified MLB 1IP empirical specialist. Other sports/stat families must remain fail-closed until their own fitted artifacts, calibration path, evidence contract, and required governance gates are actually certified.

Adjacent sportsbook lines may inform context but may not satisfy exact-line no-vig authority for a different board threshold. Duplicate-thesis exposure is a portfolio/slip-construction rule and must not rewrite the fitted sporting probability. Material matchup contradiction that is supported by a certified model input must reach the numeric probability/bounds rather than remain prose-only, but the same underlying risk must not be applied twice across model and portfolio layers.

`can_execute=false` remains invariant regardless of model coverage.

## 7. Repository governance boundary

GitHub issue #88 tracks machine-level `main` protection required for final repository certification: PR-before-merge, required WOW CI, blocked force pushes, blocked deletion, and no ordinary bypass that defeats protection. This repository-admin requirement is independent of model/runtime health and must not be emitted as `MODEL_UNAVAILABLE`.
