# WOW Betting Engine — v17 Custom GPT Editor Sync Packet

Status: `SOURCE_CONTRACT_READY_LIVE_EDITOR_SYNC_EXTERNAL`

The V17 governed backend may be active independently of the live Custom GPT editor distribution state. This packet defines the exact live-editor state required to synchronize WOW Betting Engine with the already-prepared V17 production source contract. It does not grant execution authority and must not be used to falsely attest an editor change that has not actually been saved.

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

## Production Action source contract

```text
schema = artifacts/wow-engine/v17/openapi.wow-betting-engine.v17.yaml
server = https://wow-governed-probability-engine.onrender.com
auth = Bearer/API key using WOW_ACTION_API_KEY
schema_status = PRODUCTION_SOURCE_CONTRACT
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

A completed sync must prove all of the following are absent as primary decision/scoring paths:

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

The live instructions must preserve:

```text
Full Model triggers route through the mandatory governed sequence.
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

## Editor-sync PASS criteria

```text
custom_gpt_identity = WOW_BETTING_ENGINE
action_server_origin = https://wow-governed-probability-engine.onrender.com
canonical v17 WOW Action schema installed
auth configured without exposing credential
legacy_primary_replit_route_present = false
prop ownership = WOW_BETTING_ENGINE
team/event controlling ownership = LLP_TEAM_BETTING_ENGINE
global_terminal_authority = false
can_execute = false
all trigger/governance requirements aligned
```

Until the editor itself is inspected and saved:

```text
WOW_CUSTOM_GPT_EDITOR_SYNC = EXTERNAL_SYNC_REQUIRED
V17_BACKEND_CUTOVER_ALLOWED = true
can_execute = false
```
