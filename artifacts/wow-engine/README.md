# WOW External Governed Backend — Reference Implementation

Implements the ratified methodology in
`WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE.md` v2. This is Step 3
of that patch's deployment order.

## Revision history

- **First pass (11/11 tests passing)** shipped, but ChatGPT's code review
  found several tests didn't prove what their names claimed — see
  "Fixed per review" below. `governed_probability_capability` correctly
  stayed `UNAVAILABLE` through this.
- **Second pass (20/20 tests passing)** fixed all 7 review findings and
  added an 11th deployment gate (a real end-to-end positive-path test),
  which the review also recommended.
- **Current pass (Step 3d re-review, 37/37 tests passing)** fixes the 7
  new findings from ChatGPT's re-review of the 20/20 pass — see "Fixed
  per Step 3d review" below. Still `UNAVAILABLE` — see "What's NOT done"
  below.

## Fixed per Step 3d review

1. **Platt scoring crashed at runtime.** `PlattCoefficients.apply()`
   called `math.log()` but `calibration.py` never imported `math` —
   `NameError` on any real call, invisible to the 20/20 suite because
   no test exercised `.apply()`. Fixed the import;
   `test_gate_09d_platt_coefficients_apply_does_not_crash` now exercises
   it directly.
2. **Publishability gate accepted structurally invalid rows.**
   `determine_publishability()` didn't validate `raw_model_probability`,
   `source_snapshot_id`, or `calibration_status` — a row with
   `raw_model_probability=None`, an empty `source_snapshot_id`, and
   `calibration_status="BOGUS"` came back `probability_publishable=True`.
   All three are now validated (`test_gate_07c`, `test_gate_07d`).
3. **Market freshness wasn't actually enforced.**
   `resolve_market_prior(max_staleness_seconds=60)` called `exact_match()`
   without forwarding that value, so `exact_match()` always used its own
   hardcoded 300s default — quotes 120s apart still passed a 60s request.
   Separately, "fresh" only meant fresh *relative to each other*, so two
   quotes from 2020 two seconds apart passed as a live market. Fixed by
   threading `max_staleness_seconds` into `exact_match()`, and adding a
   second, independent absolute check (`as_of` vs. each quote's
   `retrieved_at`, via `is_quote_fresh_as_of`) that fails closed
   (`MISSING_AS_OF_SCORING_TIME`) if the caller doesn't supply a scoring
   time — see `test_gate_06g/h/i`.
4. **Calibrator persistence didn't exist.** Phase B/C fits lived only in
   the in-memory `PlattFitOutcome`/`IsotonicFitOutcome` returned by
   `phase_b_platt()`/`phase_c_fit_isotonic()` — nothing survived a
   restart. Added `wow_calibrators` (schema.sql) plus
   `calibrator_store.py` (save/load, isotonic-model serialization).
   Separately, Phase B/C per-candidate publication returned
   `calibrated_probability`/bounds as `nan` with no documented reason —
   WOW has a ratified bounds method for Phase A (bootstrap percentile)
   but none yet for Phase B/C. Replaced the silent `nan` with
   `PredictiveBoundsNotRatifiedError`, a named, tested, fail-closed gap
   (see "What's NOT done" — this is a real open methodology question for
   WOW governance, not an implementation bug to paper over).
5. **The orchestrator only ever ran Phase A.**
   `score_prop_end_to_end()` ignored `settled_n_in_cohort` entirely.
   It now routes: `< 200` → Phase A; `>= 200` → look up the active
   persisted calibrator for `parent_cohort` (isotonic if `>= 500`, else
   Platt) via `calibrator_store.load_active_calibrator` — falls back to
   Phase A with a recorded, non-blocking `calibration_ladder_note` if
   none has been promoted yet for that cohort, or blocks on
   `PredictiveBoundsNotRatifiedError` (finding 4) if one has. It also now
   calls `regime_model.apply_current_game_signal()` when a
   `CurrentGameSignal` is supplied, and blocks publication on a material
   contradiction (e.g. `confirmed_opener`). See `test_gate_11c`–`11g`.
6. **Gate 11 never called the actual endpoint.** The positive-path test
   called `engine.py::score_prop_end_to_end()` directly, never
   `/score-prop` — which still 501'd unconditionally even once capability
   reached `AVAILABLE`. `api.py` now wires `/score-prop` to the engine
   through an injectable `FittedParamsBundle` provider seam
   (`set_fitted_params_provider`) and a persistence seam
   (`set_persist_fn`): production ships with neither registered, so the
   route still correctly 409s/501s by default, but
   `test_gate_11h`–`11k` hit the real FastAPI route via `TestClient` with
   a synthetic staging provider and prove a persisted, publishable result
   actually comes back.
7. **Stale "10-point" references.** `api.py`'s governance note and the
   409 error message said "10-point deployment gate" / "all 10" while
   the controlling gate is 11 (`DEPLOYMENT_GATE_COUNT = 11`, referenced
   in both places instead of a hardcoded number).

## Fixed per first review (11 → 20 tests)

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

## Gate 11 (positive path)

`engine.py::score_prop_end_to_end()` wires regime estimation → current-
game signal → simulation → calibration ladder → market resolution →
ledger publishability into one real call, and `api.py`'s `/score-prop`
now calls it for real (Step 3d fix 6) instead of only being provable via
a direct Python call. `test_gate_11_end_to_end_positive_path_produces_publishable_probability`
and `test_gate_11h`–`11k` prove a complete, valid, publishable governed
probability actually comes out the other end — both from a direct engine
call and from the actual HTTP endpoint — from fitted (here:
clearly-labeled synthetic) inputs.

## What's real and tested (37/37 passing — `pytest deployment_gate_tests.py -v`)

Gates 2, 3, 4, 5, 6, 7, 9, 10, 11 all have passing tests against the real
ratified logic — not stubs — including the Step 3d fixes: calibrator
persistence round-trips (`test_gate_09e/f`), calibration-ladder routing
and current-game-signal wiring (`test_gate_11c`–`11g`), and the actual
`/score-prop` HTTP path (`test_gate_11h`–`11k`).

## What's NOT done — required before the gate can flip

- **Gates 1 and 8** (schema migration, DB immutability including the new
  DELETE trigger, and now `wow_calibrators`) need a live Supabase
  instance — untestable in this sandbox. Run `schema.sql` against a real
  project and verify both triggers block post-event-start writes and
  deletes.
- **Per-sport fitted parameters.** Gate 11's synthetic fixtures prove the
  *pipeline* works correctly; they are explicitly not real historical
  distributions. Real cohort regime counts and per-regime stat-rate
  samplers must come from actual data, fitted offline (Colab is
  reasonable for this) and loaded into the service via
  `api.set_fitted_params_provider()`. `api.py`'s `/score-prop` still
  correctly returns 501 in production for this reason — no provider is
  registered by default.
- **Phase B/C per-candidate predictive bounds are methodologically
  unspecified.** WOW-PATCH-2026-08-26 v2 Section 8B.4 ratifies a bounds
  method for Phase A only (bootstrap 10th/90th percentile of the
  shrinkage transform) and cohort-level fit metrics (Brier/log
  loss/ECE/bias) for Phase B/C promotion — it specifies no per-candidate
  predictive-interval method for Phase B/C itself. `calibration.py`'s
  `PredictiveBoundsNotRatifiedError` blocks publication rather than
  inventing one (see the patch's own "METHODOLOGY DECISIONS REQUIRED —
  ChatGPT, not Claude, to specify" section). **This needs a WOW
  governance decision, the same way the original four v1 methodology
  gaps did**, before Phase B/C can ever produce a publishable row —
  it is not implementable from this repo alone.
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
