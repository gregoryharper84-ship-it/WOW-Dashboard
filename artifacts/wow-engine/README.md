# WOW External Governed Backend — Reference Implementation

Implements the ratified methodology in
`WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE.md` v2. This is Step 3
of that patch's deployment order.

## Revision history

- **First pass (11/11 tests passing)** shipped, but ChatGPT's code review
  found several tests didn't prove what their names claimed — see
  "Fixed per review" below. `governed_probability_capability` correctly
  stayed `UNAVAILABLE` through this.
- **Current pass (20/20 tests passing)** fixes all 7 review findings and
  adds an 11th deployment gate (a real end-to-end positive-path test),
  which the review also recommended. Still `UNAVAILABLE` — see "Still
  required" below.

## Fixed per review

1. **Future leakage in cross-validation.** `phase_b_platt()` previously
   trained each fold on "all other folds," including later data. Now a
   true walk-forward (expanding-window) split: fold `f` is only ever
   trained on folds `< f`, verified directly against a returned audit
   trail (`test_gate_09b_platt_walk_forward_no_future_leakage`), not just
   inferred from behavior.
2. **Fabricated bootstrap fallback.** `phase_a_shrinkage()` previously
   invented a symmetric interval when no resampler was supplied. It now
   raises `MissingResamplerError` and blocks publication instead —
   matching the same "no silent repair" pattern as `MissingRegimeDataError`.
3. **Money/probability lane coupling.** `determine_publishability()`
   previously erased an otherwise-valid governed probability whenever the
   Goblin/Demon payout was unresolved. Confidence lane and money lane are
   now evaluated independently; an unresolved money lane appends
   `_MONEY_LANE_UNRESOLVED` to the ceiling rather than nulling the
   probability itself.
4. **Invalid market pairs accepted.** `resolve_market_prior()` now
   rejects same-side pairs (`INVALID_SAME_SIDE_PAIR`), enforces a
   freshness window between the two quotes (`STALE_MISMATCH`), and takes
   an explicit `candidate_direction` argument rather than assuming
   whichever quote was passed first is the candidate's side.
5. **Unpersisted Platt coefficients, no isotonic implementation.**
   `phase_b_platt()` now returns fitted `PlattCoefficients` with an
   `.apply()` method for scoring future candidates. `phase_c_fit_isotonic()`
   is a real walk-forward isotonic fit using scikit-learn, not just an
   eligibility/promotion check. The `all([])`-returns-`True` bug in
   `phase_c_isotonic_eligible` is also fixed.
6. **Gate 3 tested nothing.** It checked that six enum names were
   distinct. `regime_model.py` now has `classify_historical_start()`, a
   real deterministic classifier over an `ExitReason`-tagged observation,
   tested against a synthetic batch to confirm every start lands in
   exactly one regime.
7. **Incomplete DB immutability.** `schema.sql` only blocked `UPDATE`
   after event start. Added a matching `DELETE`-blocking trigger, and
   fixed a NULL-logic gap in `chk_calibrated_bounds` where Postgres'
   three-valued CHECK logic would have silently accepted a row with
   `calibrated_probability` set but bounds left `NULL`.

## New: Gate 11 (positive path)

`engine.py::score_prop_end_to_end()` wires regime estimation → simulation
→ Phase A calibration → market resolution → ledger publishability into
one real call. `test_gate_11_end_to_end_positive_path_produces_publishable_probability`
proves a complete, valid, publishable governed probability actually comes
out the other end from fitted (here: clearly-labeled synthetic) inputs —
closing the gap the review flagged: gates 1–10 could theoretically all
pass while the scoring endpoint still produced nothing usable.

## What's real and tested (20/20 passing — `pytest deployment_gate_tests.py -v`)

Gates 2, 3, 4, 5, 6, 7, 9, 10, 11 all have passing tests against the real
ratified logic — not stubs.

## What's NOT done — required before the gate can flip

- **Gates 1 and 8** (schema migration, DB immutability including the new
  DELETE trigger) need a live Supabase instance — untestable in this
  sandbox. Run `schema.sql` against a real project and verify both
  triggers block post-event-start writes and deletes.
- **Per-sport fitted parameters.** Gate 11's synthetic fixtures prove the
  *pipeline* works correctly; they are explicitly not real historical
  distributions. Real cohort regime counts and per-regime stat-rate
  samplers must come from actual data, fitted offline (Colab is
  reasonable for this) and loaded into the service. `api.py`'s
  `/score-prop` still correctly returns 501 for this reason.
- **kappa marginal-likelihood optimization** currently falls back to the
  patch's default (12); the "prefer optimized" path needs a real
  historical dataset to fit against.
- **Deployment infrastructure** — Render account, Supabase project +
  keep-alive ping, environment variables.

## Explicitly not implemented, by design

`GOVERNED_PROBABILITY_CAPABILITY` in `api.py` is hardcoded to
`"UNAVAILABLE"`. Do not flip it until all 11 deployment gate items pass
against the actual live deployment — that flag is a governance statement,
not a feature toggle.
