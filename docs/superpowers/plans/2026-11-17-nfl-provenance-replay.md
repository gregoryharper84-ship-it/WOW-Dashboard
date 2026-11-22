# NFL Provenance-First Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for incremental implementation and tests. This plan was carried out inline at the owner's instruction to proceed.

**Goal:** Produce reproducible, leakage-safe retrospective NFL comparison without presenting backfilled features as prospective evidence.

**Architecture:** Standalone pure Python trainer/evaluator under `artifacts/wow-engine/v17/experiments/nfl_challenger_replay/`. Reuse DB exports and existing fitted-model owners; no service routes or migrations.

**Tech Stack:** Python 3.13 standard library, pytest, GitHub protected PR.

**Spec:** `docs/superpowers/specs/2026-11-17-nfl-provenance-replay-design.md`.

## Global Constraints

`CAN_EXECUTE=False`; `PROBABILITY_PUBLISHABLE=False`; never self-promote or mutate `V17_TERMINAL_REDUCER`. No claims of authenticated pregame receipts without an independently verified freeze, no market prices as model probabilities, no betting actions.

## Review Focus

- UTC overnight kickoff versus NFL local gameday: allow UTC next-day starts without misclassifying all as backfill.
- Retrospective `locked_at` after game: do not report prospective certification.
- Mismatched incumbent kickoff / duplicate event ID: reject or exclude before scoring.
- Extreme but finite inputs: fail closed on floating-point overflow.
- Feature-order drift/postgame target features: reject rather than silently train.

### Task 1: Provenance classification and chronology

**Files:** Create `replay.py`, `test_replay.py`.

**Interfaces:** `classify_feature_receipt(row) -> str`.

- [x] Write negative tests for backfilled, stale, missing-hash, overnight UTC and invalid prior dates.
- [x] Run tests to observe failures, implement input classification, rerun to green.

### Task 2: Chronological reproducible research fit

**Files:** Modify `replay.py`, `test_replay.py`.

**Interfaces:** `evaluate_chronological_replay(rows,incumbent,*,fit_end_season,calibration_season,holdout_season) -> dict`.

- [x] Test train/calibration/holdout isolation, arbitrary feature leakage, numerical extremes and identical event pairing.
- [x] Fit fixed logistic with training-only normalization and calibration-only Platt adjustment; grade holdout once.
- [x] Add deterministic week-clustered paired Brier uncertainty, source checksum and research-only invariants.

### Task 3: Audit-ready command and research receipt

**Files:** Create `run_replay.py`, `README.md`; create chronological research report.

**Interfaces:** `run_from_file(source: Path, output: Path|None=None, ...) -> dict`.

- [x] Test a valid JSON export versus an invalid dataset, command's no-write-on-invalid behavior.
- [x] Implement JSON-only offline runner and README with export/provenance guidance.
- [ ] Verify same exact code on an authenticated prospective data cohort; do not consider this task a fitted-model certification.
