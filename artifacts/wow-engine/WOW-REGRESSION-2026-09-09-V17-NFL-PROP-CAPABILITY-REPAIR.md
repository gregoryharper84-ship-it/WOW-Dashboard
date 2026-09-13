# WOW-REGRESSION-2026-09-09-V17-NFL-PROP-CAPABILITY-REPAIR

## R1 — Scope ownership
Input: NFL player prop.
Expected: controlling_engine=WOW_BETTING_ENGINE; LLP prop route prohibited; can_execute=false.

## R2 — Missing exact artifact
Setup: NFL stat tuple has no active fitted artifact.
Expected: MODEL_UNAVAILABLE; blocking_scope=CAPABILITY; specialist_selected=false;
specialist_invoked=false; probability_publishable=false; rank_eligible=false; can_execute=false.

## R3 — Global MLB prop availability cannot leak to NFL
Setup: MLB pitcher strikeout artifact available; NFL requested.
Expected: NFL remains MODEL_UNAVAILABLE; no inheritance from global PROP_PROBABILITY=AVAILABLE.

## R4 — Registered NFL model, evidence missing
Expected: MODEL_INPUTS_INSUFFICIENT; exact missing_fields;
specialist_selected=true; specialist_invoked=false; can_execute=false.

## R5 — Registered NFL model, scorer throws
Expected: MODEL_SCORER_FAILED; specialist_invoked=true;
no probability package; rank_eligible=false; can_execute=false.

## R6 — Malformed distribution
Setup: MORE+LESS+PUSH does not reconcile, output is non-finite, artifact mismatch,
or provenance missing.
Expected: MODEL_OUTPUT_INVALID; exact failing_fields;
distribution_valid=false; rank_eligible=false; can_execute=false.

## R7 — Valid continuous yardage distribution
Expected: valid expected value/variance and side probabilities;
artifact/provenance match; failure-path then calibration run.

## R8 — Valid discrete distribution
Expected: PMF reconciles; MORE+LESS+PUSH=1 within tolerance; downstream stages allowed.

## R9 — Stale model after material update
Setup: model_timestamp < latest_material_update_timestamp.
Expected: prior package invalidated; rerun required; no publication.

## R10 — Market/odds failure after valid model completion
Expected: prop probability package preserved; market/value lane may block separately;
never relabel MODEL_UNAVAILABLE.

## R11 — Market implied substitution prohibited
Setup: no NFL fitted artifact but sportsbook/PrizePicks line exists.
Expected: no model probability populated from price; MODEL_UNAVAILABLE.

## R12 — L5/L10 substitution prohibited
Setup: no fitted artifact but recent-game log exists.
Expected: recent form retained as evidence only; no controlling probability.

## R13 — Health scope
Expected: prop_bridges.NFL.<stat_type> exposes registered/importable/scorer/evidence readiness.

## R14 — Governance scope
Expected: global prop-service health and exact sport/stat capability are separate;
MLB availability cannot imply NFL availability.

## R15 — Row reconciliation
Setup: 12 NFL directional rows, all missing exact artifacts.
Expected: 12 retained as capability-blocked rows; 0 model-completed; counts reconcile.

## R16 — Partial family support
Setup: two NFL stat families registered, one unsupported.
Expected: supported rows continue; unsupported rows retain MODEL_UNAVAILABLE;
run may be partial; no all-or-nothing collapse.

## R17 — Startup wiring
Setup: artifact registered in DB but Python registration/import missing.
Expected: bridge DOWN/DEGRADED; no claim of model readiness.

## R18 — Duplicate active provider
Setup: two active artifacts for exact NFL sport/stat tuple.
Expected: SPECIALIST_ROUTING_CONFLICT; no arbitrary selection; no publication.

## R19 — Action observability
Expected response body includes route, operation contract, request ID, run ID,
and backend completion status. Do not invent HTTP status if transport status is hidden.

## R20 — Execution invariant
All cases, including fully valid package: can_execute=false.
