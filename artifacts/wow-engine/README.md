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
- **Third pass (Step 3d re-review, 37/37 tests passing)** fixed the 7
  new findings from ChatGPT's re-review of the 20/20 pass — see "Fixed
  per Step 3d review" below.
- **PREDICTIVE_BOUNDS_V1 amendment pass (45/45 tests passing)**
  implements the narrow analytical amendment ChatGPT ratified after the
  Step 3d pass — a real per-candidate Phase B/C predictive-bounds method
  — plus the two implementation constraints that came with it
  (timestamp-verified walk-forward chronology; expanded persisted-
  calibrator record). See "Fixed per PREDICTIVE_BOUNDS_V1 amendment"
  below.
- **Step 3d CHANGES_REQUIRED pass 1 (53/53 tests passing)** fixed
  3D-BLOCKER-01 and 3D-BLOCKER-02, the two implementation blockers
  ChatGPT's Step 3d re-review found in `cb9060b` — see "Fixed per Step 3d
  CHANGES_REQUIRED review" below.
- **Current pass (Step 3d CHANGES_REQUIRED pass 2, 70/70 tests passing)**
  fixes 3D-BLOCKER-03 (calibrator training evidence could bypass phase
  minimums) plus a separate timezone-awareness tightening ChatGPT
  identified in the same review round — see "Fixed per 3D-BLOCKER-03 and
  timezone-awareness tightening" below. Still `UNAVAILABLE` — see "What's
  NOT done" below. Per that review's own instructions, this pass does not
  run `scripts/live_gate_validation.py` and does not close Step 4/5 — the
  fixed commit goes back to ChatGPT for Step 3d re-review.

## Fixed per 3D-BLOCKER-03 and timezone-awareness tightening (70 tests)

ChatGPT's Step 3d re-review of the BLOCKER-01/02 fix found that while
`phase_b_platt()`/`phase_c_fit_isotonic()` already reject fitting on too
few observations, nothing enforced the same `PHASE_B_MIN_N`/
`PHASE_C_MIN_N` invariant on the PERSISTED calibrator artifact's own
`training_n` — at either the write boundary
(`save_platt_calibrator`/`save_isotonic_calibrator` accepted any positive
value) or the read/use boundary (`score_prop_end_to_end` only checked the
loaded record's cohort/method, never its `training_n`). The prior pass's
own `test_3d_blocker_01c` demonstrated exactly this bypass: a
`training_n=20` Platt calibrator, treated as valid Phase B evidence.

**3D-BLOCKER-03 fix**: `save_platt_calibrator`/`save_isotonic_calibrator`
now reject a `training_n` below `PHASE_B_MIN_N`/`PHASE_C_MIN_N`
respectively, or one that isn't a real integer (missing, bool, float,
string — no float→int canonicalization). `score_prop_end_to_end`
independently re-validates the loaded active calibrator's `training_n`
before using it — required even with the write-side check in place, since
Supabase may already hold a bad historical row, another write path could
bypass the saver, or a test/custom loader could return a malformed
record; failure surfaces as `MODEL_CALIBRATION_UNAVAILABLE` /
`probability_publishable=False` in the row's `error`/`data_gaps`, never a
silent fallback. `test_3d_blocker_01c` no longer manufactures a valid
Phase B result from under-evidenced training data — see the comment in
that test for why its cheap 20-row cohort-lifecycle fixture and its
calibrator's own `training_n` (now `200`, at the boundary) are
deliberately decoupled numbers, not an inconsistency. See
`test_3d_blocker_03_1` through `_7`.

**Timezone-awareness tightening** (a separate, smaller Step 3d finding,
not itself BLOCKER-03): `ledger.py::_valid_iso_timestamp()` and
`calibration.py::_parse_ts()` both accepted a timezone-naive ISO 8601
string (e.g. `"2026-08-27T00:00:00"`, no `Z`/offset) as valid — parsing
success alone doesn't mean the value represents an unambiguous absolute
instant. Both now additionally require `utcoffset() is not None`.
`compute_predictive_bounds()` converts a naive/malformed `candidate_as_of`
into the standard `ModelCalibrationUnavailableError` (never an uncaught
`TypeError` from a later naive-vs-aware comparison), and excludes
(fails closed on) any individual historical row whose own timestamp is
naive/malformed rather than aborting the whole run. `phase_b_platt`/
`phase_c_fit_isotonic` already raised `ValueError` for malformed
timestamp input; a naive timestamp now falls under that same existing
contract. See `test_timezone_*`.

Recorded but explicitly not fixed here (unrelated to either finding, per
review scope): `PRE_PRODUCTION_BLOCKER_API_AUTH` remains open.

No schema changes were required for this pass either — `training_n` was
already `integer` in `schema.sql`.

## Fixed per Step 3d CHANGES_REQUIRED review (53 tests, cb9060b → this commit)

ChatGPT's Step 3d re-review of `cb9060b` found two real implementation
blockers, both invisible to the existing test suite because every test
that reaches the calibration ladder injects a fake
`load_historical_rows_fn`/`load_calibrator_fn` — the actual defects lived
inside `calibrator_store.py`'s real Supabase query construction, which no
existing test exercised without a live project.

1. **3D-BLOCKER-01 — historical calibration cohort lifecycle / look-ahead
   leakage.** `calibrator_store.load_historical_calibration_rows()`
   required a historical prediction to already carry the target Phase
   B/C `calibration_method`, so a cohort's Phase A observations (the ones
   that got it to N>=200 in the first place) could never become that
   cohort's first Phase B training data — the natural production
   lifecycle could never actually bootstrap. It also used
   `event_start_time` as the "was this available" timestamp instead of
   the outcome's `settlement_timestamp`, which could leak a not-yet-
   settled result into calibration for a candidate scored before that
   result was actually known.

   Fixed: membership is now by `calibration_parent_cohort` alone, not by
   `calibration_method`; `engine.py` now persists `calibration_parent_cohort`
   on every row regardless of which calibration phase produced it (not
   only once a Phase B/C calibrator was already found and used); the
   loader now returns each row's `settlement_timestamp` and excludes any
   outcome missing one (fails closed rather than assuming availability).
   `/settle` now always records a server-generated `settlement_timestamp`
   — without it, the corrected loader would find zero eligible rows in
   practice, reproducing the same symptom under a different cause.
   `scripts/live_gate_validation.py`'s historical-row fixture no longer
   seeds every row as already-PLATT — it seeds real Phase A rows plus a
   late-settling row and a missing-settlement row, and exercises the
   fixed loader against a live project. See `test_3d_blocker_01a/b/c`.

2. **3D-BLOCKER-02 — mandatory model/scoring timestamp.**
   `ScorePropRequest.scored_at` and `PredictionRow.model_timestamp` were
   both optional, and `determine_publishability()` never checked for one
   — a governed probability could publish with no auditable scoring
   timestamp. Several existing tests relied on this (Phase A/fallback
   paths that omitted `scored_at` and still expected
   `probability_publishable=True`).

   Fixed: `determine_publishability()` now fails closed when
   `model_timestamp` is missing or not a valid ISO 8601 timestamp.
   `scored_at` is no longer a field on `ScorePropRequest` at all —
   ordinary HTTP callers cannot supply or backdate it; `/score-prop` now
   always generates it from the server clock. The engine-level
   `score_prop_end_to_end(scored_at=...)` parameter remains the separate,
   controlled path for deterministic validation/backtesting callers (see
   `deployment_gate_tests.py`, which calls the engine directly). The five
   existing tests that omitted a timestamp and expected a publishable
   result were updated to supply one — that gap in their fixtures is
   exactly what this blocker closes. See `test_3d_blocker_02*`.

Recorded but explicitly not touched by this pass, per the review's own
scope instruction: **PRE_PRODUCTION_BLOCKER_API_AUTH** — `/settle` has no
API authentication layer before it reaches the service-role-backed
outcome writer. Harmless for the validator's `127.0.0.1`-only server;
must be resolved before this service is exposed publicly on Render.

No schema changes were required — `wow_predictions.calibration_parent_cohort`
and `wow_outcomes.settlement_timestamp` already existed in `schema.sql`;
this pass only changed how they're written and queried.

## Fixed per PREDICTIVE_BOUNDS_V1 amendment (37 → 45 tests)

ChatGPT's Step 3d re-review found the fixes below directionally correct
but identified one remaining governance gap (item 4's Phase B/C bounds
question) and ratified a narrow analytical amendment to close it, plus
two implementation constraints:

1. **Phase B/C predictive bounds, ratified and implemented.**
   `calibration.compute_predictive_bounds()` (`PREDICTIVE_BOUNDS_V1`):
   for a publishable Phase B/C candidate, filter the cohort's historical
   calibration rows to strictly before `candidate_as_of`, run >= 2,000
   bootstrap realizations (each: resample the eligible cohort, re-sort
   chronologically, refit the active calibrator, draw a candidate raw-
   probability realization via `simulation.bootstrap_candidate_raw_probability_sampler()`,
   apply the refit calibrator), then take q10/q90 of the resulting
   distribution widened to include the full-data point estimate. Every
   named failure condition (too few valid realizations, an excessive
   calibrator fit-failure rate, non-finite/order-violating bounds, no
   eligible historical rows, a cohort/method mismatch on the loaded
   calibrator) raises `ModelCalibrationUnavailableError` and blocks
   publication — see `test_gate_09g`–`09j`. `engine.py`'s routing now
   actually produces a publishable Phase B row end-to-end
   (`test_gate_11de`), not just a failure path that correctly blocks
   (`test_gate_11d`, `test_gate_11dd`).
2. **Walk-forward chronology is now timestamp-verified, not just
   fold-ID-trusted.** `phase_b_platt()`/`phase_c_fit_isotonic()` take a
   required `timestamps` argument and assert
   `max(train_timestamp) < min(validation_timestamp)` for every
   validation fold, raising `ValueError` if fold IDs claim an ordering
   the actual timestamps contradict (`test_gate_09cb`).
3. **Persisted calibrator record expanded.** `wow_calibrators` (schema.sql)
   gained `phase`, `fitted_at`, and `bounds_method_version` columns —
   the reviewer's full persisted-record checklist (cohort key, phase,
   calibration version, fitted-at time, training start/end, `n`, method,
   parameters/artifact reference, bounds-method version) is now
   represented explicitly rather than left to infer from
   `calibration_method`.

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
   WOW had a ratified bounds method for Phase A (bootstrap percentile)
   but none yet for Phase B/C. This pass initially replaced the silent
   `nan` with `PredictiveBoundsNotRatifiedError`, a named, tested,
   fail-closed gap; **the Step 3d re-review then ratified a bounds method
   for Phase B/C too, and this repo now implements it** — see "Fixed per
   PREDICTIVE_BOUNDS_V1 amendment" above.
5. **The orchestrator only ever ran Phase A.**
   `score_prop_end_to_end()` ignored `settled_n_in_cohort` entirely.
   It now routes: `< 200` → Phase A; `>= 200` → look up the active
   persisted calibrator for `parent_cohort` (isotonic if `>= 500`, else
   Platt) via `calibrator_store.load_active_calibrator` — falls back to
   Phase A with a recorded, non-blocking `calibration_ladder_note` if
   none has been promoted yet for that cohort, or scores it via
   `compute_predictive_bounds()` (finding 4 / PREDICTIVE_BOUNDS_V1) if
   one has, publishable or blocked on its own merits. It also now calls
   `regime_model.apply_current_game_signal()` when a `CurrentGameSignal`
   is supplied, and blocks publication on a material contradiction (e.g.
   `confirmed_opener`). See `test_gate_11c`–`11g`, `test_gate_11d`–`11de`.
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

## What's real and tested (70/70 passing — `pytest deployment_gate_tests.py -v`)

Gates 2, 3, 4, 5, 6, 7, 9, 10, 11 all have passing tests against the real
ratified logic — not stubs — including: calibrator persistence
round-trips (`test_gate_09e/f`), the ratified Phase B/C predictive-bounds
method (`test_gate_09g`–`09j`), timestamp-verified walk-forward chronology
(`test_gate_09cb`), calibration-ladder routing including a genuine
publishable Phase B positive path (`test_gate_11c`–`11de`), calibrator
training-evidence phase minimums enforced at both write and read
boundaries (`test_3d_blocker_03_1`–`_7`), timezone-aware governed
timestamps (`test_timezone_*`), the corrected
historical-calibration cohort lifecycle and mandatory scoring timestamp
(`test_3d_blocker_01a`–`02e`),
current-game-signal wiring (`test_gate_11f/g`), and the actual
`/score-prop` HTTP path (`test_gate_11h`–`11k`).

## Batch 3: live validation

`scripts/live_gate_validation.py` runs the full live-Supabase sequence
(calibrator persistence, `load_historical_calibration_rows()`, a real
`compute_predictive_bounds()` run from Supabase-loaded rows, the actual
`/score-prop` HTTP handler via a local `uvicorn` server hit with real
HTTP against real Supabase persistence, a read-back comparison, and a
negative-path regression) in one shot. It needs `SUPABASE_URL` and
`SUPABASE_SERVICE_KEY` as environment secrets (never chat text) and
`schema.sql` already applied via a direct Postgres connection — see the
script's own docstring for the exact prerequisites and usage. It cannot
be run or verified from this sandbox; its HTTP/uvicorn mechanics were
smoke-tested offline against a stubbed persistence layer (real sockets,
real `/score-prop` call, real 200 + publishable response), but the
Supabase-specific sections (2, 3, 4, 7, 8) are unexercised until a real
project exists.

Preparing this script surfaced two real gaps, now fixed: `PredictionRow`
had `calibration_version`/`calibration_training_n`/`calibration_parent_cohort`
fields that `engine.py` never populated for Phase B/C rows, and
`model_timestamp` (the scoring run's `scored_at`, distinct from
`event_start_time`/`created_at`) didn't exist on the ledger at all. Both
are now wired through and covered by `test_gate_11`/`test_gate_11de`.

## What's NOT done — required before the gate can flip

- **Gates 1 and 8** (schema migration, DB immutability including the new
  DELETE trigger, and now `wow_calibrators`) — the DDL/constraints/
  triggers/calibrator-artifact round-trip all passed against an
  ephemeral local PostgreSQL 16 instance with zero schema changes
  needed (see git log for the local-Postgres-stopgap commits). That is
  **not** the same as live Supabase: it doesn't exercise `supabase-py`/
  PostgREST, service-role permissions, or `calibrator_store.py`'s and
  `load_historical_calibration_rows()`'s actual query code paths —
  those still need a live Supabase instance, which `scripts/live_gate_validation.py`
  is ready to run the moment one exists.
- **Per-sport fitted parameters.** Gate 11's synthetic fixtures prove the
  *pipeline* works correctly; they are explicitly not real historical
  distributions. Real cohort regime counts and per-regime stat-rate
  samplers must come from actual data, fitted offline (Colab is
  reasonable for this) and loaded into the service via
  `api.set_fitted_params_provider()`. `api.py`'s `/score-prop` still
  correctly returns 501 in production for this reason — no provider is
  registered by default.
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
