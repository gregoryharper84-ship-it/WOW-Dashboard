# NFL moneyline challenger — forward settlement grade preparation

Issue #1523. Class B research/persistence-adjacent implementation. Not serving, not a model change, not a certified pick.

## Confirmed evidence
- The V17 fitted production NFL moneyline model and its Pick'em downstream selector are unchanged.
- Research shadow: 2026_04_ATL_NO, prediction created pregame on October 5, selected Atlanta, calibrated selected probability 0.581583343499892, promotion_authorized=false, can_execute=false.
- Authoritative persisted settled game: away ATL 45, home NO 24, home_win=false, tie=false, source captured after kickoff. Its research shadow remains ungraded.
- Incumbent 24 unique already graded pregame predictions have 11 successful selected sides. That is a small **non-exhaustive** cohort, not the NFL weekly top-1 success rate. Investigate confidence calibration, OOD inputs and grading integrity under #1342.

## Safe implementation
`v17/nfl_ml_challenger_forward_grader.py` exposes a deterministic **pure** function to validate the official settled game against the immutable pregame research prediction and return grade fields. It refuses post-kickoff predictions, erroneous event and side identity, premature/invalid settlement, unresolved ties, missing snapshot/hash, non-binary source outcomes, malformed model values, incorrect research lifecycle state and any governance drift.

The returned `update_fields` contains only `lifecycle_state=GRADED`, integer outcome, hit boolean, Brier, log loss and grading timestamp. It **does not persist** or modify a fitted model, calibrated probability, lower bound, frozen prediction, production recommendation, model registry or order.

## Review and completion requirements
- Model Research independently validates official winner/identity and source snapshot, and compares same-event champion and challenger without hindsight feature adjustment.
- SIRT and independent QA adversarially review typed failures, duplicate/retry idempotence and strict timestamp provenance.
- Engineering routes a separate governed Class B database writer after approval, using an idempotent transaction, exact shadow ID + FORWARD_SHADOW CAS, source identity and durable immutable grade receipt.
- Release verifies exact revision and no shared-lane regression; do not imply the passive grader endpoint is deployed as a public operation.
- Do not promote NFL Class C challenger without chronologically separated holdout, accumulated forward-grade evidence, calibration/lower-bound integrity and independent governed review.

```text
custom_gpt_identity=WOW_BETTING_ENGINE
runtime_generation=V17_ACTIVE
terminal_authority=V17_TERMINAL_REDUCER
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
```
