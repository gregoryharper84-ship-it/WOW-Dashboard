# 2026-11-18 Independent SIRT review evidence boundary

- **Date:** 2026-11-18
- **Typed reason:** `SIRT_EVIDENCE_INVALID` / `QA_SIRT_REVIEW_POLICY_CONFLICT`.
- **Mechanism:** The new GitHub review collector dereferenced `repo.owner` and `commit.author` without type validation, which caused exceptions on malformed authenticated evidence rather than a typed HOLD. The protected Release evaluator also initially did not bind a previously issued SIRT approval to the currently enabled SIRT policy, which could accept a stale mode after an environment change.
- **Prediction confirmed:** 3 of 6 API-boundary tests failed with AttributeError/TypeError before repair; source checks found no current-mode binding in either QA or Release before patch.
- **Detection point:** Authenticated GitHub API boundary and independent QA/Release cross-mode acceptance, before a protected review receipt is published or a model is promoted.
- **Fix (staged, not deployed):** Reject malformed upstream owner/commit/check inventories with typed HOLD; require `sirt_enabled=true`, no concurrent Gemini/zero-credit activation, and matching QA and Release opt-in on the exact head.
- **Prevention artifact:** `artifacts/wow-engine/tests/test_v17_sirt_review_evidence.py`, `artifacts/wow-engine/tests/test_v17_sirt_qa_policy.py` and `artifacts/wow-engine/tests/test_v17_sirt_workflow_contract.py`, plus the canonical `SIRT_EVIDENCE_INVALID` / `QA_SIRT_REVIEW_POLICY_CONFLICT` codes in `artifacts/wow-engine/docs/failure_codes.md`.
- **Link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2732
- **Status:** MOVING: local collector red/green observed; latest exact-head CI, separate independent reviewer signature and production acceptance are still required. Not FIXED_AND_VERIFIED.

Every review receipt preserves `can_execute=false`, `model_certified=false`, `probability_publishable=false` and `merge_or_release_authorized=false`.
