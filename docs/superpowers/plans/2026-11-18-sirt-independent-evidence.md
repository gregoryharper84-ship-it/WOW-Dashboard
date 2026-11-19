# Option A — Independent Evidence Verification Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: use superpowers:test-driven-development and superpowers:verification-before-completion for each task.

**Goal:** Replace reliance on paid Gemini source reviews with independently authenticated, reproducible, read-only evidence while preserving independent protected-change signoff and fitted-model scientific certification.

**Architecture:** Use existing GitHub and three native exact-head checks for an evidence-only SIRT review admission receipt; reuse existing per-sport scientific replayers, and have existing QA/Release Authority publish protected decisions only after validated independent evidence. No parallel gate service, no model fitting, no automatic trading, no self-approval.

**Tech stack:** Python stdlib, pytest, GitHub REST / Actions, existing V17 release/checks and forward replay.

**Spec:** Owner selected Option A in the November 18, 2026 WOW Engineering conversation.

## Non-negotiable boundaries

- custom_gpt_identity=WOW_BETTING_ENGINE
- runtime_generation=V17_ACTIVE
- terminal_authority=V17_TERMINAL_REDUCER
- can_execute=false
- DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
- P/C changes require exact-head independent signoff and negative/security/concurrency/fail-closed checks; no altered fitted probability, threshold, calibration, qualification rule or scientific acceptance without a separately governed promotion.
- An observation that independent SIRT reviewed a source PR is NOT scientific model certification and NOT permission to merge/deploy.
- The SIRT reviewer principal is configured in protected GitHub environment as WOW_SIRT_REVIEWER_USER_ID, never accepted from PR body or an attacker-controlled artifact.

## Task 1 — Read-only SIRT exact-head evidence observer

**Files:** artifacts/wow-engine/v17/sirt_review_evidence.py; artifacts/wow-engine/tests/test_v17_sirt_review_evidence.py.

- [x] Write negative tests for author/owner/head-commit self-review, stale review SHA, dismissed/rejected latest review, wrong check App, changed PR head, draft and missing checks.
- [x] Verify local red→green for reviewer-independence failure class (three tests red when independence guard disabled, 16 green when restored).
- [x] Implement a read-only GitHub REST collector and evaluator that rejects incomplete/ambiguous authenticated evidence.
- [ ] Run hosted affected suite and all three required checks at exact PR head; repair any genuine failures.
- [ ] Independently verify that the documented SIRT principal is a distinct accountable reviewer, not an engineer/owner self-review.

**Contract:** Input: GitHub-authenticated PR, reviews, head commit, repository owner and native check runs. Output: typed EVIDENCE_READY/HOLD receipt, always can_execute=false, model_certified=false, probability_publishable=false, merge_or_release_authorized=false.

## Task 2 — Separate scientific acceptance

**Files:** existing v17/nfl_exact_version_evidence_replay.py and corresponding sport-specific replayers/certification registries; no new scorer.

- [ ] Reuse existing exact-version replay when actual historical/pregame immutable rows and verified health are available.
- [ ] Check chronology, artifact SHA, model identity, holdout calibration arithmetic, sample sizes, source rights and calibrated lower bounds per sport/market.
- [ ] Bind independently accepted science receipt to the same model and calibration artifact hashes.
- [ ] Missing/contradictory evidence must HOLD; no odds/heuristic/fallback probability and no qualification promotion.

## Task 3 — Protected production integration (separate P-class review)

**Files:** existing .github/workflows/wow-v17-independent-qa-check.yml, .github/scripts/independent_source_review_receipt.py, v17/independent_release_checks.py plus tests.

- [ ] Add a governed SIRT review mode that reads a protected reviewer principal and Task 1 receipt, not a PR supplied approval flag.
- [ ] Bind real scientific acceptance evidence from Task 2 for C/model changes.
- [ ] Preserve signed exact-head QA and distinct Release Authority check publication; failures always HOLD the candidate and do not contaminate unrelated main deployments.
- [ ] Preserve current Gemini mode until a P-class migration has independent acceptance; do not flip env variables to simulate SIRT review.
- [ ] Negative tests: forged reviewer ID, owner/author approval, stale SHA, wrong-App green check, missing replay, modified model artifact, conflicting approval, duplicate or superseded event, publisher/token failure.
- [ ] Independent P-class review, exact-head CI, protected merge, Render web/worker live SHA and actual Action→Supabase→terminal verification.

**Review Focus:** API pagination/truncation; HEAD SHA race; GitHub null-identity commits; more recent CHANGES_REQUESTED; scientific evidence that belongs to another artifact.

**Release state:** Task 1 is an evidence producer only. No deployment, no fitted-model certification and no changed required checks are authorized by this plan.
