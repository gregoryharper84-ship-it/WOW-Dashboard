# Wave 3 FB-EVIDENCE-20261112 — fitted-model evidence packet (research-only)

Date: 2026-11-12. Owner: Freebuff independent model research. No certification,
promotion, publication, execution or production change is made or claimed here.
`can_execute=false` throughout.

## Decisions (all lanes REMAIN_SHADOW)

| Lane | Decision | Valid n / status |
|---|---|---|
| FB-NFL-ML forward calibration | `DO_NOT_CERTIFY_YET` / `REMAIN_SHADOW_MORE_EVIDENCE_REQUIRED` | 95 valid forward grades < 100 minimum; 5 short. Reaching 100 is necessary, not sufficient. |
| FB-NFL-FIVE (5 count props) | `MODEL_BUILD_REQUIRED` per family; FANTASY_SCORE `CANDIDATE_ONLY` | No per-family fitted evidence in this packet. |
| FB-NBA-ML | `REMAIN_SHADOW_MORE_EVIDENCE_REQUIRED` | Triad + chronology + OT + prelock preflight only; bounds policy review outstanding. |
| FB-NHL-ML | `REMAIN_SHADOW_MORE_EVIDENCE_REQUIRED` | 6,128 accounting enforced; `SOURCE_RIGHTS_OR_GOALIE_DATA_HOLD`. |
| Triad packaging (§E) | `TRIAD_BOUND_READY_FOR_REVIEW` at most | No lane is certified, promoted, publishable or executable. |

## NFL ML forward evidence (§A)

- Pure replay in `v17/wave3_nfl_ml_readiness.py`: paired Brier / log loss /
  ECE / calibration bias / reliability / lower-bound gap, same thresholds as
  `v17.nfl_forward_shadow.calibration_health`.
- Synthetic 95-row test cohort mirrors the Nov-11 validation observation
  shape (mean forecast ≈ 0.6555, lower ≈ 0.5996): ledger emits
  `FORWARD_GRADED_N_95_LT_100`, `missing_to_100=5`, decision
  `DO_NOT_CERTIFY_YET`.
- Certification package helper always returns `certified=false`,
  `probability_publishable=false`, `can_execute=false`.
- No database read, no retrospective prediction, no pooled old champions.

## NFL five count props (§B, PR #2605 stays source of record)

- Registry only in `v17/wave3_nfl_five_prop_evidence.py`. Nothing in this
  packet fits, imputes, relabels or promotes a model.
- UNKNOWN stays UNKNOWN: 3,994 snap-positive / weekly-missing rows remain
  quarantined; 48 PFR↔GSIS gaps stay open; the two residuals (Rodgers 2025
  W02 PIT pass attempts; Ogunbowale 2024 W15 HOU rush attempts) stay visible.
- Poisson is challenger-only; train 2021–2023 / calibration 2024 / holdout
  2025; old-four nonregression gate keeps candidate published delta at +0.

## NBA (§C) / NHL (§D)

- NBA: disjoint train/calibration/holdout by event time, OT settlement basis,
  no post-tipoff features; hard negatives (same-pair consecutive days, late
  scratch, missing tip time, stale source, swapped home/away, missing
  artifact/calibrator/bounds) all fail closed.
- NHL: unique-game accounting enforces 6,128 and rejects the contradicted
  8,580; full-game WIN requires OT/shootout; goalie prelock + permitted
  source + second corroboration all required, otherwise
  `SOURCE_RIGHTS_OR_GOALIE_DATA_HOLD`.
- No unapproved scraping; no odds-as-probability.

## Reproducibility

```sh
PYTHONPATH=artifacts/wow-engine python -m pytest -q \
  artifacts/wow-engine/test_wave3_nfl_ml_readiness.py \
  artifacts/wow-engine/test_wave3_five_prop_nba_nhl.py
```

Live Supabase reads, nflverse SHA rechecks, current-head CI and independent
SIRT/governed certification remain separate and are not claimed here.
Remaining blockers: 5 valid forward grades; per-family fits + 2025 holdout
replays; NBA fitted artifact/calibrator/corpus lineage; NHL goalie source
rights + corroboration; exact-head review of this branch.
