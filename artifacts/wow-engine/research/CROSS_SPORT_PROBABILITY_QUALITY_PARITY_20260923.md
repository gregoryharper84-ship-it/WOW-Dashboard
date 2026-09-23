# Cross-sport team/event probability-quality parity audit — 2026-09-23

Issue: #781  
Status: **PR SLICE / GOVERNANCE PARITY — NO CLASS C PROMOTION**

## Scope

The MLB probability-quality acceptance standard now applies as a governance/evidence contract to:

- MLB
- NFL
- NCAAF (`CFB` is an alias, not a second model authority)
- NBA
- WNBA
- NHL

Equal treatment means identical lifecycle/evidence requirements. It does **not** mean identical sporting models, coefficients, feature sets, distributions, dominance definitions or uncertainty mathematics.

## Shared required dimensions

Every lane must prove:

1. exact fitted sport specialist;
2. quantitative calibration health (Brier, log loss, ECE, calibration intercept/slope and max bin gap);
3. cohort reliability stored separately from event-specific uncertainty;
4. a certified event-specific uncertainty method;
5. immutable early-pregame model grading;
6. immutable final-pregame published grading;
7. sport-appropriate dominance/margin diagnostics;
8. chronological challenger selection;
9. genuinely untouched holdout evidence;
10. typed-failure preservation.

A stored `PASS` label, a registered route, sample count, generic uncertainty width or model import is insufficient.

## Reproduced current-state inventory

### MLB

The mature governed lane remains champion-frozen while #777 / PR #778 investigates probability compression and calibration. It is the reference lifecycle, not a mathematical template for other sports.

### NFL

Validation database evidence:

- fitted team/event artifacts: **10**;
- immutable event predictions: **33**;
- forward-shadow grades: **2**;
- forward calibration-health rows: **9**.

Latest health correctly reports:

- `graded_n=2`;
- `minimum_forward_required=100`;
- Brier `0.20349`;
- log loss `0.58824`;
- ECE `0.42302`;
- status `INSUFFICIENT_FORWARD_EVIDENCE`;
- recommendation `DO_NOT_CERTIFY_YET`;
- `can_execute=false`.

NFL therefore already fails closed on sample sufficiency. It still needs the same explicit final-pregame ledger, event-uncertainty semantics, dominance diagnostics and full calibration intercept/slope evidence before MLB-equivalent quality parity can be declared.

### NCAAF / CFB

Validation database evidence:

- fitted model artifacts: **0**;
- calibrator artifacts: **0**;
- predictions: **0**;
- outcomes: **0**.

The repository contains training/provider/trust infrastructure, but the registry audit states there is no governed team/event winner scorer entry point, probability-package mapping, or event-governor binding. This lane must remain `MODEL_CAPABILITY_UNPROVEN` / unavailable for governed publication until its own fitted specialist lifecycle is completed.

### NBA

Validation database contains **4,804 training games**, but the registry audit still reports no certified V17 team/event winner bridge. Training data does not equal numerical authority. NBA therefore remains model-development only until fitted artifact + calibration + bridge + prospective grading are certified.

### WNBA

Validation database contains **1,041 training games** and the repository declares a WNBA Bradley-Terry request-dependent scorer. However:

- server-owned calibration/hydration is not bound in operational readiness;
- the shared calibrator currently validates a provided `health_status=PASS` label and checks Brier/calibration-error only for numeric range;
- it does not itself require ECE, calibration intercept/slope, max-bin error or immutable forward grading evidence.

The new parity overlay therefore refuses to treat route/artifact PASS as MLB-equivalent probability-quality certification.

### NHL

The repository declares an NHL Elo + goalie/special-teams/OT scorer, but no NHL-specific persisted validation tables were present in the validation DB inventory queried for this audit. NHL also uses the shared request-supplied calibration contract described above. It remains request-dependent and quality-evidence incomplete until its own persisted training/calibration/grading lifecycle is proven.

## Repair in this branch

`v17/team_event_probability_quality_parity.py` adds a same-shaped, fail-closed quality contract for all six sports. It is installed after operational readiness and before outer sport-parity capture.

The overlay:

- never computes a sporting probability;
- never promotes a model;
- never changes rank eligibility;
- never changes calibration coefficients;
- exposes exact parity blockers instead of manufacturing capability;
- normalizes `CFB -> NCAAF`;
- requires quantitative calibration metrics rather than trusting a PASS label;
- blocks test-set selection leakage;
- distinguishes cohort reliability from event uncertainty;
- distinguishes early from final-pregame grading;
- requires dominance/margin diagnostics and untouched temporal evidence;
- preserves `V17_TERMINAL_REDUCER` and `can_execute=false`.

## Model-development work still required

This PR does **not** certify missing models. Class C follow-ons are sport-specific:

- **NFL:** accumulate prospective grades; audit probability resolution/calibration once sample is sufficient; add final-pregame + dominance/margin ledgers.
- **NCAAF/CFB:** finish exact fitted winner model, calibration, governed scorer/bridge, immutable prospective ledger and temporal holdout certification.
- **NBA:** train/validate an exact winner specialist from the existing training corpus, calibrate on chronological data, certify bridge and prospective ledger.
- **WNBA:** replace request-only quality assumptions with server-owned fitted artifact/calibration evidence and prospective grading; audit Bradley-Terry resolution against stronger sport-specific challengers.
- **NHL:** persist fitted artifact/training/calibration/grading evidence, validate goalie/special-teams/OT model resolution and calibration, and certify event uncertainty appropriate to hockey scoring/OT.

No sport may borrow MLB coefficients, probabilities, thresholds or distribution assumptions to satisfy parity.

## Terminal status

For this governance slice: **PR_CREATED** once CI is attached.  
For full #781 closure: **UNFINISHED** until all five non-MLB lanes satisfy the shared evidence contract with their own certified sport-specific artifacts.
