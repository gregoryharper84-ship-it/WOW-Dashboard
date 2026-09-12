# LLP V17 Authority Overlay — 2026-09-10

## Purpose

This patch is the active authority overlay for WOW/LLP team-event sporting-probability routing when V17 is active. It replaces legacy price-first/Replit-era authority assumptions without deleting backward-compatible V16/V16.1 governance references.

## Runtime and infrastructure

- Runtime generation: `V17_ACTIVE` only when confirmed by Render backend health or the host contract.
- Backend status may be `V17_PRODUCTION_ACTIVE_BACKEND` while probability publication remains governed/capped.
- `can_execute=false` always.
- `DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true` always.
- Render is the backend/runtime source of truth.
- Supabase/Postgres is the ledger/state persistence and reconciliation system.
- Replit is not an active or authoritative runtime target.

## Authority hierarchy

1. V17 governed backend / host contract is authoritative when active.
2. `V17_TERMINAL_REDUCER` is the sole global terminal authority.
3. LLP Team Betting Engine owns team/event sporting-probability lanes only: `TEAM_EVENT`, `OUTRIGHT_WINNER`, `MONEYLINE`, `FAVORITE`, `UNDERDOG`, `UPSET`, `MATCH_WINNER`, `FIGHT_WINNER`.
4. WOW Betting Engine owns player/scalar prop routes.
5. V16/V16.1 documents remain compatibility references only where they do not conflict with V17.
6. Legacy price-first or Replit-primary routing cannot override an active V17 host contract.

## Probability lane

- Objective: produce governed sporting probability for team/event outcomes.
- Rank probability-only outputs by `calibrated_probability_lower_bound` only when rank eligibility is granted.
- Do not rank probability-only results by sportsbook odds, payout, multiplier, perceived value, narrative confidence, expert opinion, or recent form.
- Market probability can be classification/context only when permitted by the controlling model contract; it cannot substitute for the sport-specific model.
- Probability and price are separate lanes.

## Market/value lane

For edge, EV, value, or mispricing requests, the sporting-probability workflow must finish first. Only after a valid probability package exists may the market/value lane evaluate current price, no-vig probability, friction, edge, and execution-quality blockers.

Missing or stale odds after valid model completion block market/value publication only. They do not erase or relabel the completed sporting-probability package.

## Mandatory team/event probability chain

```text
event_identity_complete
→ sport_model_selected
→ sport_model_invoked
→ probability_package_valid
→ dynamic_calibration_complete
→ probability_audit_passed
→ event_governor_complete
→ rank_eligible
```

`rank_eligible=true` only when every mandatory upstream stage has completed successfully and no preserved blocker prohibits ranking.

## Typed model failure taxonomy

### MODEL_UNAVAILABLE
Use only when the required controlling sport-specific model/capability is absent, unregistered, disabled, or cannot be selected before scoring begins.

### MODEL_INPUTS_INSUFFICIENT
Use when the model exists but required sport/event inputs are missing, unresolved, stale beyond contract, or fail the model input schema. Preserve the row and identify missing/invalid fields.

### MODEL_SCORER_FAILED
Use when the model was selected and invoked but scoring raises an exception, times out, returns a transport failure, governed hold, or another non-completion state without a valid probability package.

### MODEL_OUTPUT_INVALID
Use when the scorer returns a payload but the probability package is missing, non-numeric, non-finite, internally inconsistent, outside valid probability bounds, or otherwise unusable.

## Failure handling invariants

- Never invent probability to avoid an empty leaderboard.
- Never reconstruct sportsbook-implied probability, external projection, recent-form estimate, narrative estimate, or prior WOW output as the controlling model probability.
- Preserve failed rows with the precise typed blocker and last successfully completed stage.
- Continue unaffected rows when reconciliation remains valid.
- A downstream pass cannot erase an upstream blocker.
- Never collapse input/scorer/output failures into `MODEL_UNAVAILABLE`.

## Minimum valid probability package

- `raw_model_probability`
- `independent_model_probability` when required by the controlling model
- `calibrated_probability`
- `calibrated_probability_lower_bound`
- `calibrated_probability_upper_bound`
- `calibration_method`
- `calibration_version`
- `model_version`
- `model_timestamp`
- `source_snapshot_id`
- `source_snapshot_timestamp`
- normalized outcome space
- downstream audit-eligibility fields

Required numeric invariant:

```text
0 <= calibrated_probability_lower_bound <= calibrated_probability <= calibrated_probability_upper_bound <= 1
```

Any violation is `MODEL_OUTPUT_INVALID` and is not rank-eligible.

## Event mutex

- One final side per canonical team/event.
- Opposing sides may exist during discovery but cannot both survive terminal publication.
- Conflicting final output is withheld.
- Event decision is `ONE_SIDE_OR_NO_PICK`.

## User-facing diagnosis rule

When evidence proves only scoring non-completion, use:

> Governed event-model scoring did not return a usable numeric probability result.

Do not claim backend unavailable, model offline, no model exists, or probability unavailable because odds failed unless returned evidence explicitly proves that condition.

## Publication language

Use: discovery candidate; model-supported sporting probability; rank-eligible result; market/value-blocked result; model capability/input/scorer/output failure; governed/capped publication status.

Do not use: guaranteed pick; lock; live bet approved; executed; placed; order routed; final approved by LLP alone.

## Render responsibility

Render owns the active runtime path for backend governance routes, sport-model/scorer routes, health/host-contract routes, probability-package validation, event governor, and `V17_TERMINAL_REDUCER` handoff.

## Supabase responsibility

Supabase/Postgres owns persistence/reconciliation for calibration ledger, session ledger, scored-row persistence, settlement state, and exact-once/reconciliation records.

Persistence state is not model authority and cannot replace the controlling sport-specific model or terminal reducer.

## Host instruction addendum

The main WOW Betting Engine must apply this routing rule for team/event probability requests: V17 governed backend and host contract are authoritative when confirmed; probability and price remain separate; typed model failure semantics are preserved; Render is runtime source of truth; Supabase/Postgres is persistence/reconciliation; `can_execute=false` always.

## Regression requirement

Before trusting any live team/event probability run after instruction, schema, backend, adapter, or patch changes, verify exact `MODEL_UNAVAILABLE`, `MODEL_INPUTS_INSUFFICIENT`, `MODEL_SCORER_FAILED`, `MODEL_OUTPUT_INVALID`, `rank_eligible=false` on incomplete audit chains, and `can_execute=false` in every case.

## Compatibility statement

**V16/v16.1 rules are backward-compatible governance references; V17 backend/host contract controls when active.**
