# SIRT_MODEL_DELIVERY_AUDIT reducer

Module: `artifacts/wow-engine/v17/sirt_model_delivery_audit.py`
Tests: `artifacts/wow-engine/test_v17_sirt_model_delivery_audit.py`
Issue: SIRT P0 independent ML/spread/prop assurance (parents #1562, #1128).

This reducer is **evidence-only**. It does not generate or relabel probability,
certify a model, approve a merge/deploy or change any terminal state.
`can_execute=false`; `V17_TERMINAL_REDUCER` stays the sole terminal authority.

## Capability census

`build_model_delivery_audit(cells, registered_sports=...)` produces a full
sport × `ML`/`SPREAD`/`PROPS` × `PREGAME`/`LIVE` matrix. It covers the 13
canonical sports plus every registered or observed sport. Each cell walks
these stages in order:
`DISCOVERY → CANONICAL_IDENTITY → CURRENT_HYDRATION → FITTED_SPECIALIST →
FAILURE_REGIMES → CALIBRATION_BOUND → CERTIFICATION → ACTION → EXACT_ONCE →
TERMINAL_REDUCER → USER_PATH`. The cell records the first failing stage and
its canonical blocker.

| Status | Requirement |
|---|---|
| `VERIFIED` | Every stage PASS with an evidence ref, plus all of: artifact SHA-256, model version, cert ID, Action receipt, prediction ID, 40-hex deployed SHA, `PRODUCTION` environment, positive candidate/event counts and a fresh source timestamp (≤36h) |
| `SHADOW` | Fitted artifact hash present and the first non-PASS stage is `CALIBRATION_BOUND` or later |
| `BLOCKED_TYPED` | Stage FAIL with a canonical typed code (for example `MODEL_SCORER_FAILED`) |
| `NO_EVENTS` | `DISCOVERY` NO_EVENTS with an authoritative schedule ref and `event_count == 0` |
| `UNKNOWN_NOT_VERIFIED` | Anything else: missing evidence, an untyped or generic failure, duplicate cell evidence, an incomplete receipt, or stale or future evidence |

An HTTP 200, a READY registry, or a 0/0 reconciliation never becomes VERIFIED.

## Forecast quality

`assess_forecast_quality(rows, min_cohort=100)` uses only immutable, pregame,
identity-matched settled forecasts. Identity matching covers the event,
market, period, side and line. Spreads also need the `SIGNED_SIDE_LINE`
convention. Excluded rows are counted with a typed reason: post-start
leakage, a mismatch, a malformed probability, a duplicate, push/void,
unsettled, or a soccer draw logged as a push. For each sport × market ×
cohort the function reports Brier (with standard error), log loss, ECE, a
reliability diagram, sharpness and lower-bound reliability. Below
`min_cohort` it reports `NOT_YET_ESTIMABLE`. A baseline is compared only when
every row in the identical cohort carries one; it is never substituted.

## Independent semantic review

`assess_semantic_review(...)` returns one of `REVIEW_PASS`,
`REVIEW_REJECT_ENGINEERING` or `REVIEW_BLOCKED_HARD_BOUNDARY`. It returns
`INDEPENDENT_SEMANTIC_UNVERIFIED` (HOLD, escalate #2136) for any of these:
self/anonymous, bot (CodeRabbit, github-actions, Copilot), check-run/CI/
intake/author-comment sources, a stale or mismatched SHA, or a PASS without
full-diff, evidence refs and counterexample tests. P-class PASS also needs a
distinct, independent architect PASS on the same SHA. No verdict authorizes
merge or deploy.

CLI: `python -m v17.sirt_model_delivery_audit census|quality <evidence.json>`.
Unreadable evidence prints `EVIDENCE_UNAVAILABLE` and exits 2.
