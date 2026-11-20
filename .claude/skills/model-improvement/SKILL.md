---
name: model-improvement
description: Engineer evidence-backed WOW V17 model improvement challengers, calibration checks and regression tests without unapproved probability or gate changes.
---
# Model Improvement — preserve strengths first

1. Identify exact route, fitted model artifact, cohort, immutable pregame prediction IDs and official outcomes. Never use backfilled predictions or mismatched lines/sides.
2. Separate sporting forecast, data/identity, calibration, settlement, card exposure and execution layers.
3. Compute cohort `n`, Brier, log loss, mean predicted vs observed, bias, lower-bound reliability if supported; show uncertainties and cohort thinning.
4. Investigate the specific repeated failure or hard contract violation. Search counterexamples and successful neighboring cases first; confirm whether existing model features already consume the factor.
5. Classify learning state L0 preserve, L1 log, L2 watch, L3 hypothesis, L4 validation eligible. A lone outcome does not justify changing model weights.
6. Engineer a **challenger in isolation** with reproducible chronological train/replay/holdout cutoffs and leakage checks. Report incumbent vs challenger under identical data and exact cutoff.
7. Count improvements **and** previously correct picks harmed. Reject patches degrading Brier/log loss, calibration, safe bounds, or protected cohorts without a justified governed decision.
8. Propose only a narrow patch with rollback criterion and feature/probability provenance. Independent model certification and final reducer authority remain unchanged.
9. Implement and test the approved engineering portion; never directly promote a new fitted probability, loosen a qualification hold, or self-certify.

Output: `hypothesis | cohort n | counterexamples | baseline metrics | challenger metrics | chronology | regressions | rollback | owning PR | independent review | status`.
