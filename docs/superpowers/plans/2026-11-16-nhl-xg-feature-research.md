# NHL 5v5 xG feature research implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a reusable, deterministic, time-correct NHL five-on-five expected-goals research feature contract without changing production model predictions.

**Architecture:** Standalone pure Python module with typed historical records and one reconstruction function. All data stays in memory; callers own sources and independent rights review. Tests use only synthetic fixtures.

**Tech Stack:** Python 3, dataclasses, pytest and standard library.

**Spec:** `docs/superpowers/specs/2026-11-16-nhl-xg-feature-research-design.md`

## Global constraints
- Do not touch scorer, incumbent model schema, PR #2637/#2655/#2639 branches, production DB, or deployment.
- `can_execute=false`, `V17_TERMINAL_REDUCER` sole terminal authority.
- No external rights/capture/verification claims and no calibrated probabilities.

## Review focus
- A game captured after freeze must never become a pregame feature.
- An official event ID reused on another fixture must fail closed.
- Historical all-strength xG must not pass as 5v5 xG.
- Zero-sample or one-team coverage must hold rather than default to zero.
- Fake source hashes or self-asserted rights must not certify the feature.

---

### Task 1: Time-correct 5v5 xG pure feature contract
**Files:**
- Create: `artifacts/wow-engine/v17/nhl_xg_point_in_time.py`
- Test: `artifacts/wow-engine/test_v17_nhl_xg_point_in_time.py`

**Interfaces:**
- Consumes: `Sequence[NHLXGRecord]` and explicit event identity/cutoff; no network.
- Produces: `reconstruct_5v5_xg(*, target_event_id, target_start_utc, as_of_utc, home_team, away_team, prior_games, window=10, min_prior_games=5) -> dict` with typed hold or research-only features.

- [x] **Step 1: Write failing pytest contracts** for right-way deltas, order invariance, 5v5, cutoff/rights/identity negative paths.
- [x] **Step 2: Run tests** `PYTHONPATH=artifacts/wow-engine pytest -q artifacts/wow-engine/test_v17_nhl_xg_point_in_time.py` Expected: collection fails on missing module.
- [x] **Step 3: Implement minimal pure logic** in `v17/nhl_xg_point_in_time.py` with typed holds and immutable research-only authority.
- [x] **Step 4: Run tests** with same pytest command Expected: all 23 cases passed locally.
- [ ] **Step 5: Commit** only the design, plan, module and test files on an isolated review branch; open draft PR under #1952 and link the existing owner issues.
