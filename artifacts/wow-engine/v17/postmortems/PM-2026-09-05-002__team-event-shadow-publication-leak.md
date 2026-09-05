# PM-2026-09-05-002 — Team-event shadow publication leak

- status: DIAGNOSED
- severity: P1
- domain: team-event publication / ranking governance
- created_utc: 2026-09-05T08:07:00Z
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Impact

A research/shadow MLB team-event result could be described downstream with official ranking language when legacy materialization trusted only `probability_publishable=true` and `rank_eligible=true`. The Seattle vs Athletics postmortem exposed the distinction: a shadow research probability could be useful diagnostically while still lacking the governed evidence required for official publication.

## Evidence

The V17 Daily MONEYLINE materializer in `v17/daily_snapshot_runtime.py` previously marked a team/event row `COMPLETED` solely when those two booleans were true. The controlling scorer itself has stronger governance requirements, but the materialization boundary did not independently prove reducer authority, final approval, LLP probability audit, event mutex, post-model/final gates, or a calibrated lower-bound package.

A prior attempted repair in PR #211 was closed without merge and contained no changed files. The missing publication-boundary regression therefore remained open on `main`.

## Sporting-Model Classification

This incident does **not** establish that Seattle's sporting probability was wrong. The realized 7-6 loss is compatible with ordinary favorite-loss variance. No model coefficient, fitted distribution, calibration parameter, or probability is changed by this repair.

## Root Cause

Official presentation/materialization relied on two derived booleans instead of requiring the complete governed team-event publication package. That left a semantic path by which shadow/research output could acquire official `#1`/`strongest` meaning without proving the V17 terminal contract.

## Governance Classification

Publication/ranking boundary defect. Valid sporting probabilities remain diagnostic when the backend contract preserves them; the fix only blocks official publication/ranking until the complete governance package is proven. `V17_TERMINAL_REDUCER` remains the sole global terminal authority and `can_execute=false` remains invariant.

## Linked Engineering Fixes

- FIX-2026-09-05-002

## Closure Criteria

Add a deterministic official-publication guard, wire it into Daily MONEYLINE materialization, preserve the underlying scored probability diagnostically on holds, add a Seattle-style regression proving copied true booleans cannot publish a shadow row, pass protected CI, merge, deploy, and verify production health. No outcome-driven model-weight change is permitted.
