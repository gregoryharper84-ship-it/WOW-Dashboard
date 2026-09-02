# WOW v17 — Production Backend Cutover Status

As of: 2026-09-01

Status: `OWNER_AUTHORIZED_BACKEND_CUTOVER`

This file supersedes the original Phase-A candidate status. The accepted V17 architecture has been converged into the current governed backend and is being activated additively on the existing production entrypoint. Existing V16-compatible routes remain available during the cutover. Wager execution remains permanently disabled.

## Invariants

```text
V17_CUTOVER_ALLOWED = true
V17_BACKEND_ACTIVE = true when WOW_V17_ACTIVE=1
GLOBAL_TERMINAL_AUTHORITY = V17_TERMINAL_REDUCER
can_execute = false
WOW_CAN_EXECUTE = false
WOW_DRY_RUN_ONLY = true
LEGACY_REPLIT_PRIMARY_ROUTING_ALLOWED = false
```

The cutover authorization applies to architecture/backend routing only. It does not authorize betting, market orders, or bypass of any controlling-model, calibration, evidence, final-refresh, or terminal-ceiling gate.

## Production topology

```text
production_service = wow-governed-probability-engine
production_entrypoint = api_ncaaf_acceptance:app
activation_flag = WOW_V17_ACTIVE=1
canonical_runtime = RENDER_SUPABASE_GOVERNED_CORE
runtime_supabase_project = iczfhsmjrrafhvcpmqhr
```

Activation is additive rather than a parallel second brain. When the flag is enabled, the accepted production app mounts:

```text
/score-team-event
/v17/host-contract
/record-recommendations
/settle-recommendations
```

Existing governed prop, pick-request, event, health, governance, NCAAF maintenance, live-probability, and MLB 1IP paths remain available. Setting `WOW_V17_ACTIVE=0` atomically removes the new V17 host route surface without changing lower-level scoring implementations.

## Host and lane authority

```text
PLAYER_PROP / PLAYER_SCALAR / PITCHER_PROP / BATTER_PROP / WORKLOAD_PROP
  controlling host = WOW_BETTING_ENGINE

TEAM_EVENT / OUTRIGHT_WINNER / MONEYLINE / FAVORITE / UNDERDOG / UPSET /
MATCH_WINNER / FIGHT_WINNER
  controlling host = LLP_TEAM_BETTING_ENGINE

PROJECT_CHAT
  authorized requester host only; never global terminal authority
```

A team/event request may originate from WOW or Project Chat. The backend still resolves LLP as controlling engine. Neither Custom GPT owns the global final terminal decision.

## Team/event governed flow

The V17 team-event ingress enforces:

```text
request
-> host/lane resolution
-> contract + event identity validation
-> mandatory focused Scout
-> mandatory Research team + reconciler
-> sport-specific controlling specialist
-> probability claim/event governance bridge
-> post-model gates
-> final gates
-> V17_TERMINAL_REDUCER
```

Scout and Research remain evidence-only and may not emit a governed probability or terminal override. Unsupported sports continue to fail closed as `MODEL_UNAVAILABLE`; market-implied probability and generic reasoning are not substitutes.

For MLB, the accepted adapter reuses the governed fitted MLB event path and requires `wow_v17_mlb_team_event_governance_bridge` proof before numeric probability can leave the V17 team-event boundary.

## Database governance baseline

The live runtime database has the required V17/shared gate functions:

```text
wow_v17_team_failure_path_gate
wow_v17_mlb_team_event_governance_bridge
wow_run_event_postmodel_gates
wow_run_event_final_gates
```

The previously live-only event-ledger table baseline is now guarded by repository migration:

```text
migrations/20260901_v17_event_schema_baseline_guard.sql
```

The guard was applied successfully to the runtime Supabase project. It verifies the required legacy tables, critical governance columns, immutable scoring linkage, execute-false constraints, and bridge/shared final-gate functions. It intentionally fails closed rather than attempting to silently recreate an unknown or drifted production event ledger.

The underlying shared event gate function definitions remain captured in:

```text
migrations/20260901_event_terminal_gate_functions_capture.sql
```

## Action contracts

Canonical source schemas are now production V17 source contracts:

```text
v17/openapi.wow-betting-engine.v17.yaml
v17/openapi.llp-team-engine.v17.yaml
```

They share the governed Render origin and bearer-auth family. The LLP schema contains no player-prop scoring operation. The WOW schema delegates team/event scoring to the LLP-controlled backend route. Both explicitly preserve `can_execute=false`.

## MLB 1IP continuity

V17 activation must preserve the already accepted MLB 1IP production path:

```text
certified empirical artifact = READY
exact line support = 11.5, 13.5, 15.5, 17.5, 19.5, 21.5
authenticated live ingress = PASS
non-grid OOD rejection = PASS
final-refresh scheduler = LIVE
probability_publishable = false
can_execute = false
```

## Owner-authorized governance exception

The repository owner explicitly authorized proceeding through the cutover without stopping for the prior independent-review checkpoint. This does not waive machine verification, fail-closed runtime semantics, deployment-SHA parity, or execution prohibition.

## Remaining external editor sync

The backend and repository source contracts can be completed from the current tool surface. The live Custom GPT editors cannot be directly written from this environment, so they are not falsely attested as synchronized.

```text
WOW_CUSTOM_GPT_LIVE_EDITOR_INSTALL_V17_SCHEMA_AND_INSTRUCTIONS = EXTERNAL_SYNC_REQUIRED
LLP_CUSTOM_GPT_LIVE_EDITOR_INSTALL_V17_SCHEMA_AND_INSTRUCTIONS = EXTERNAL_SYNC_REQUIRED
```

That is an editor-distribution task, not a backend-model fallback. Until live editor sync is performed, the existing live GPT editor configuration may continue using older Action definitions even though the V17 backend routes are active.

## Cutover acceptance

Before the backend activation is considered operationally complete, final-head CI and production acceptance must show:

```text
WOW_ENGINE_VERIFY = PASS
WOW_VERIFY = PASS
RENDER_DEPLOY_SHA_PARITY = PASS
RENDER_HEALTH = PASS
WOW_V17_RUNTIME status=ACTIVE
/v17/host-contract authenticated acceptance = PASS
/score-team-event authenticated routing/fail-closed acceptance = PASS
MLB_1IP_SCHEDULER_CONTINUITY = PASS
can_execute=false = PASS
```

No missing fitted sport model is created by this cutover. Model availability remains sport- and market-specific.
