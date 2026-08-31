# WOW v17 Phase A — Custom Engine Alignment Contract

Status: `PHASE_A_BINDING_CANDIDATE`

This contract does **not** activate WOW v17, change production routing, deploy code, or authorize execution. It defines the required alignment between the two Custom GPT hosts before any v17 cutover.

## 1. Non-negotiable governance

```text
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
missing_required_evidence=FAIL_CLOSED
upstream_blockers=MONOTONIC
final_terminal_ceiling=STRICTEST_APPLICABLE_UPSTREAM_CEILING
backend_global_ceiling_enforcement_status=PARTIAL_OR_PENDING until proven otherwise
```

No Custom GPT, backend, connector, worker, model, or support skill may place, route, modify, cancel, or imply live wagering execution.

## 2. Main-brain topology

```text
WOW v17 MAIN BRAIN
|
+-- WOW_BETTING_ENGINE           # Custom GPT host: player/prop/scalar intelligence
|   +-- player props
|   +-- pitcher/batter workload and scalar markets
|   +-- sport-specific prop specialists
|   +-- bidirectional MORE/LESS analysis
|
+-- LLP_TEAM_BETTING_ENGINE      # Custom GPT host: team/game/event intelligence
|   +-- full-game/event winner probability
|   +-- favorite classification
|   +-- underdog/upset analysis
|   +-- event mutual exclusion
|   +-- sport-specific team/event specialists
|
+-- SHARED_GOVERNED_CORE
    +-- governance and capability routing
    +-- canonical slate/event/candidate identity
    +-- immutable evidence/provenance/freshness
    +-- probability component ledger and shrinkage
    +-- dynamic calibration and uncertainty bounds
    +-- failure-path framework
    +-- probability validity/normalization
    +-- market identity/settlement/no-vig/fees
    +-- probability/edge/money objective separation
    +-- dependency/correlation/exposure/portfolio controls
    +-- final refresh
    +-- immutable pregame write
    +-- reconciliation
    +-- single global terminal-ceiling reducer
```

The architecture is **not** `WOW -> external LLP selector`. Both are sibling intelligence hosts inside one governed main brain.

## 3. Lane ownership

### WOW Betting Engine

Controlling host for:

```text
PLAYER_PROP
PLAYER_SCALAR
PITCHER_PROP
BATTER_PROP
WORKLOAD_PROP
WEATHER_SCALAR where a dedicated WOW/Kalshi specialist is controlling
```

For any prop row, the WOW host must route to exactly one sport/stat controlling specialist. Generic research, market data, L5/L10, or another Custom GPT cannot substitute for a missing fitted specialist.

### LLP Team Betting Engine

Controlling host for:

```text
TEAM_EVENT
OUTRIGHT_WINNER
MONEYLINE
FAVORITE
UNDERDOG
UPSET
MATCH_WINNER
FIGHT_WINNER
```

LLP must model the full mutually exclusive event outcome space. For two-outcome markets, both sides must reconcile. For three-way markets, material draw states must be preserved and normalized.

LLP owns the **team/event probability question**. It does not own global market qualification, portfolio approval, or the final native WOW terminal label.

## 4. Shared gate contract

Both hosts inherit the same mandatory Full Model control sequence where applicable:

```text
0   governance/host/safety
0.5 calibration-health precheck
1   discovery
2   slate/event identity
3   exact candidate/market/settlement identity
4   provenance/freshness
4.5 typed hydration/objective readiness where available
5   role/starter/lineup/status
6   role-valid history + discernment/ESS where applicable
7   probability component ledger/shrinkage
8   controlling specialist
9   matchup model
10  bidirectional/opposing-outcome audit
11  failure paths + unconditional probability
12  dynamic calibration + bounds
13  probability validity/normalization
14  cross-market drift/cause where governed
15  exact line/payout/push/settlement/no-vig/fees
16  probability/edge/settlement/money/portfolio separation
17  dependency/correlation/structure
18  session/directional/duplicate-thesis exposure
19  weakest-leg elimination/shrink where applicable
20  final refresh
21  reconciliation
22  immutable pregame write where required
23  strict global terminal-ceiling reduction
24  native-label output
```

A lane may mark a stage `NOT_APPLICABLE` only when it is genuinely inapplicable.

## 5. Specialist and decision authority

Exactly one controlling specialist is permitted per row/event outcome model.

Specialists and host-specific governors may return:

```text
model_status
raw_probability
probability_components
failure_paths
unconditional_probability
calibration_inputs
calibrated_probability
lower_bound
upper_bound
confidence
contradictions
host_decision_recommendation
host_blockers
host_ceiling
```

They may **not** independently override the global terminal reducer.

The LLP Probability Claim Auditor and LLP Event Decision Governor remain first-class team/event controls, but their outputs are inputs to the global reducer. The Event Decision Governor may enforce `one side or NO_PICK`; it may not erase a stricter shared-core blocker or independently create a global `FINAL_APPROVED` state.

## 6. Terminal label authority

```text
single_global_terminal_authority = V17_TERMINAL_REDUCER
host_terminal_authority = false
```

Legacy host-local labels may be retained only as audit/status fields during migration. They may not be treated as the final v17 terminal decision.

No downstream pass may erase an upstream blocker. The final native label is the strictest applicable upstream ceiling after final refresh, reconciliation, and immutable write requirements.

## 7. Probability and calibration alignment

Both engines must obey:

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

For multi-outcome events, probabilities must normalize and material draw/push/void states must be preserved where applicable.

A calibration/publication blocker does not automatically prove the specialist is unavailable. Capability failures are scoped to the lane/gate they actually affect.

## 8. Market and portfolio alignment

Neither Custom GPT may independently convert a probability result into a money/portfolio approval.

The shared core owns:

```text
exact current market identity
settlement/push/void rules
payout
no-vig
fees/friction
market drift/cause
edge math
probability-vs-edge separation
dependency/correlation
session/directional exposure
duplicate thesis control
portfolio governance
weakest-leg cycle
```

## 9. Host and backend alignment

Canonical host identities:

```text
WOW_BETTING_ENGINE
LLP_TEAM_BETTING_ENGINE
PROJECT_CHAT
```

`nested_custom_gpt_required=false`.

A Custom GPT is a model/orchestration host. Render/Supabase/other services are capability backends. The absence of a backend application named after either Custom GPT is not evidence that the model host is unavailable.

Both Custom GPTs must use the same canonical governed backend contract for shared scoring/publication/ledger functions. Direct vendor Actions may provide evidence only if their provenance and freshness are captured; they may not bypass the governed core or provide terminal authority.

Legacy Replit contracts must not be the primary v17 scoring/governance path.

## 10. Required Action responsibilities

The exact OpenAPI grouping may evolve, but v17 must preserve these responsibilities behind versioned contracts:

### Shared

```text
health
governance
immutable recommendation/prediction write
settlement/outcome write
final decision traceability
```

### WOW-specific ingress/scoring

```text
player/prop batch ingress
prop evidence hydration
prop fitted-model scoring
bidirectional prop scoring/settlement
```

### LLP-specific ingress/scoring

```text
team/event ingress
sport-specific event fitted-model scoring
favorite/underdog classification
both-sides or full-outcome-space reconciliation
team failure/upset paths
LLP probability-claim audit
LLP event decision governor
```

A sport without a certified team/event fitted model must fail closed as `MODEL_UNAVAILABLE` for that lane; market-implied probability or generic reasoning cannot replace it.

## 11. Custom-GPT editor parity gate

Before v17 cutover, **both actual Custom GPT editor configurations** must be inspected and attested against this contract.

Required attestation fields:

```text
custom_gpt_name
custom_gpt_identity
instructions_version
knowledge_manifest_version
shared_core_version
action_schema_versions
action_server_origins
auth_contracts
governance_trigger_contract
full_model_trigger_contract
wow_daily_contract_if_applicable
lane_ownership_contract
terminal_authority_contract
can_execute
legacy_primary_backend_routes_present
editor_inspected_at
editor_attestation_status
```

Allowed status:

```text
PASS
PARTIAL
BLOCKED_WITH_EXACT_EXTERNAL_REQUIREMENT
```

No v17 cutover may occur unless both hosts are `PASS`.

## 12. Phase-A evidence snapshot — 2026-08-31

This section records current audit evidence and is **not** a claim that live editor parity has passed.

```text
repository = gregoryharper84-ship-it/WOW-Dashboard
repository_main = 4ef39405702ce38682b28733f767206ebf28a2d5
render_service = wow-governed-probability-engine
render_service_id = srv-da7sa9gu01pc73brt80g
render_autodeploy = false
render_live_commit = 7462f595ebd9db16fbaa54b296c99ce9f58afe58
render_entrypoint = api_ncaaf_acceptance:app
```

The production entrypoint imports the shared governed production API and installs pick-request and live-probability routes, so the NCAAF-named entrypoint is a wrapper rather than proof of an NCAAF-only service. However, the live deployment is behind current `main`, so production/repository parity is not yet proven.

Current repository OpenAPI contracts include:

```text
openapi.custom-gpt.template.yaml
  getWowProbabilityHealth
  getWowProbabilityGovernance
  scoreWowProp
  scoreWowEvent        # current request schema is MLB-only
  settleWowProp

openapi.pick-request-action.yaml
  scoreWowPickRequest  # current ingress is prop-specific
  recordWowRecommendations
  settleWowRecommendations
```

The current Render contract therefore does not yet provide a universal cross-sport LLP team/event scoring endpoint.

Latest available LLP editor-level audit evidence is newer than the latest available WOW editor-level audit evidence. LLP also had direct vendor Action authentication gaps in that audit. These are v17 closeout items, not permission to assume parity.

## 13. Phase-A cutover blockers

```text
B01_WOW_LIVE_EDITOR_PARITY_NOT_CURRENTLY_PROVEN
B02_LLP_LIVE_EDITOR_PARITY_REQUIRES_REATTESTATION_AFTER_V17_CONTRACT
B03_RENDER_DEPLOY_SHA_BEHIND_REPOSITORY_MAIN
B04_TEAM_EVENT_RENDER_CONTRACT_NOT_YET_CROSS_SPORT
B05_LLP_DIRECT_VENDOR_ACTION_AUTH_GAPS_REMAIN_FROM_LATEST_EDITOR_AUDIT_UNTIL_REPROVEN
B06_LEGACY_LLP_HOST_LABELS_MUST_BECOME_AUDIT_FIELDS_UNDER_GLOBAL_TERMINAL_REDUCER
B07_CANONICAL_LLP_CUSTOM_GPT_HOST_IDENTITY_NOT_YET_MACHINE_ENFORCED_IN_SHARED_HOST_CONTRACT
```

These blockers do not mean the existing v16 engines are unusable. They mean v17 host parity is not yet certified.

## 14. Phase-A exit criteria

```text
WOW_CUSTOM_GPT_EDITOR_ATTESTATION=PASS
LLP_CUSTOM_GPT_EDITOR_ATTESTATION=PASS
BOTH_HOSTS_SHARE_CANONICAL_GOVERNANCE_VERSION=PASS
BOTH_HOSTS_SHARE_CANONICAL_BACKEND_ORIGIN=PASS
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
V17_CUTOVER_ALLOWED=true
```

Until all exit criteria pass:

```text
V17_CUTOVER_ALLOWED=false
can_execute=false
```
