# WOW V17 — Projected-Lineup Scenario Modeling + Confirmation Refresh

Date: 2026-09-03
Status: PROPOSED / requires protected CI and production acceptance
can_execute: false

## Problem

V17 must not equate `LINEUP_NOT_CONFIRMED` with absence of the controlling fitted model. Lineup certainty is normally an uncertainty/input state. A valid sporting probability may exist before official confirmation, while rank/final-card publication can remain held pending final refresh.

## Governing states

- `CONFIRMED`: run normal certified fitted model; normal calibration/governance.
- `PROJECTED_HIGH_CONFIDENCE`: certified contextual/projected-lineup model may emit a governed sporting probability; preserve calibrated probability/lower bound; `rank_eligible=false` until confirmation refresh when required.
- `PROJECTED_MEDIUM_CONFIDENCE`: same separation, with a stricter hold ceiling; model must support provisional lineup context.
- `MATERIAL_CONFLICT`: scenario integration only if the controlling fitted model explicitly supports the conflicting identities; otherwise hold/fail closed.
- `DATA_UNOBTAINABLE`: fail closed when lineup identity/context is indispensable to the certified route.

## Scenario rule

Scenario probabilities and weights belong to the controlling fitted model. Governors may validate a model-emitted mixture but must never manufacture weights or manually adjust sporting probability.

If a model exposes scenarios, the mixture must satisfy:

`P(event) = sum_s P(s) * P(event | s)`

with finite nonnegative weights summing to 1.0. A missing mixture is not permission for the governor to create one. Existing certified contextual models may continue to expose only their final integrated distribution.

## Objective separation

A projected-lineup row may simultaneously be:

- `sporting_probability_publishable=true`
- calibrated probability/lower bound present
- `rank_eligible=false`
- `terminal_label=MODEL_QUALIFIED_HOLD`
- `value_qualification_status=PENDING/NOT_EVALUATED`
- `card_qualification_status=NOT_EVALUATED`
- `final_refresh_required=true`

This is not a contradiction. Sporting probability availability and final recommendation eligibility are separate contracts.

## Confirmation refresh

Official lineup confirmation is immutable evidence. If confirmation arrives after a projected score:

1. compare the confirmed lineup identity to the modeled evidence;
2. if materially different, invalidate the stale recommendation state and rerun the controlling fitted scorer;
3. if unchanged/materially equivalent under the certified model contract, refresh governance against confirmed evidence;
4. only then allow rank/final promotion where all other gates pass.

The existing MLB official-lineup ledger and rescore-on-material-change functions remain the source of confirmation truth.

## Player-prop exception

Do not generalize team-event projected-lineup tolerance to every player prop. If the player's own starting/role identity is model-critical, unresolved identity can remain `MODEL_INPUTS_INSUFFICIENT` or another typed hold. MLB 1IP keeps its dedicated projected-top-four contract and hold ceiling.

## Invariants

- Never relabel market probability as model probability.
- Never invent scenario weights.
- Never downgrade genuine model capability to `MODEL_UNAVAILABLE` solely because lineup confirmation is pending.
- Never promote projected-lineup sporting probability directly to final approval without required confirmation refresh.
- `V17_TERMINAL_REDUCER` remains sole terminal authority.
- `can_execute=false` always.
