# Seven-Sport Joint-Score Settlement Shadow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fail-closed sport settlement checks to the existing V17 Class C joint-game-state shadow, without a competing fitted model.

**Architecture:** Pure stdlib adapter consumes the existing joint-game-state projector output, validates explicit sport-period-tie conventions and exact-line three-way probabilities, and returns permanently research-only components.

**Tech Stack:** Python 3, stdlib, pytest, GitHub hosted CI.
**Spec:** `docs/superpowers/specs/2026-11-13-seven-sport-settlement-shadow-design.md`

## Global Constraints
- Reuse existing PR #2639 and #2076; no parallel prediction authority or controller.
- Preserve `LLP_TEAM_BETTING_ENGINE`, `V17_TERMINAL_REDUCER`, `can_execute=false`; no wagers or automatic entries.
- Soccer quarter lines require underlying draws and remain held.
- Claimed fitted-source digest is not independent source attestation.
- No paid provider, registry changes, deployment, or protection bypass.

## Review Focus
1. NFL/NCAAF tie mass cannot disappear through a no-ties settlement rule.
2. NHL full-game winner must not retain unresolved ties or regulation-only basis.
3. Soccer quarter-line settlement cannot be guessed from aggregate more/exact/less.
4. Tennis game totals cannot determine match-winner probability.
5. NaN, overflow, half-point push mass, invalid timestamps and uncertified data must fail closed.

---

### Task 1: Write RED tests
**Files:** `artifacts/wow-engine/tests/test_v17_seven_sport_settlement_shadow.py`
**Interface:** `settle_seven_sport_shadow(sport: str, settlement_basis: str, projection: Mapping, score_unit: str|None=None) -> dict`

- [x] Write seven-sport, tie/push, tennis, soccer quarter, numeric/provenance and governance adversarial tests.
- [x] Run `PYTHONPATH=artifacts/wow-engine python -m pytest -q artifacts/wow-engine/tests/test_v17_seven_sport_settlement_shadow.py` before implementation: expected/observed `ModuleNotFoundError`.

### Task 2: Add GREEN implementation
**Files:** `artifacts/wow-engine/v17/seven_sport_settlement_shadow.py`
- [x] Implement explicit rules and probability-triplet validation; no rank/publish/certify output.
- [x] Run isolated focused tests locally: `29 passed` (local copy).
- [ ] Verify exact-head hosted tests against existing repository code and nearby suites, including unchanged NFL Pick'em.

### Task 3: Independent integration review
- [x] Document design, safety boundaries, and NCAAF/NCAAB fitted follow-on.
- [x] Append to existing isolated PR #2639 branch.
- [ ] Verify CI and exact SHA; request independent QA/SIRT/Release approval; no merge/deploy without authorized gates.
