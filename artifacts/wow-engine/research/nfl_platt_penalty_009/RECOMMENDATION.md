# WOW-MODEL-NFL-PLATT-PENALTY-HYPOTHESIS-009 — Recommendation

```text
change_class              = C
status                    = EVIDENCE_COMPLETE_AWAITING_GOVERNED_REVIEW
promoted                  = false
applied_to_production     = false
can_execute               = false
probability_publishable   = false
recommending_agent        = Claude Code (implementation capability, not approval authority)
deciding_authority        = V17 terminal reducer / governed review
date                      = 2026-09-18
```

This is a recommendation, not a promotion. Per the Process Addendum, a Class C
change is never silently promoted however complete the evidence is. The
production file `nfl_event_model_v17.py` is **deliberately unchanged** by this
package. Nothing here is imported by production code.

---

## 1. Discover

sklearn 1.8 deprecated the `LogisticRegression(penalty=...)` kwarg; it is removed
in 1.10. `artifacts/wow-engine/nfl_event_model_v17.py:208` builds the NFL Platt
calibrator as:

```python
platt = LogisticRegression(solver="lbfgs", max_iter=2000, random_state=17, penalty=None)
```

`penalty=None` is genuinely non-default (it requests an *unregularized* fit), so
unlike the four `penalty="l2"` call sites closed under
`WOW-MODEL-SKLEARN-PENALTY-NOOP-008` this cannot be deleted as a no-op. It sits
directly on the NFL Platt-scaling calibration path, which feeds
`calibrated_home_probability` and `calibrated_*_lower_bound`.

On sklearn 1.10 this line stops working, so the lane has a dated deadline.

## 2. Hypothesis

`C=np.inf` expresses the same unregularized objective through the surviving
kwarg, and reproduces `penalty=None` exactly.

Prior evidence was synthetic only: agreement to ~1e-8, described as
solver-noise level. That was never validated against real fitted NFL artifacts
or replayed through historical NFL games — which is why this ticket stayed open.

## 3. Challenger

```python
platt = LogisticRegression(solver="lbfgs", max_iter=2000, random_state=17, C=np.inf)
```

Champion and challenger are fitted on identical inputs by
`replay_harness.fit_platt`, which mirrors `fit_candidate`'s Platt construction
exactly (same solver, `max_iter`, `random_state`, same 1-D logit design matrix).

## 4. Historical replay on real NFL data

Inputs are real, not synthetic (`replay_dataset.py`):

| Item | Value |
|---|---|
| Artifact | `7fee1209-0bed-4bd0-aa0f-fc7a881eff80` — `NFL_EVENT_LOGREG_PLATT_V1_5d603a873fbb` |
| Lifecycle | `CHAMPION`, active, promoted, `probability_publishable=false` |
| Artifact checksum | `aa41eefbbe93f94f9af0401a2c0a7bd9c9505569e8c3754ac396d552a0ead04b` |
| Calibration cohort | real 2024 season, n=285 |
| Validation cohort | real 2025 season, n=284 (untouched holdout) |
| Base logits | the CHAMPION artifact's own scaler + coefficients + intercept, applied to the governed `wow_nfl_pregame_feature_rows` |
| sklearn | 1.9.1 (production version) |

**Replay fidelity check.** Refitting the champion Platt stage on the real 2024
cohort reproduces the registered artifact's own parameters:

| | registered | replayed | abs delta |
|---|---|---|---|
| `platt_a` | 0.9358407245138914 | 0.9358407245138808 | 1.07e-14 |
| `platt_b` | -0.05740265013467314 | -0.057402650134680426 | 7.29e-15 |

The replayed cohort also reproduces the artifact's recorded `calibration_n`
(285), `validation_n` (284) and `baseline_probability` (0.5473684210526316)
exactly. The replay is therefore fed what production was fed.

**Champion vs challenger on that real data — bit-identical, not merely close:**

```text
abs_delta_platt_a            = 0.0
abs_delta_platt_b            = 0.0
validation_brier_score delta = 0.0
validation_log_loss delta    = 0.0
validation_auc delta         = 0.0
validation_ece_10 delta      = 0.0
uncertainty_margin delta     = 0.0
certification_checks         identical, all pass
```

The synthetic-data result was ~1e-8; on the real artifact and real cohorts the
difference is exactly zero in float64.

## 5. Counterexample review

Every real game in both cohorts (569 total) was checked, not just aggregates:

| Cohort | n | max abs Δ probability | max abs Δ lower bound | favourite-side flips | lower-bound threshold flips (0.50/0.55/0.60/0.65/0.70) |
|---|---|---|---|---|---|
| validation 2025 | 284 | 0.0 | 0.0 | 0 | 0 / 0 / 0 / 0 / 0 |
| calibration 2024 | 285 | 0.0 | 0.0 | 0 | 0 / 0 / 0 / 0 / 0 |

No published probability, no calibrated lower bound, and no gate threshold
crossing changes on any real NFL game. No counterexample was found.

**Worst-case search.** 200 bootstrap resamples of the real 2024 calibration
cohort, refitting both variants on each: `max_abs_param_delta = 0.0`,
`max_abs_probability_delta = 0.0`, zero fit failures.

**Adverse-regime probe.** On perfectly separable calibration data — the one
regime where unregularized logistic regression is fragile — both variants return
identical parameters (`max_abs_param_delta = 0.0`). The challenger introduces no
failure mode the champion does not already have. This is expected: they are the
same objective, so they degrade together.

**Negative control.** Finite `C` is *not* equivalent and must not be substituted:

```text
C=1e6   platt_a = 0.9358406954434028   (differs at ~1e-8)
C=1e9   platt_a = 0.9358407244848103   (differs at ~4e-11)
C=1e12  platt_a = 0.9358407245138516   (differs at ~4e-14)
C=inf   platt_a = 0.9358407245138808   (exact)
```

Only `C=np.inf` is exact. Any reviewer tempted to substitute a large finite `C`
should read this row: that variant *does* perturb calibration.

## 6. Holdout / forward validation

2025 is the untouched validation season and was never used to fit either
variant. All eight `fit_candidate` certification checks evaluate identically and
pass under both. No forward-outcome or calibration threshold is altered,
fabricated, or bypassed; forward calibration cohorts are untouched.

## 7. Regression impact

- Production behaviour: none. Identical parameters produce identical
  probabilities, identical lower bounds, identical certification outcomes.
- Artifact identity: `artifact_checksum` and `bundle_fingerprint` are computed
  over the coefficient payload, which is unchanged bitwise, so a refit under the
  challenger registers the same artifact identity. No re-certification is
  triggered by the change itself.
- Observability: the champion emits a `FutureWarning` on every fit; the
  challenger emits none.
- Failure taxonomy: untouched. `MODEL_UNAVAILABLE` / `MODEL_SCORER_FAILED` /
  `MODEL_INPUTS_INSUFFICIENT` / `MODEL_OUTPUT_INVALID` semantics are unaffected.

## 8. Residual item for the reviewer — now closed by CI

`requirements.txt` pins `scikit-learn>=1.4` with no upper bound, and the original
version of this package could only claim verification on 1.9.1 (the production
version). That was the single open question here.

It is now settled empirically. The `NFL Platt sklearn compatibility` matrix
(`.github/workflows/nfl-platt-sklearn-compat.yml`) replays champion vs challenger
on the real cohorts across the whole supported range, and on 2026-09-21 every
released leg passed:

| sklearn | result |
|---|---|
| 1.4.* | pass |
| 1.5.* | pass |
| 1.6.* | pass |
| 1.7.* | pass |
| 1.8.* | pass |
| 1.9.1 | pass |
| 1.10.* | not published yet — leg is inert |

Each released leg asserts the same bar as the main replay: parameter deltas
exactly `0.0`, every metric delta `0.0`, certification checks identical and
passing, bootstrap deltas `0.0`, and zero per-game probability, lower-bound,
favourite-side or threshold-crossing differences. So the equivalence holds on
every version CI can resolve to, not just the production one. No dependency
floor change is needed.

The 1.10 leg cannot run yet because that release does not exist on PyPI. It is
marked `unreleased_ok` and reports "not published yet" instead of failing; it
starts enforcing automatically the moment 1.10 ships, at which point it checks
the `C=np.inf` challenger against the frozen governed 1.9.1 Platt reference
(the champion `penalty` kwarg is removed in 1.10, so the two cannot be fitted
side by side). Only that forward-looking leg may be inert — every released leg
stays strictly fail-closed, so a yanked or unresolvable release is still red.

This does not change the governance position: the recommendation below still
requires governed review, and nothing here promotes anything.

## 9. Recommendation

Adopt the challenger, subject to governed review:

```diff
-    platt = LogisticRegression(solver="lbfgs", max_iter=2000, random_state=17, penalty=None)
+    platt = LogisticRegression(solver="lbfgs", max_iter=2000, random_state=17, C=np.inf)
```

Rationale: bit-identical on the real champion artifact and both real cohorts,
zero counterexamples across 569 real games and 200 bootstrap refits, no new
failure mode, and it removes a dependency on a kwarg that sklearn 1.10 deletes.

**This recommendation does not authorize the change.** Promotion is the V17
terminal reducer's decision under governed review. Until that decision,
`nfl_event_model_v17.py` stays as it is and this ticket stays open.

## 10. Reproducing this

```bash
cd artifacts/wow-engine
python research/nfl_platt_penalty_009/replay_harness.py
```

Full machine-readable output: `replay_report_2026-09-18.json`.
