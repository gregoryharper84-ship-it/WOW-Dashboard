# FIX-2026-09-05-002 — Official team-event publication guard

- status: FIX_IN_PROGRESS
- linked_postmortem: PM-2026-09-05-002
- risk: R1
- created_utc: 2026-09-05T08:07:00Z
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Root Cause Being Repaired

The Daily MONEYLINE materialization boundary previously treated `probability_publishable=true` plus `rank_eligible=true` as sufficient proof for an official completed row. Those booleans are necessary but not sufficient to distinguish a complete governed LLP result from a shadow/research artifact whose fields were copied or mislabeled downstream.

## Implementation

Add `v17/team_event_official_publication_guard.py` as a pure fail-closed materialization guard. Official publication now requires all of the following to be proven by the scored payload:

- `probability_publishable=true`
- `rank_eligible=true`
- `can_execute=false`
- global terminal authority is `V17_TERMINAL_REDUCER`
- terminal label is `FINAL_APPROVED`
- LLP probability audit is `PASS_PROBABILITY_AUDIT`
- event mutex is `PASS`
- a valid calibrated probability plus calibrated lower bound is present
- nested LLP governance confirms publication/rank eligibility, reducer authority, no execution, audit PASS, event mutex PASS, post-model gates PASS, final gates PASS, and final candidate label `FINAL_APPROVED`
- no explicit shadow/research-only artifact marker is present

If the guard holds a row, Daily preserves the original scored payload and numeric probability fields for diagnosis, records the prepublication boolean claim, forces official `probability_publishable=false` and `rank_eligible=false`, and materializes the row as `HELD`.

## Non-Goals

No sporting probability, model coefficient, fitted distribution, calibration parameter, market price, or card-thesis penalty is changed. The repair does not reinterpret a Seattle loss as proof of model error. It only closes the publication/ranking semantic leak.

## Regression Tests

- fully governed synthetic team/event result passes
- Seattle-style `FORWARD_SHADOW` row with both legacy booleans true is held
- missing calibrated lower bound is held
- wrong terminal authority is held
- Daily MONEYLINE holds/depublishes a shadow-style boolean leak while retaining the numeric probability diagnostically
- Daily still completes a fully governed team/event result

## Validation Gates

- targeted guard and Daily tests green
- complete protected required checks green
- incident-ledger validator green
- `V17_TERMINAL_REDUCER` remains terminal authority
- `can_execute=false` remains invariant
- no model math/calibration changes
- protected `main` merge succeeds
- Render production deploy reaches live state

## Rollback

Revert the patch merge if protected CI or production verification exposes a regression. No data migration is required.
