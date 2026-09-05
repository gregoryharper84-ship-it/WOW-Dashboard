# WOW V17 Prop Family Production Acceptance Matrix — 2026-09-05

Status snapshot created from current V17 repository/runtime audit.

| Family | Certified artifact | Evidence snapshots observed | Governed predictions observed | Acceptance state |
|---|---|---:|---:|---|
| MLB Pitcher Strikeouts | `MLB_PITCHER_SO_FAILURE_PATH_NB_V1` | 138 PASS | 229 total / 223 publishable | PRODUCTION_PROVEN |
| MLB 1st-Inning Pitches | `MLB_1IP_CONDITIONAL_TOTAL_PITCH_PMF_V1` | 0 observed | 0 observed | CERTIFIED_NOT_PRODUCTION_PROVEN |
| MLB Pitching Outs | `MLB_PITCHER_OUTS_WORKLOAD_NB_V1` | 0 observed | 0 observed | CERTIFIED_NOT_PRODUCTION_PROVEN |
| MLB Strikes Thrown | `MLB_PITCHER_STRIKES_THROWN_WORKLOAD_NB_V1` | 0 observed | 0 observed | CERTIFIED_NOT_PRODUCTION_PROVEN |
| MLB Balls Thrown | `MLB_PITCHER_BALLS_THROWN_WORKLOAD_NB_V1` | 0 observed | 0 observed | CERTIFIED_NOT_PRODUCTION_PROVEN |
| MLB Plate Appearances | `MLB_BATTER_PLATE_APPEARANCES_NB_V1` | 0 observed | 0 observed | CERTIFIED_NOT_PRODUCTION_PROVEN |

## Binding production acceptance contract

A family is `PRODUCTION_PROVEN` only after a real, pregame, immutable evidence row for its exact route traverses the canonical WOW prop path and produces all of the following:

1. exact event/player/stat/line identity frozen before event start;
2. `hydration_status=PASS` with no acquisition blockers;
3. controlling specialist resolved server-side;
4. exact active `PROSPECTIVE_CERTIFIED` artifact resolved;
5. fitted distribution inference executed server-side;
6. MORE/PUSH/LESS probabilities derived from one direction-free distribution;
7. calibration/bounds package returned where required;
8. immutable `wow_predictions.prediction_id` persisted;
9. `probability_publishable=true` at the MODEL objective when the lane contract permits it;
10. Phase-A money/final ceilings preserved independently from sporting-probability publication;
11. `can_execute=false` throughout.

## Current primary gap

The five newer MLB families are registered and active, but the connected evidence ledger currently contains no rows for those stat types. Their first unresolved production dependency is therefore acquisition/hydration, not the certified numerical artifacts themselves.

Do not promote any family to `PRODUCTION_PROVEN` from registry presence or unit/integration tests alone. Do not weaken fail-closed behavior to manufacture a successful acceptance row.
