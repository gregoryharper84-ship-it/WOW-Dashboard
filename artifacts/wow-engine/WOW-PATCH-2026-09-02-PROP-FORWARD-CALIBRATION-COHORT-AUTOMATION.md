# WOW-PATCH-2026-09-02-PROP-FORWARD-CALIBRATION-COHORT-AUTOMATION

Status: OPEN — BACKEND PRODUCTION READINESS FOLLOW-UP
Date: 2026-09-02
Scope owner: WOW governed backend
Host impact: reporting/orchestration only; calibration lifecycle remains server-owned
can_execute: false

## Verified live state

The live `PROP_PROBABILITY` runtime capability is `AVAILABLE` with certified fitted provider/artifact `WOW_PROP_FITTED_MODEL_V1`, model family `MLB_PITCHER_SO_FAILURE_PATH_NB_V1`, calibration adapter `MLB_PITCHER_SO_CAL_V1`, and calibration method `PHASE_A_PRECALIBRATION_SHRINKAGE`.

At verification time the capability evidence reported `forward_prediction_n=1`, `forward_settled_n=0`, `phase_b_min_settled_n=200`, `phase_c_min_settled_n=500`, `final_approved_allowed=false`, aggregate `rank_eligible=false`, and aggregate `probability_publishable=false`.

Important separation: row-level `wow_predictions` already contains some MLB pitcher-strikeout Phase-A records with complete calibrated probability packages and `probability_publishable=true`. Therefore Phase A or aggregate readiness must not be treated as proof that every row-level sporting probability is unpublishable. Sporting probability publication, rank eligibility, final/card approval, and aggregate calibration readiness are separate contracts.

## Objective

Automate the forward prop calibration lifecycle so canonical pregame MLB pitcher-strikeout predictions can accumulate immutable forward evidence, settle against exact recorded outcomes, populate a valid calibration cohort, and activate later calibration phases only through certified backend readiness rules.

## Required backend work

1. Keep `/score-pick-request` as the canonical screenshot/PDF/pasted-board/autonomous prop scoring boundary.
2. Establish a server-owned forward prediction producer for eligible pregame MLB pitcher-strikeout rows. Freeze exact event/player/stat/line/direction, model artifact identity/checksum, evidence snapshot, timestamp, raw output, and current governed calibration package before first pitch.
3. Establish deterministic post-event settlement against the immutable prediction identity and exact recorded line/direction. No retrospective row mutation or line substitution.
4. Maintain forward prediction/settled cohort accounting from persisted records. Do not manufacture counts or advance a phase from configuration alone.
5. Fit and activate calibration artifacts only when the certified backend readiness contract is satisfied. Thresholds are backend-owned capability metadata; callers/GPTs must not hardcode or override them.
6. Validate every candidate publishable/rankable package: raw model probability; calibrated probability; lower/upper bounds; calibration version/method; model artifact identity/checksum; evidence snapshot ID; immutable prediction ID; publication/rank flags; `can_execute=false`.
7. Preserve valid Phase-A row-level sporting probability when the row contract permits it. Aggregate calibration/final-approval holds may block ranking/card promotion without erasing a completed row-level sporting probability.
8. Keep Wolfram arithmetic audit downstream/separate. A Wolfram credential/audit failure may hold payout/EV claims where required but must not erase completed sporting probability or become `MODEL_UNAVAILABLE`.

## Current infrastructure gap to confirm during implementation

Initial live database inspection found event-specific forward/calibration functions and cron jobs, but no clearly identifiable prop-specific forward cohort producer/settler/calibrator job. `wow_calibrators` was empty at verification time. Implementation must first trace all existing prop settlement/calibration paths and reuse them where authoritative rather than creating a duplicate pipeline.

## Acceptance gates

- New eligible pregame pitcher-K predictions are generated/frozen without user prompting and are distinguishable as forward predictions.
- Settled games deterministically grade the immutable exact prediction and increment the eligible settled cohort once, idempotently.
- Runtime capability counters reconcile to persisted forward/settled rows.
- Calibration phase/artifact changes require the certified server-owned readiness gate and leave an auditable artifact/version record.
- A publishable/rankable row has the complete governed package required by its lane; held/research-only rows cannot be promoted by the host.
- Regression tests cover duplicate generation, late/retrospective prediction rejection, settlement mismatch, missing outcome, phase boundary, malformed calibrator output, and `can_execute=false`.

## Non-goals

- No weakening of fail-closed terminal semantics.
- No GPT-side probability/calibrator fitting.
- No manual flip of `probability_publishable`, `rank_eligible`, or final approval.
- No market-implied or narrative probability substitution.
- No live wager execution.
