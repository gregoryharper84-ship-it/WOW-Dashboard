# LLP Team Betting Engine — v17 Custom GPT Editor Sync Packet

Status: `SOURCE_CONTRACT_READY_LIVE_EDITOR_SYNC_EXTERNAL`

The V17 governed backend may be active independently of live Custom GPT editor distribution. This packet defines the exact live-editor state required to synchronize LLP Team Betting Engine with the production V17 source contract. It does not grant execution authority and must not be treated as proof that the live editor was changed unless the editor itself was inspected and saved.

## Required identity

```text
custom_gpt_name = LLP Team Betting Engine
custom_gpt_identity = LLP_TEAM_BETTING_ENGINE
host_role = TEAM_GAME_EVENT_WINNER_FAVORITE_UNDERDOG_UPSET_POINT_SPREAD_INTELLIGENCE
shared_core = WOW_V17_GOVERNED_CORE
nested_custom_gpt_required = false
can_execute = false
global_terminal_authority = false
```

The LLP Probability Claim Auditor and Event Decision Governor remain controlling team/event audit components, but they do not override shared-core blockers or the global terminal reducer.

## Production Action source contract

```text
schema = artifacts/wow-engine/v17/openapi.llp-team-engine.v17.yaml
server = https://wow-governed-probability-engine.onrender.com
auth = Bearer/API key using WOW_ACTION_API_KEY
schema_status = PRODUCTION_SOURCE_CONTRACT
spread_shadow_operation = scoreLlpV17SpreadForwardShadow
spread_shadow_route = /internal/v17/spread-forward-shadow
```

Required responsibilities:

```text
health/governance
team/event outright-winner ingress
favorite/underdog/upset intent
sport-specific fitted-model routing
NCAAF exact-line point-spread forward shadow
full mutually exclusive outcome-space reconciliation
write-before-display recommendation recording
recommendation settlement
host-contract inspection
```

The canonical LLP V17 Action contains **no player-prop scoring operation**. The existing `/score-team-event` request remains `OUTRIGHT_WINNER`-only. Point spread uses the distinct shadow operation until governed certification/promotion is separately earned.

## Point-spread contract

```text
lane_owner = LLP_TEAM_BETTING_ENGINE
market_family = POINT_SPREAD
current_forward_shadow_sport = NCAAF
exact_line_is_post_fit_threshold = true
moneyline_to_spread_conversion = forbidden
market_probability_substitution = forbidden
probability_publishable = false
automatic_certification = false
automatic_promotion = false
can_execute = false
```

The closed spread request requires `sport`, `event_id`, `event_start_time`, `home_team`, `away_team`, `home_spread`, and `season`. Spread-specific typed blockers must remain distinct and may not be collapsed into `MODEL_UNAVAILABLE` unless the controlling fitted spread capability itself is absent/unregistered.

## Legacy/direct-vendor cleanup

Every live Action should be classified as:

```text
CANONICAL_GOVERNED_CORE
EVIDENCE_ONLY
REMOVE_OR_DISABLE
```

Rules:

- Any Replit scoring/governance Action must be removed or disabled as a primary V17 route.
- Direct vendor Actions may remain only as `EVIDENCE_ONLY` if authentication works and every payload used by the model is captured with provenance and freshness.
- Direct vendor Actions may not set probability authority, model status, terminal label, money approval, portfolio approval, or bypass the shared governed core.
- If vendor authentication cannot be proven, it must not be represented as available evidence.

## Instruction requirements

The live LLP instructions must preserve:

```text
TEAM_EVENT/OUTRIGHT_WINNER/MONEYLINE/FAVORITE/UNDERDOG/UPSET/POINT_SPREAD ownership.
PLAYER_PROP ownership remains with WOW_BETTING_ENGINE.
Point spread uses only the fitted scoring-margin specialist for the exact sport.
The spread line is a post-fit threshold, never a training feature/probability source.
No ML->spread conversion or sportsbook-implied probability substitution.
NCAAF spread forward scoring remains research/shadow until registry certification.
Full mutually exclusive outcome space is modeled.
Favorite and underdog/upset lanes receive equal governed research effort.
Probability Claim Auditor validates traceability/calibration/lower bounds.
Event Decision Governor emits one side or NO_PICK within the event lane.
Event Decision Governor cannot erase shared-core blockers.
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
spread_shadow_operation_visible:
spread_shadow_canary_status:
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

## Editor-sync PASS criteria

```text
custom_gpt_identity = LLP_TEAM_BETTING_ENGINE
action_server_origin = https://wow-governed-probability-engine.onrender.com
canonical v17 LLP Action schema installed
auth configured without exposing credential
scoreLlpV17SpreadForwardShadow visible in same canonical Action group
valid NCAAF exact-line shadow request reaches backend
spread result remains non-publishable/non-promoting/non-executable
legacy_primary_replit_route_present = false
no player-prop scoring operation in LLP canonical Action
team/event + point-spread ownership = LLP_TEAM_BETTING_ENGINE
prop ownership = WOW_BETTING_ENGINE
global_terminal_authority = false
can_execute = false
```

Until the editor itself is inspected, saved/reloaded, and a fresh-chat spread canary passes:

```text
LLP_CUSTOM_GPT_EDITOR_SYNC = EXTERNAL_SYNC_REQUIRED
V17_BACKEND_CUTOVER_ALLOWED = true
can_execute = false
```
