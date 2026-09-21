# LLP V17.1 Sharpness Challenger — Initial Replay Evidence

Date: 2026-09-21
Environment: `wow-engine-validation` Supabase project
Mode: READ-ONLY REPLAY / SHADOW
Production mutation: none
can_execute: false

## Scope

The initial replay used immutable `wow_event_predictions` joined to `wow_event_outcomes` for settled MLB slates from 2026-09-03 through 2026-09-20.

Method:

1. Require settled, non-void outcome.
2. Require complete calibrated home/away point probabilities and lower bounds.
3. Deduplicate repeated scans by `(requested_slate_date, official_event_id)`, taking the latest immutable pregame model row. Repeated rows for sampled events had one distinct calibrated probability, so deduplication did not manufacture probability changes.
4. For each event choose the model-favored side by the larger calibrated probability.
5. Compare slate ordering by:
   - calibrated probability (`lambda=0`)
   - uncertainty-adjusted score at lambda 0.25, 0.50, 0.75
   - calibrated lower bound (`lambda=1`)
6. Evaluate Top-1 winner rate, Top-3 hit rate, Top-5 hit rate, and selected Top-3 proper scoring metrics.

## Sample

```text
settled slates: 10
unique deduplicated events: 101
```

This sample is directional evidence only. It is not sufficient for promotion.

## Ranking replay

| lambda | interpretation | Top-1 win rate | Top-3 hit rate | Top-5 hit rate | Top-3 Brier | Top-3 log loss |
|---:|---|---:|---:|---:|---:|---:|
| 0.00 | calibrated probability | 90.0% | 73.33% | 67.35% | 0.22814 | 0.64925 |
| 0.25 | light uncertainty penalty | 80.0% | 76.67% | 69.39% | **0.22778** | **0.64855** |
| 0.50 | midpoint | 80.0% | 73.33% | 69.39% | 0.23113 | 0.65527 |
| 0.75 | heavy uncertainty penalty | 80.0% | 73.33% | 69.39% | 0.23113 | 0.65527 |
| 1.00 | current pure lower-bound ordering | 80.0% | 73.33% | 69.39% | 0.23113 | 0.65527 |

Initial observations:

- Pure calibrated-probability ordering won Top-1 on 9/10 slates versus 8/10 for pure lower-bound ordering.
- Pure lower-bound ordering did not beat calibrated probability on Top-1 on any sampled slate; the strategies tied on 9 slates and calibrated probability was better on 1.
- Lower-bound-heavy ordering was slightly better for Top-5 hit rate.
- `lambda=0.25` produced the best Top-3 hit rate and the best selected Top-3 Brier/log loss in this small replay.
- This supports testing objective-specific ranking instead of assuming one universal lower-bound ranking rule is optimal.

## Example rank-order changes

The replay found days where the same valid probability packages produced different Top-1 candidates depending on ranking objective.

Examples:

```text
2026-09-20
calibrated-probability Top-1: Cleveland Guardians
P(win): 55.43%
LB: 39.83%
result: WIN

lower-bound Top-1: Texas Rangers
P(win): 53.81%
LB: 51.36%
result: LOSS
```

Other sampled days had rank-order changes where both Top-1 candidates won, demonstrating that lower-bound ranking can alter ordering even when it does not alter realized success.

## Rank-eligibility diagnostic

Among rows in the replay window with complete calibrated home/away point probabilities and lower bounds:

```text
rank_eligibility_status=FAIL: 804
rank_eligibility_status=PASS: 7
```

Most common recorded reasons on failed rows:

```text
MODEL_NOT_READY                         804
PROBABILITY_AUDIT_NOT_PASS              804
CALIBRATION_HEALTH_NOT_PASS             803
SCORING_EVIDENCE_SNAPSHOT_INCOMPLETE    803
SELECTED_PARTICIPANT_UNRESOLVED         585
MODEL_STALE_OR_INVALID                  327
MARKET_ROLE_NOT_LOCKED                  219
SELECTED_SIDE_ROLE_UNRESOLVED           219
MARKET_SNAPSHOT_MISSING                 219
MARKET_ROLE_CONSENSUS_FAIL              219
MODEL_PROBABILITY_LEDGER_INCOMPLETE     218
```

Interpretation must remain cautious because many rows predate or span changing governance instrumentation. Still, this shows a large distinction between:

```text
numeric sporting probability package exists
```

and:

```text
current publication/rank eligibility chain is complete
```

That is exactly the separation this challenger is intended to study.

## What this evidence does NOT justify

It does not justify:

- replacing the incumbent production rank metric;
- setting lambda=0 or lambda=0.25 in production;
- weakening hard identity/status/model-output blockers;
- blending sportsbook implied probability into the fitted forecast;
- treating historical governance/instrumentation gaps as harmless;
- automatic promotion.

## Next evidence required

1. Expand the ranking replay to a materially larger chronological sample.
2. Add untouched forward-shadow slates.
3. Segment by probability band, uncertainty-width band, market role, and status certainty.
4. Compare rank-order changes and count previous correct Top-1s flipped vs incorrect Top-1s corrected.
5. Audit uncertainty provenance to determine whether large point-to-LB gaps reflect distinct residual uncertainty or repeated consumption of the same underlying status risk.
6. Add market-divergence recheck diagnostics without blending market probability.
7. Add event sensitivity attribution for starter/lineup/bullpen/weather counterfactuals where certified inputs support them.

## Current conclusion

The challenger hypothesis is **earned for continued shadow testing**.

The current evidence is consistent with the concern that pure lower-bound ordering can sacrifice Top-1 winner discrimination while improving deeper-list conservatism. A light uncertainty penalty may offer a better compromise, but the sample is far too small for production promotion.

Production behavior remains unchanged.
