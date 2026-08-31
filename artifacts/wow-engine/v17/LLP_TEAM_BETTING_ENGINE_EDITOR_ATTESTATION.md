# LLP Team Betting Engine — v17 Custom GPT Editor Attestation

Status: `PARTIAL_REATTESTATION_REQUIRED`

This packet defines the exact live-editor state required before LLP Team Betting Engine may be certified for v17 cutover. It does not activate v17.

## Required identity

```text
custom_gpt_name = LLP Team Betting Engine
custom_gpt_identity = LLP_TEAM_BETTING_ENGINE
host_role = TEAM_GAME_EVENT_WINNER_FAVORITE_UNDERDOG_UPSET_INTELLIGENCE
shared_core = WOW_V17_GOVERNED_CORE
nested_custom_gpt_required = false
can_execute = false
global_terminal_authority = false
```

The LLP Probability Claim Auditor and Event Decision Governor remain controlling team/event audit components, but they do not override shared-core blockers or the global terminal reducer.

## Required Action configuration after candidate entrypoint certification

Canonical schema:

```text
artifacts/wow-engine/v17/openapi.llp-team-engine.v17.yaml
```

Canonical server origin:

```text
https://wow-governed-probability-engine.onrender.com
```

Authentication:

```text
Bearer/API key using WOW_ACTION_API_KEY
```

Required responsibilities:

```text
health/governance
team/event ingress
favorite/underdog/upset intent
sport-specific event fitted-model routing
full mutually exclusive outcome-space reconciliation
write-before-display recommendation recording
recommendation settlement
host-contract inspection
```

The canonical LLP v17 Action must contain **no player-prop scoring operation**.

## Legacy/direct-vendor cleanup

The previous editor audit identified direct-vendor authentication gaps and a legacy Replit scoring route. Before PASS, inspect every current Action and classify it:

```text
CANONICAL_GOVERNED_CORE
EVIDENCE_ONLY
REMOVE_OR_DISABLE
```

Rules:

- Any Replit scoring/governance Action must be removed or disabled as a primary v17 route.
- Direct vendor Actions may remain only as `EVIDENCE_ONLY` if authentication works and every payload used by the model is captured with provenance and freshness.
- Direct vendor Actions may not set probability authority, model status, terminal label, money approval, portfolio approval, or bypass the shared governed core.
- If vendor authentication cannot be proven, the Action is not required for the v17 core and should not block unrelated governed lanes; it must not be represented as available evidence.

## Instruction requirements

The live LLP instructions must preserve:

```text
TEAM_EVENT/OUTRIGHT_WINNER/MONEYLINE/FAVORITE/UNDERDOG/UPSET ownership.
PLAYER_PROP ownership remains with WOW_BETTING_ENGINE.
Full mutually exclusive outcome space is modeled.
Two-outcome markets reconcile both sides; three-way markets preserve draw.
Favorite and underdog/upset lanes receive equal governed research effort.
No forced upset.
Favorite failure paths and underdog upset paths change unconditional probability.
Probability Claim Auditor validates traceability/calibration/lower bounds.
Event Decision Governor emits one side or NO_PICK within the event lane.
Event Decision Governor cannot erase shared-core blockers.
Market probability is context/prior only and cannot replace a missing fitted model.
Global market economics, portfolio governance and final terminal authority remain shared-core responsibilities.
Global terminal authority belongs only to V17_TERMINAL_REDUCER.
can_execute=false.
```

## Required live-editor evidence

Record, without exposing secrets:

```text
editor_inspected_at:
custom_gpt_name:
custom_gpt_identity:
instructions_version_or_hash:
knowledge_manifest_version_or_hash:
action_schema_title:
action_schema_version:
action_server_origin:
auth_type:
lane_ownership_contract:
probability_claim_auditor_contract:
event_decision_governor_contract:
terminal_authority_contract:
legacy_primary_replit_route_present:
direct_vendor_actions:
  - name:
    classification:
    auth_status:
    provenance_status:
    freshness_status:
can_execute:
```

## PASS criteria

```text
custom_gpt_identity = LLP_TEAM_BETTING_ENGINE
action_server_origin = https://wow-governed-probability-engine.onrender.com
canonical v17 LLP Action schema installed after backend candidate certification
auth configured without exposing credential
legacy_primary_replit_route_present = false
no player-prop scoring operation in LLP canonical Action
team/event ownership = LLP_TEAM_BETTING_ENGINE
prop ownership = WOW_BETTING_ENGINE
direct vendor Actions are evidence-only or removed
global_terminal_authority = false
can_execute = false
all probability/event-governor requirements aligned
```

Until every item is evidenced:

```text
LLP_CUSTOM_GPT_EDITOR_ATTESTATION = PARTIAL
V17_CUTOVER_ALLOWED = false
```
