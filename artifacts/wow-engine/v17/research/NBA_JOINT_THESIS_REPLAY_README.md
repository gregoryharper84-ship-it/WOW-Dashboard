# NBA two-prop joint-thesis replay — V17 research harness

Status: **OFFLINE RESEARCH ONLY**. This is **not** a fitted model, live scorer,
certification, ranking, card approval, or wager execution capability.
Controlling authority stays with `V17_TERMINAL_REDUCER`; `can_execute=false`.

## Why this exists

The 18-screen video proposes three players from three NBA games with two
stat offers per player, packaged as four cards AB, AC, BC and ABC. The video
explicitly says the missing piece is determining whether the two props move
together under matchup/game-script conditions. The existing V17
`nba_scalar_fitted_candidate.py` already has a *research-only*, joint-residual
candidate; its scores are explicitly uncalibrated/nonpublishable. This separate
evaluation tool does not promote that candidate or invent joint probabilities.

## Replay interface

```bash
python artifacts/wow-engine/v17/research/nba_joint_thesis_replay.py \
    /path/to/immutable_historical_pairs.json \
    --holdout-start-utc 2026-10-01T00:00:00+00:00
python -m pytest -q artifacts/wow-engine/test_v17_nba_joint_thesis_replay.py
```

The JSON document must contain a `records` array. **This PR supplies no genuine
historical dataset or live odds**. The unit test fixtures are synthetic and
cannot establish performance.

Each record must contain:

- `sport="NBA"`, `official_event_id`, `official_player_id`,
  `prediction_id` and `event_start_utc`;
- timestamped immutable forecast identity:
  `prediction_timestamp_utc`, `joint_model_timestamp_utc`,
  `joint_model_artifact_id`, `joint_research_receipt_id`,
  `joint_fit_data_cutoff_utc` (strictly before joint model timestamp);
- frozen pregame matchup bucket `matchup_cohort` and
  `matchup_cohort_asof_utc` (not after prediction timestamp);
- `research_joint_probability` numeric, strictly between zero and one,
  and constrained by the two component marginal Fréchet bounds;
- `official_settlement_source`, `official_settlement_timestamp_utc`
  (after scheduled event start), and exactly two `legs`;
- for each leg: `receipt_id`, `stat_type`, `period`,
  **literal** `exact_line`, `direction="MORE"|"LESS"`,
  `settlement_identity`, `model_timestamp_utc` (before event),
  `calibration_status="PASS"`, pregame `calibrated_probability`,
  and `result="WIN"|"LOSS"|"PUSH"|"VOID"|"DNP"|"UNRESOLVED"`.

The caller must independently authenticate receipt provenance, exact line,
same-player identity, official settlement, and freeze chronology before
supplying records. This CLI validates field presence and timestamps; it
**cannot authenticate a string's origin or verify a ledger by itself**.

## What is computed

The split is based on **event start UTC**, using the caller's fixed
`--holdout-start-utc`. Events before the cutoff are not graded; qualifying
holdout pairs with **both** valid WIN/LOSS outcomes are graded as:

`y=1 iff both legs WIN; y=0 otherwise`.

Any PUSH/VOID/DNP/UNRESOLVED means the joint thesis is excluded from the
binary settled holdout, not counted as a loss. Duplicated exact two-leg player
theses count once. All source rows must reconcile as
prior + holdout + duplicates + rejected, and holdout as
settled + unsettled/void.

Given **supplied** pregame `p_joint` and leg marginals `p1,p2`,
compare candidate `p_joint` against `p1*p2` **only as an explicitly
non-governed naive-independence diagnostic**. Compute Brier, log loss and
observed joint hit rate overall and by frozen matchup cohort. No correlation
coefficient, lower bound, calibrated joint probability, or model support is
created. The minimum cohort size of 30 is **descriptive**, not a certification
threshold. Larger cohorts still cannot certify without provenance, chronology,
statistically appropriate uncertainty, independent holdout, and governance.

## Failure and governance semantics

- Missing immutable receipt, pregame model timestamp, source, or joint
  probability: reject that row; do not backfill/reconstruct.
- Joint probability outside
  `max(0,p1+p2-1) <= p_joint <= min(p1,p2)`: reject, not repair.
- Post-start forecast, post-forecast matchup bucket, non-prior fit data:
  reject as leakage.
- Exact duplicate pair: remove duplicate from grade, retain index in audit.
- No settled holdout: metrics `null`, never false success.
- Output always `RESEARCH_HOLD_NOT_CERTIFIED`,
  `probability_publishable=false`, `rank_eligible=false`,
  `model_promoted=false`, `can_execute=false`.
- Nothing invokes the existing portfolio scoring or bypasses
  `DEPENDENCE_UNQUANTIFIED_SAME_EVENT`, duplicate-thesis/common-hinge
  exposure, market price validation, or V17 terminal reduction.

## Future independent validation

1. Join immutable pregame player/offer/model receipts to authoritative official
   settlement rows with authenticated provenance and strictly prior inputs.
2. Use a **locked** chronology and an untouched forward/holdout sample; evaluate
   unconditional and predeclared matchup cohorts without postgame selection.
3. Compare joint candidate to naive baseline and any existing approved joint
   model using Brier/log loss, reliability, uncertainty, lower-bound validity
   and counterexamples; check model feature ablations only with proper holdout.
4. Separately evaluate four-ticket payoffs using *actual exact ticket prices*
   and fees, plus cross-slip common-hinge exposure and equal-risk alternatives.
5. Require independent certification and governed release *outside* this PR
   before any live probability/ranking/card behavior changes.

If historical receipts or exact prices are missing, state
**DATA_UNOBTAINABLE / RESEARCH HOLD** rather than manufacture results.
