# Niche Props Fitted Shadow and Joint Slip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans task-by-task; RED then GREEN, and verify fresh exact-head tests.

**Goal:** Add a scientifically bounded niche-prop shadow fitting/evaluation engine, rights-aware offer receipt validation and aligned scenario slip diagnostics without enabling uncertified predictions.

**Architecture:** Independent research modules depend on no production scoring or market connectors. Input provenance is caller-claimed and remains untrusted until separately authenticated; all results research-only. Existing PR #2841 taxonomy and preflight keep authority.

**Tech Stack:** Python 3, standard library (math, hashlib, json, datetime, hmac), pytest, GitHub Actions.

**Spec:** docs/superpowers/specs/2026-11-23-niche-prop-shadow-model-offer-joint-design.md

## Global Constraints
- V17_TERMINAL_REDUCER sole authority; can_execute=false; no wagering.
- No provider scraping, paid data, production certification or synthetic forward evidence.
- Separate training/calibration/evaluation cohorts with source timestamp and identity guarantees.
- Exact lines, period, two-kicker identities and platform tier must remain distinct.
- Do not claim live quote authentication merely because a signature validates.

## Review Focus
1. Leakage through post-start features, repeated official event ID across splits or reused calibration test data.
2. Same-team kickers, changed line/period, or substituted NBA player on offer or payout envelope.
3. Receipt signer spoof, wrong provider rights, stale/future capture, and unsigned payout.
4. Scenario outcomes with missing legs, unrecognized PUSH/VOID, duplicate samples or unaligned lineup.
5. Small/degenerated data samples, numerical instabilities, class-perfect separation and overconfidence.

---

### Task 1: Fitted, chronological niche route shadow

**Files:** Create artifacts/wow-engine/v17/niche_prop_fitted_shadow.py; test artifacts/wow-engine/v17/test_niche_prop_fitted_shadow.py

**Interfaces:** fit_niche_shadow(rows, route, min_train, min_cal, min_test) -> dict with artifact, independent OOS metrics; predict_niche_shadow(artifact, row) -> research-only probability/receipt.

- [ ] Write tests proving nonleaky cohort splits, deterministic fit, malformed data holds, no certified probability.
- [ ] Demonstrate RED (feature missing).
- [ ] Fit standardized regularized logistic from train only, calibrate on later holdout, report OOS metrics; no production publication.
- [ ] Demonstrate GREEN and exact data chronology.
- [ ] Commit.

### Task 2: Independently controlled signer and quote payload integrity

**Files:** Create artifacts/wow-engine/v17/niche_prop_quote_integrity.py; test artifacts/wow-engine/v17/test_niche_prop_quote_integrity.py

**Interfaces:** seal_capture_for_trusted_collector(payload,key) -> signed envelope; verify_capture(envelope,key,expected,as_of,approved_sources) -> status.

- [ ] Write tests for altered signature/line, wrong event/player, missing rights and stale timestamp.
- [ ] Demonstrate RED.
- [ ] Implement pure signer verifier, source rights allowlist and short TTL; NEVER collect from PrizePicks.
- [ ] Demonstrate GREEN and commit.

### Task 3: Aligned scenario slip and payout diagnostics

**Files:** Create artifacts/wow-engine/v17/niche_prop_joint_shadow.py; test artifacts/wow-engine/v17/test_niche_prop_joint_shadow.py

**Interfaces:** evaluate_joint_shadow(offer_receipts, lineup_receipt, scenario_draws, ... ) -> shadow hit-count distribution, payout diagnostics, non-certification.

- [ ] Tests for aligned 5/5, 4/5, 3/5 and dependence, missing outcomes, PUSH, unsigned quote and wrong offer bindings.
- [ ] Demonstrate RED.
- [ ] Implement strict cross-lineup binding, pregame receipt/signature validation and aligned-sample economics.
- [ ] Demonstrate GREEN, commit.

### Task 4: Integration and verification

**Files:** docs/wow/research/niche-prop-shadow-acceptance-2026-11-23.md; focused tests.

- [ ] Document actual historical-data and provider authorization prerequisites; no unearned certification.
- [ ] Run all new plus existing niche + preflight regressions in GitHub exact-head Actions.
- [ ] Obtain independent code review; if required checks do not pass, leave PR open.
- [ ] No automatic merge/deployment without independent release acceptance.
