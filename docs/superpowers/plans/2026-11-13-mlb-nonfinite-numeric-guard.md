# MLB Nonfinite Required-Input Guard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans and test-driven-development.

**Goal:** Preserve MLB prospective model validity by rejecting nonfinite required numeric inputs before simulation.
**Architecture:** Reuse the existing typed `ProspectiveModelUnavailable` validation seam rather than changing scoring/terminal routing.
**Tech Stack:** Python 3, pytest, existing `math.isfinite`.
**Spec:** #1309 MLB sport-specific model input completeness and #455 calibrated prediction-integrity contract.

## Global Constraints
- Only MLB source validator and tests change.
- Do not alter trained artifacts, market prices, model probabilities, release governance or NFL Pick'em.
- Preserve `can_execute=false` and `V17_TERMINAL_REDUCER`.

## Review Focus
- Nonfinite numeric floats, negative infinity, and string forms are typed holds.
- Multiple invalid fields appear together in stable sorted diagnostic.
- Valid signed finite metrics remain unchanged.
- Existing caller uses the existing MODEL_INPUTS_INSUFFICIENT taxonomy.
- No generic exception is relabeled MODEL_UNAVAILABLE.

### Task 1: Reject nonfinite model inputs
**Files:** `artifacts/wow-engine/mlb_event_specialist_v16.py` and `artifacts/wow-engine/tests/test_v17_mlb_nonfinite_required_inputs.py`.
**Interface:** `_required_numeric_inputs(row, fields, source) -> dict[str,float]`, raising `ProspectiveModelUnavailable` on nonfinite fields.

- [x] RED on original implementation in isolated local copy: seven expected failures and one finite-path pass.
- [x] GREEN after minimal `math.isfinite` check: 8 passed locally, py_compile success.
- [ ] Exact-head hosted regressions and independent review.
- [ ] Independent runtime readback before any production-proven claim.

No probability promotion or publication route is changed.

## Post-draft protected-check recovery

The first exact-head Independent QA check was produced while this pull request was a draft and correctly returned `QA_PR_DRAFT`. The PR is now Ready for Review. This branch update triggers fresh normal regression workflows so the existing protected-main Independent QA `workflow_run` path can re-evaluate the current commit and propagate its authenticated decision to Release Authority. A green ordinary CI run or CodeRabbit review is not a substitute for a fresh trusted App check or non-author SIRT semantic acceptance. No check is waived and no merge is authorized by this plan.
