# NFL Pick'em Week 9 postmortem integrity patch — 2026-11-11

Owner: [Pick'em Project Hub #2076](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/issues/2076). This is a downstream consumer repair, NOT an alternate fitted NFL model.

## Measured historical symptom

In the Week 9 visible pool sheet, GH had 10 of 15 correct; the top two visible entries had 14. GH selected the minority of 10 visible entries in five games and won only one of those. These are descriptive **postsettlement** observations, not evidence that opponent cards or pick shares were visible before lock. The sheet alone cannot certify GH's original submitted receipt or timestamp. Any favored-side consensus hindsight benchmark is retrospective only.

## Implemented

1. `nfl_pickem_postmortem.analyze_settled_pickem_week` emits `visible_majority_benchmark` with eligible strict-majority games, retrospective correct count, user correct count on the same set, and the difference. Split games are excluded, rather than selecting an arbitrary popular side.
2. Output explicitly reports `POST_SETTLEMENT_VISIBLE_SAMPLE_ONLY`, unverified opponent-pool and prelock status, and absence of frozen user submission and independently attributable model misses. Do NOT use the retrospective benchmark for pool-win-equity promotion.
3. `nfl_pickem_pool_optimizer.select_pickem_game` checks an explicit kickoff against its immutable model timestamp when supplied, rejecting at/after-kickoff forecasts.
4. When the authoritative team-event scorer supplies `material_pregame_updates`, each row must contain the exact official event ID, an offset-aware `observed_at_utc`, a nonempty `evidence_receipt_id` and change type. An update after model time, even if pregame, demands a governed model rerun. A postkickoff update is rejected. An older update must be linked in `model_material_context_receipt_ids`; duplicates and malformed/naive timestamps fail closed.
5. **Important boundary:** a schedule `fetched_at` does not prove a material player/QB update. Without an event-specific update receipt, output says `NO_MATERIAL_UPDATE_RECEIPTS_SUPPLIED` and counts the gap. This patch does NOT fetch or independently certify an NFL starter, transform research into a probability, override the fitted selection, change calibration or alter an opponent entry.

## Explicitly not solved / upstream owners

- **Authoritative QB/lineup receipt producer and pre-kickoff material recheck**: LLP team/event scorer and canonical availability source, owned upstream of the Pick'em selection layer. Connect official event-specific injury/QB receipt IDs and model-ingestion lineage. Missing context remains visible; no automatic unverified quarterback determination.
- **Authenticated prelock opponent snapshot and GH submitted card:** #1703. No postlock sheet can substitute.
- **Official full-slate immutable model and settlement grade:** #1530; independent accuracy QA #1521.
- **Pool-equity challenger:** existing research-only #1708 / PR #1680. No production differentiation changes.
- **NFL total-points tiebreaker:** existing independently developed model #1090. This patch does not retrain or change its parameters; require actual runtime receipt/visibility to claim tiebreaker readiness.

## Non-negotiable invariants

`MAX_EXPECTED_CORRECT` remains production objective. `V17_TERMINAL_REDUCER` remains sole terminal authority. `can_execute=false`. Missing or stale model evidence must never be described as a certified Full Model prediction. No wagers, entry submissions, Google Sheet modifications or release-gate changes.

## Verification matrix

`PYTHONPATH=artifacts/wow-engine pytest -q artifacts/wow-engine/tests/test_v17_nfl_pickem_pool_optimizer.py artifacts/wow-engine/tests/test_v17_nfl_pickem_postmortem_pool_equity_shadow.py artifacts/wow-engine/tests/test_v17_nfl_pickem_runtime.py`

Specific adversarial coverage: premodel QB receipt linked, newer QB update requires rerun, postkickoff model, postkickoff update, wrong event ID, missing ingestion receipt, naive timestamp, duplicate evidence, schedule refresh not misclassified as QB change, and a majority benchmark containing a vote split. Full production acceptance requires green exact-head hosted tests, independent QA/SIRT/Release decisions, protected merge, deployed SHA readback, and an authentic post-lock settlement plus separate prelock-evidence audit. A passing isolated unit test does not establish any of those later milestones.
