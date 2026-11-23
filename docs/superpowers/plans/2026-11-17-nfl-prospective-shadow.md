# NFL Prospective Shadow Capture and Independent Grade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task; test RED before GREEN, record exact SHA evidence.

**Goal:** Automate first genuine immutable NFL challenger pregame forecasts and postgame two-provider research grades without altering sporting-model publication.

**Architecture:** Reuse canonical NFL P2 prior builder and existing `wow_nfl_ml_challenger_forward_shadow` rather than new prediction infrastructure. A small pure module fits a 2021–24 challenger/2025 calibrator, a separate runner captures or grades on protected-main GitHub Actions, and a SQL trigger enforces one-way immutable lifecycle.

**Tech Stack:** Python 3.11 stdlib + existing Supabase Python SDK, PostgreSQL PL/pgSQL, GitHub Actions, pytest.

**Spec:** `docs/superpowers/specs/2026-11-17-nfl-prospective-shadow-design.md`

## Global Constraints

- No code may change fitted champion, LLP team-event serving route, market actions, odds, execution, or terminal authority.
- `can_execute=false`; `probability_publishable=false`; `V17_TERMINAL_REDUCER` exclusive authority.
- One pregame freeze and one postgame grade per challenger+official event; never update a pregame probability.
- Two independent providers must reconcile before nonofficial outcome grading.

## Review Focus

1. Pre-kickoff source timestamp and DB commit clock vs forged caller clocks → test PostgreSQL overwrite + reject postkickoff.
2. Source hash/fixture mismatch or stale snapshots → test exact bytes and fail-closed checks.
3. Future 2026 labels leaking into fitted parameters → test fixed fit/cal windows and identical artifact SHA.
4. Duplicate or contradictory game results → test duplicate event lock and provider disagreement.
5. Reusing postkickoff or wrong-kickoff incumbent forecasts → exact-event/time selection tests.

### Task 1 — Pure forward fitted candidate and source validation

**Files:** `artifacts/wow-engine/v17/experiments/nfl_prospective/prospective.py`, `test_prospective.py`.

- [x] Write failing tests for fixed splits, chronology, exact fixture DST, selected probability, and no execution.
- [x] Verify failing import before implementing.
- [x] Implement bounded, reproducible L2 logistic and Platt calibrator using only 2021–24 + 2025.
- [x] Verify local tests; preserve artifact hash and uncertified lower bounds.

### Task 2 — First-only capture, independent grading

**Files:** `runner.py`, `test_runner.py` in same experiment.

- [x] Write failing import tests for ESPN consensus, source timestamps, incumbent receipts and no-event semantics.
- [x] Implement canonical schedule SHA validation, prior-only feature builder reuse, protected-only insert and two-provider grade update.
- [x] Run local tests, preserve typed no-capture statuses.

### Task 3 — Physical immutability and operational wiring

**Files:** `artifacts/wow-engine/migrations/20261117_nfl_prospective_immutable_freeze.sql`, `test_release_contract.py`, `.github/workflows/wow-v17-nfl-prospective-shadow.yml`.

- [x] Write failing structural checks for server clock, one-way transition and workflow main-only guard.
- [x] Implement minimal trigger and twice-daily research workflow.
- [ ] Run disposable PostgreSQL insert/rewrite/delete/grade adversarial integration test, plus full adjacent suites.

### Task 4 — Protected integration and live receipt

- [ ] Create research branch with source/test/spec/plan, open PR under existing #2076.
- [ ] Verify all exact-head native required regression checks, review, and merge only if allowed.
- [ ] Apply approved SQL migration to linked Supabase; independently verify installed trigger and permissions.
- [ ] Verify workflow presence, monitor first scheduled/authorized run, confirm true DB pregame row; never claim successful live capture without readback.

## Completion boundary

Merge of code does **not** mean first real pregame receipt was captured. Model lift and promotion require a future matched cohort and scientific acceptance; any missing evidence remains visible as `NOT_VERIFIED`.
