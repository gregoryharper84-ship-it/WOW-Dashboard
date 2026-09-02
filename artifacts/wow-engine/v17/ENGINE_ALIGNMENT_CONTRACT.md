# WOW V17 Phase A — Historical Custom Engine Alignment Contract

> **SUPERSEDED / HISTORICAL:** This Phase-A candidate record is preserved for
> audit history. V17 is now the only active generation. Current authority is
> `../V17_PRODUCTION_STATUS.md`, `PHASE_A_ALIGNMENT_STATUS.md`, and
> `../../../docs/wow/GENERATION_STATUS.md`. Statements below that production
> remains V16 describe the pre-cutover state and are no longer current.

Status: `PHASE_A_BINDING_CANDIDATE`

This contract does **not** activate WOW v17 or authorize execution. Production remains WOW v16 Clean Core until every cutover gate passes.

## Non-negotiable governance

```text
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
missing_required_evidence=FAIL_CLOSED
upstream_blockers=MONOTONIC
final_terminal_ceiling=STRICTEST_APPLICABLE_UPSTREAM_CEILING
```

## Main-brain topology

```text
WOW v17 MAIN BRAIN
|
+-- WOW_BETTING_ENGINE
|   +-- player / prop / scalar intelligence
|
+-- LLP_TEAM_BETTING_ENGINE
|   +-- team / game / event / favorite / underdog / upset intelligence
|
+-- SHARED_GOVERNED_CORE
    +-- evidence / provenance / freshness
    +-- failure paths
    +-- calibration / bounds
    +-- market / settlement / no-vig / fees
    +-- dependency / exposure / portfolio
    +-- final refresh / immutable write / reconciliation
    +-- V17_TERMINAL_REDUCER
```

Both Custom GPTs are sibling intelligence hosts. Neither is the global terminal authority.

## Requester host vs controlling engine

These are separate concepts:

```text
requester_host_identity = WOW_BETTING_ENGINE | LLP_TEAM_BETTING_ENGINE | PROJECT_CHAT

PLAYER_PROP family
  controlling_engine_identity = WOW_BETTING_ENGINE

TEAM_EVENT / OUTRIGHT_WINNER / MONEYLINE / FAVORITE / UNDERDOG / UPSET
  controlling_engine_identity = LLP_TEAM_BETTING_ENGINE
```

A request may originate from WOW or Project Chat and still be controlled by LLP for team/event probability. `nested_custom_gpt_required=false` remains valid.

## Lane invariants

WOW prop lanes require exactly one sport/stat controlling specialist, bidirectional MORE/LESS analysis, role-valid evidence, failure paths, calibration and bounds. Missing controlling specialist means `MODEL_UNAVAILABLE`; generic reasoning, L5/L10 or market probability cannot substitute.

LLP team/event lanes require a sport-specific controlling model, mutually exclusive outcome-space reconciliation, favorite failure path, underdog/upset path, Probability Claim Auditor, and Event Decision Governor. The Event Decision Governor may choose one side or `NO_PICK`; it cannot erase a stricter shared-core blocker.

## Global terminal authority

```text
single_global_terminal_authority = V17_TERMINAL_REDUCER
WOW_BETTING_ENGINE.global_terminal_authority = false
LLP_TEAM_BETTING_ENGINE.global_terminal_authority = false
PROJECT_CHAT.global_terminal_authority = false
```

Host-local labels are audit/model-decision fields only.

## Probability/calibration alignment

```text
0 < published_probability < 1
point_estimate != lower_bound
published_probability requires controlling_model_support
published_probability requires calibrated_lower_bound
market_probability != model_probability
L5_or_L10_hit_rate != model_probability
failure_paths_must_change_unconditional_probability
Calibration_Health != Dynamic_Calibration
```

## Shared market/portfolio ownership

Neither Custom GPT independently turns a probability into global money/portfolio approval. The shared core owns exact market identity, settlement, payout, no-vig, fees/friction, drift/cause, objective separation, dependency/correlation, exposure, duplicate-thesis control, portfolio governance, final refresh and final reduction.

## Backend alignment

```text
canonical_runtime = RENDER_SUPABASE_GOVERNED_CORE
canonical_render_service = wow-governed-probability-engine
legacy_replit_primary_routing_allowed = false
direct_vendor_actions_terminal_authority = false
```

Direct vendor Actions may be evidence-only only when auth, provenance and freshness are proven.

## Candidate implementation now present

On branch `chatgpt/v17-phase-a-custom-engine-alignment-20260831`:

```text
v17/host_routing.py
  machine-enforces canonical requester identities and lane controller mapping

v17/team_event_request_runtime.py
  generic authenticated POST /score-team-event
  MLB -> existing governed MLB event adapter
  unsupported sports -> MODEL_UNAVAILABLE

api_v17_candidate.py
  distinct app; does not mutate accepted v16 app
  preserves v16 routes/lifecycle hooks
  mounts /score-team-event
  mounts /record-recommendations and /settle-recommendations
  mounts /v17/host-contract

v17/openapi.wow-betting-engine.v17.yaml
  WOW prop operations + team/event delegation

v17/openapi.llp-team-engine.v17.yaml
  team/event operations only; no prop scoring
```

## Render parity

The prior production deploy lag is resolved:

```text
repository_main_sha = 4ef39405702ce38682b28733f767206ebf28a2d5
render_live_sha     = 4ef39405702ce38682b28733f767206ebf28a2d5
REPOSITORY_DEPLOY_PARITY = PASS
```

This repaired v16 production parity; it did not activate v17.

## Custom GPT editor parity

Direct live-editor evidence is still required. Use:

```text
WOW_BETTING_ENGINE_EDITOR_ATTESTATION.md
LLP_TEAM_BETTING_ENGINE_EDITOR_ATTESTATION.md
```

The current tool surface cannot truthfully mark either live editor PASS without direct editor evidence.

## Candidate Action policy

The prepared schemas use the eventual shared governed origin:

```text
https://wow-governed-probability-engine.onrender.com
```

They must not be installed into live Custom GPTs while production still runs the v16 entrypoint and therefore lacks v17 candidate routes. Candidate CI and shadow acceptance must precede any approved entrypoint migration.

## Remaining blockers

```text
B01_WOW_LIVE_EDITOR_PARITY_NOT_CURRENTLY_PROVEN
B02_LLP_LIVE_EDITOR_PARITY_REQUIRES_REATTESTATION_AFTER_V17_CONTRACT
B03_V17_CANDIDATE_ENTRYPOINT_NOT_YET_PRODUCTION_OR_SHADOW_ACCEPTANCE_CERTIFIED
B04_LLP_DIRECT_VENDOR_ACTION_AUTH_AND_EVIDENCE_ONLY_STATUS_REQUIRES_EDITOR_REPROOF
B05_V17_CANDIDATE_CI_AND_INDEPENDENT_REVIEW_PENDING
```

## Phase-A exit

Required before `V17_CUTOVER_ALLOWED=true`:

```text
WOW_CUSTOM_GPT_EDITOR_ATTESTATION=PASS
LLP_CUSTOM_GPT_EDITOR_ATTESTATION=PASS
PROP_LANE_OWNERSHIP=WOW_BETTING_ENGINE
TEAM_EVENT_LANE_OWNERSHIP=LLP_TEAM_BETTING_ENGINE
ONE_CONTROLLING_SPECIALIST_PER_ROW=PASS
LLP_EVENT_MUTEX=PASS
SHARED_CALIBRATION_CONTRACT=PASS
SHARED_MARKET_PORTFOLIO_CONTRACT=PASS
GLOBAL_TERMINAL_REDUCER_ONLY=PASS
CAN_EXECUTE_FALSE=PASS
REPOSITORY_DEPLOY_PARITY=PASS
LEGACY_PRIMARY_REPLIT_ROUTING=ABSENT
CANDIDATE_CI=PASS
CANDIDATE_SHADOW_ACCEPTANCE=PASS
INDEPENDENT_REVIEW=PASS
```

Until all applicable criteria pass:

```text
V17_CUTOVER_ALLOWED=false
can_execute=false
```
