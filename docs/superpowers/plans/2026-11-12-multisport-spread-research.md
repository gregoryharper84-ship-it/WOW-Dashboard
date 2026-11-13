# Multisport Spread Research Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a read-only full-slate cross-sport exact-line spread/puck/run/handicap research card and independent settlement diagnostic without model promotion.

**Architecture:** Add one isolated `v17.llp_spread_research_card` module alongside the existing fitted `spread_card_admission`. Source-authenticated manifest, pregame pick, exact quote and independent final records are inputs; no network or database writes. Keep the governing model unchanged and add comprehensive synthetic tests and a concise integration contract.

**Tech Stack:** Python 3, stdlib, pytest, GitHub Actions.

**Spec:** docs/superpowers/specs/2026-11-12-multisport-spread-research-design.md

## Global Constraints

- Owner LLP_TEAM_BETTING_ENGINE / #2076; never duplicate the fitted spread scorer or #2547 winner retro.
- No market/ML/narrative probability substitution; all outputs RESEARCH_ONLY, non-rankable, `probability_publishable=false`.
- `V17_TERMINAL_REDUCER` remains sole terminal authority; `can_execute=false`, no wagers, orders or automatic Pick'em submission.
- Canonical event and exact line/side/market/settlement identity, all original cohort rows, pregame timestamps and independent settlement are mandatory for claims.
- No paid providers, migrations, protected CI changes or direct production deployment.

## Review Focus

1. Same event ID paired with different opponents or start times must hold both and suppress completeness.
2. Exact line missing, wrong sign, wrong market family/period, late or stale quote must never be replaced from ML/odds.
3. Quarter soccer Asian handicap must return half-win and half-loss correctly; pushes cannot be marked wins.
4. Tennis games-versus-sets and retirement must not silently grade from match-winner score.
5. Missing or unauthenticated final must remain pending/held; no partial headline accuracy or fitted-model credit.

---

### Task 1: Synthetic adversarial tests (RED)

**Files:** Create `artifacts/wow-engine/tests/test_v17_llp_spread_research_card.py`.

**Interfaces:** `audit_spread_research_card(slate, picks, finals=(), *, official_slate_verified=False, official_slate_receipt_id=None) -> dict`.

- [ ] Write tests for complete scheduled card and separate final, pending/held, quote age/identity, duplicates, decimal line, pushes, soccer quarter line, tennis games/sets and retirement, missing authentication, unsupported markets.
- [ ] Run `PYTHONPATH=artifacts/wow-engine pytest -q artifacts/wow-engine/tests/test_v17_llp_spread_research_card.py`; Expected: fail because new module is missing.

### Task 2: Read-only implementation (GREEN)

**Files:** Create `artifacts/wow-engine/v17/llp_spread_research_card.py`.

**Interfaces:** Consume Task 1's exact API. Return `scheduled_count`, `playable_count`, `card_complete`, `settlement_complete`, `rows`, `by_sport`, distinct held/pending/graded/void outcomes and `headline_full_win_rate` only for complete, no half outcomes.

- [ ] Implement pure stdlib validation and exact line settlement; use source timestamps, strict booleans and bounded numerical parsing; no writer or model call.
- [ ] Run focused tests; Expected: all pass.
- [ ] Run existing `artifacts/wow-engine/tests/test_v17_spread_card_admission.py` in addition; Expected: no regression.
- [ ] Commit.

### Task 3: Integration contract and final verification

**Files:** Create `docs/wow/research/2026-11-12-multisport-spread-audit-contract.md`; no model code changes.

- [ ] Document inputs, use through LLP downstream Daily/card presentation, unsupported capability gaps, and explicit non-authoritative status.
- [ ] Run compilation/focused tests, then repository-wide feasible tests; compare expected outputs against fixture and original governance invariants.
- [ ] Run a whole-branch review, flag any missing independent validation, open a protected PR and cross-link #2076/#1563. Do not merge without exact-head gates and independent acceptance.
