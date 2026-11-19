# Solo QA capture retirement contract cleanup (2026-11-19)

- **Date:** 2026-11-19
- **Typed reason:** `QA_LEGACY_INDEPENDENT_CAPTURE_RETIRED`
- **Mechanism:** Protected QA workflow contained a legacy Gemini source-review capture stack (credentials, preflight, capture, inference, PR-diff recheck, attest, artifact upload), and seven downstream CI checks asserted this stack must always exist. Removing the stack without updating its verification contracts causes guarded tests to fail; allowing independent/canary mode to silently fall back would be an unsafe release bypass.
- **Prediction:** When retired `independent` or `canary` mode is selected, workflow fails explicitly, terminal QA publishes HOLD for the exact PR head, and no Gemini capture/inference/credential/reviewer App invocation occurs. Routine and solo verified mode retain prior deterministic checks; P/C remain HOLD absent genuine independent approval.
- **Detection point:** Full `wow-v17-rapid-repair` and governed-backend exact-head regression on draft PR #2738.
- **Observed RED:** Test-only head `595c353b` contained the old capture and contradicted new absence assertions. Removing old steps first exposed older workflow-test requirements; at `2e43d3e` 3 protected reviewer contract tests failed, and at `7b097dad` full-engine shard/1 failed 7 old contract tests (3262 passed). These are observed artifacts, not release success claims.
- **Fix:** Draft-only removal of old capture pipeline (~9 KB); explicit retired-mode error plus existing terminal signed QA HOLD; migrate outdated tests without deleting legitimate QA/source verifier unit tests; update producer-count expectations and register one canonical typed failure. Protect exact-head and App identity invariants.
- **Prevention artifact:** `artifacts/wow-engine/tests/test_v17_solo_qa_release_integration.py`, `artifacts/wow-engine/test_v17_independent_release_checks.py`, `.github/scripts/tests/test_independent_source_review_receipt.py`, and `artifacts/wow-engine/docs/failure_codes.md`.
- **Link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2738
- **Status:** P-class draft; exact-head GREEN and independent authorization still required before protected release. No production change, no probability promotion.
- **Scoreboard delta:** Coverage, qualified, and production fidelity = 0 until separately verified release.
