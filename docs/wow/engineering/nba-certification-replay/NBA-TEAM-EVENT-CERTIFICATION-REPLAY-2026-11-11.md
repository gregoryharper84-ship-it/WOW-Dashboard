# NBA team/event specialist: certification replay package (Class C, for governed review)

Date: 2026-11-11. Status: **EVIDENCE ONLY. No promotion, adapter registration or probability publication is performed or implied.**
`can_execute=false` · `terminal_authority=V17_TERMINAL_REDUCER` · production behavior unchanged.

## Why this exists
Latest Daily run `v17-daily-3f1172cb-...` (serving SHA `a1494317`) held all 10 NBA rows with
`MODEL_UNAVAILABLE` / `NBA_TEAM_EVENT_FITTED_MODEL_OR_ADAPTER_UNAVAILABLE` /
`SPORT_SPECIFIC_TEAM_EVENT_ADAPTER_NOT_REGISTERED` (`model_invoked=false`).
`wow_basketball_team_event_certification` records NBA as `SHADOW / CERTIFICATION_REPLAY_REQUIRED`,
`promotion_attempted=false`. This package is that replay.

## Subject
| Item | Value |
|---|---|
| Artifact | `NBA_TEAM_EVENT_LOGISTIC_V1_20261110T202644Z`, checksum `b842355e83b7b6d6693dd6bfd402881a86f7af10f2cb7eb28090e947c51ac92f` |
| Family / features | `BASKETBALL_TEAM_EVENT_LOGISTIC_V1`, `BASKETBALL_TEAM_EVENT_FEATURES_V2` (8 features) |
| Calibrator | `NBA_TEAM_EVENT_PLATT_V1_20261110T202644Z`, Platt a=-0.0220, b=0.8880 (calibration rows 6,960) |
| Train | 7,117 games, 2019-11-01..2025-03-16. Holdout: 1,784 games, 2025-03-17..2026-11-09 (no overlap) |
| Registry state | `promoted=false`, `active=false` for artifact and calibrator |

## Method
Read-only SQL (`replay_holdout.sql`) re-scores the holdout from the **stored** artifact JSON, stored calibrator
and stored pregame features, independent of the training pipeline's own output.

## Results
| Check | Replay | Persisted | Match |
|---|---|---|---|
| Raw Brier | 0.227159 | 0.227159 | exact |
| Raw log loss | 0.645385 | 0.645385 | exact |
| Calibrated Brier | 0.227394 | 0.227394 | exact |
| Calibrated log loss | 0.645979 | 0.645980 | exact |
| Calibrated bias | -0.0051 | -0.0051 | exact |

- **Skill vs. constant home-win-rate baseline (train rate):** Brier 0.2274 vs 0.2476 (-8.2%); log loss 0.6460 vs 0.6884; accuracy 61.5% vs 54.9%.
- **Leakage:** 0 of 1,784 holdout games have a feature snapshot `as_of` after the game day.
- **Reliability (holdout, calibrated, 10 bins):** ECE 0.0236 (calibrator-fit ECE was 0.0458). Bins with n>=185 are within about 0.03 of observed. Tails are sparse: bin 0.1-0.2 n=3, bin 0.2-0.3 n=44 (predicted 0.269, observed 0.114), bin 0.8-0.9 n=27 (0.835 vs 0.889).
- **Stability by season label (season = ending year):** model beats baseline Brier in every slice: 2025 0.2142 vs 0.2490 (n=308); 2026 0.2309 vs 0.2470 (n=1,322); 2027, the in-progress season, 0.2235 vs 0.2507 (n=154, bias +0.027, small sample).

## Counterexample / regression review
1. **Calibration is marginally worse than raw on the holdout**: Brier +0.00023, log loss +0.00059 (raw bias +0.0052 vs calibrated -0.0051; equal magnitude, opposite sign). Differences are within noise; calibrator-fit-set bias was -0.0406, so the Platt step is correcting a real earlier-era home-advantage shift and has overshot slightly. Reviewer should decide whether Platt adds value over raw for NBA or whether a recalibration with more recent data is preferred. **Not changed here.**
2. Tail reliability (p<0.3 or p>0.8) is under-sampled; any lower-bound/qualification rule must not trust tails on this evidence.
3. This is **retrospective** holdout evidence. The Model Improvement Loop also requires forward validation; none exists yet for NBA.
4. Two artifact versions (`...194344Z`, `...202644Z`) hold identical parameters with different checksums; certify exactly one and bind its checksum.

## What this package does NOT establish (smallest remaining actions)
1. **Independent verification** (`independent_verification_status=PASS`, required by `parse_certification`): must come from an admissible independent principal, not from the author of this package.
2. **Calibrated lower bound** for the team/event lane: not evidenced here; publication requires governed calibrated lower bounds.
3. **Governed decision** on adapter registration + promotion (Class C). Recommended first step if approved: register the adapter in **forward-shadow mode only** (no publication) to start NBA forward evidence, then decide promotion on forward results.
4. Run `scripts/v17_certification_promotion_preflight.sql` against the exact candidate before any promotion attempt.
