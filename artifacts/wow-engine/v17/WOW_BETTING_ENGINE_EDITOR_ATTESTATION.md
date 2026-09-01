# WOW Betting Engine — v17 Custom GPT Editor Attestation

Status: `BLOCKED_WITH_EXACT_EXTERNAL_REQUIREMENT`

This packet defines the exact live-editor state required before the WOW Betting Engine may be certified for v17 cutover. Completing this document is an attestation step; it does not itself activate v17.

## Required identity

```text
custom_gpt_name = WOW Betting Engine
custom_gpt_identity = WOW_BETTING_ENGINE
host_role = PLAYER_PROP_AND_SCALAR_INTELLIGENCE
shared_core = WOW_V17_GOVERNED_CORE
nested_custom_gpt_required = false
can_execute = false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = true
global_terminal_authority = false
```

WOW Betting Engine may originate team/event requests, but the backend must resolve their controlling engine to `LLP_TEAM_BETTING_ENGINE`.

## Required Action configuration after candidate entrypoint certification

Canonical schema:

```text
artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml
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
player-prop scoring
prop pick-request ingestion
team-event delegation to LLP controlling engine
write-before-display recommendation recording
recommendation settlement
prop settlement
host-contract inspection
```

## Prohibited live-editor state

A PASS attestation must prove all of the following are absent as primary decision/scoring paths:

```text
Replit-hosted WOW scoring as primary backend
an Action server origin other than the canonical Render service for governed scoring
LLP controlling player-prop scoring
market-implied probability relabeled as model probability
generic/L5/L10 fallback when controlling specialist is missing
host-local FINAL_APPROVED or terminal-ceiling authority
any action or instruction capable of live wager execution
```

Evidence/data vendor tools may exist only as evidence sources if provenance/freshness are captured and they cannot bypass the shared governed core.

## Instruction requirements

The live instructions must preserve these behaviors:

```text
Full Model triggers route through the full mandatory gate sequence.
WOW Daily uses probability-first Pass A then money/slip Pass B.
PLAYER_PROP/PLAYER_SCALAR are owned by WOW_BETTING_ENGINE.
TEAM_EVENT/OUTRIGHT_WINNER/MONEYLINE/UPSET are controlled by LLP_TEAM_BETTING_ENGINE.
Exactly one controlling specialist is allowed per row/event model.
Controlling specialist failure returns MODEL_UNAVAILABLE.
Calibration Health and Dynamic Calibration remain separate gates.
Failure paths change unconditional probability.
Published probability requires calibrated lower bound.
Probability, edge, settlement, money and portfolio are separate objectives.
No downstream stage erases an upstream blocker.
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
full_model_trigger_contract:
wow_daily_contract:
lane_ownership_contract:
terminal_authority_contract:
legacy_primary_replit_route_present:
can_execute:
```

## PASS criteria

```text
custom_gpt_identity = WOW_BETTING_ENGINE
action_server_origin = https://wow-governed-probability-engine.onrender.com
canonical v17 WOW Action schema installed after backend candidate certification
auth configured without exposing credential
legacy_primary_replit_route_present = false
prop ownership = WOW_BETTING_ENGINE
team/event controlling ownership = LLP_TEAM_BETTING_ENGINE
global_terminal_authority = false
can_execute = false
all trigger/governance requirements aligned
```

Until every item is evidenced:

```text
WOW_CUSTOM_GPT_EDITOR_ATTESTATION = BLOCKED_WITH_EXACT_EXTERNAL_REQUIREMENT
V17_CUTOVER_ALLOWED = false
```
