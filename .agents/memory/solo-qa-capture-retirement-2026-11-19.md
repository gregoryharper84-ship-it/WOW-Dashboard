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


## 2026-11-20 — QA/Release latent protected-receipt acceptance

- **Typed reason:** `QA_SOLO_VERIFIED_EVIDENCE_INVALID` (existing canonical code; no new code).
- **Mechanism:** The QA validator accepted dormant `SOLO_PROTECTED_EVIDENCE_READY` / `SOLO_EXTERNALLY_ATTESTED_EVIDENCE_READY` codes, and did not independently check the receipt's `change_class`, changed file allowlist, or rename/unsafe operation evidence. This could misclassify a future forged or misrouted P/C receipt as valid deterministic R evidence, despite the protected solo verifier intentionally never producing a protected-ready code. Release independently calls `qa_findings`, so the gap was shared.
- **Prediction:** A test-only receipt claiming a protected-ready code or using R for a protected file yields `QA_SOLO_VERIFIED_EVIDENCE_INVALID` and `RELEASE_QA_SOLO_VERIFIED_EVIDENCE_INVALID`. Genuine allowlisted R evidence remains PASS.
- **RED:** Test-only head `6f83f35dd83af30e185f7e6cc7c614b607a62e9b`, hosted rapid job `135987758751`, `1 failed, 362 passed`; exact failure `AssertionError: dormant protected code` and `assert 'QA_SOLO_VERIFIED_EVIDENCE_INVALID' in []`.
- **Fix:** QA accepts only `SOLO_R_EVIDENCE_READY` with `change_class=R`, exact safe allowlisted files, no disallowed operations, and honest nonindependent administration audit; Release inherits the typed negative.
- **Prevention artifact:** `test_solo_qa_never_accepts_protected_success_codes_or_nonroutine_changes` in `artifacts/wow-engine/tests/test_v17_solo_qa_release_integration.py`.
- **Link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2738
- **Verification:** RED observed; final exact-head GREEN pending at time of writing. Production fidelity/coverage/qualified delta = 0; no merge or protected approval.


## 2026-11-20 — Four independently reproduced provenance and race defects

- **Typed reasons:** `SOLO_QA_RELEASE_APP_IDENTITY_INVALID`, `SOLO_PR_CHANGED_DURING_VERIFICATION`, `RELEASE_QA_FAILED`; exact Class R path aliasing is prevented by the existing fail-closed P classification.
- **Mechanisms and predictions:** (1) Stripping spaces/replacing backslashes aliased distinct GitHub paths into the R allowlist; preserving the exact API filename predicts HOLD. (2) Only checking that QA and Release App IDs differ let reversed App roles look valid; pinning authentic QA `5245832` and Release `5245853` predicts HOLD. (3) The solo collector compared PR head/ref but omitted base SHA, allowing a changing merge base during evidence capture; binding base SHA predicts HOLD. (4) QA/Release gathered check runs before solo collection, so a later signed QA failure could be hidden by an earlier PASS; refreshing the canonical paged check inventory after collection predicts `RELEASE_QA_FAILED`.
- **Detection point:** CodeRabbit review of PR #2738 and exact-head hosted `wow-v17-rapid-repair` red test.
- **RED:** Test-only head `e088a9ff0cdfeb4590f3258a62383a10389ffcba`; hosted job `135993068807` recorded `4 failed, 363 passed`. Failures were `test_r_allowlist_compares_exact_github_paths_without_normalization_aliases`, `test_reversed_distinct_qa_release_app_roles_fail_closed`, `test_collect_rejects_base_sha_race_even_when_head_is_stable`, and `test_gather_refreshes_qa_check_after_solo_collection_before_release`.
- **Fix:** Minimal changes to `solo_automated_verification.py` and `independent_release_checks.py`; exact filenames, authentic App role IDs, base SHA in PR identity, and refreshed check inventory. No new workflow, vendor, probability or release bypass.
- **Prevention artifact:** The four named regression tests in `tests/test_v17_solo_automated_verification.py` and `tests/test_v17_solo_qa_release_integration.py`.
- **Link:** https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2738
- **Verification:** RED confirmed; GREEN on new exact head pending. No production deployment or protected authorization.
