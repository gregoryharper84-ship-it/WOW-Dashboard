# WOW V17 Restoration Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore stable, genuine multi-sport V17 scoring before model improvements.

**Architecture:** Reuse existing canonical scorer, data feeds, fitted specialist, calibration, immutable receipt, reducer, and hourly recovery. Isolate each sport owner's source changes; first fix false-green restoration telemetry in existing workflow, not model gates.

**Tech Stack:** Python 3.11, pytest, GitHub Actions, existing V17 engine, Supabase ledgers, Render.

**Spec:** `docs/superpowers/specs/2026-11-18-wow-v17-restoration-first-design.md`

## Global constraints
No betting execution, no implied-odds or generic probability substitute, no model weight/certification/QA gate edits, no new paid provider or duplicate scheduler. `can_execute=false`; `V17_TERMINAL_REDUCER` only. Separate `BACKEND_RUNTIME`, `MODEL_CAPABILITY`, `REPOSITORY_GOVERNANCE`, `LIVE_GPT_EDITOR_SYNC`.

## Review focus
All-held but balanced must not signal restored; scored rows are not certified by monitoring; empty inventory does not prove no games; malformed/negative/boolean counters fail closed; changes in terminal authority or execution lock fail closed.

### Task 1 — Evidence-only signal (this PR)
**Files:** create `artifacts/wow-engine/v17/phase1_restoration_signal.py`, `artifacts/wow-engine/tests/test_v17_phase1_restoration_signal.py`; modify existing `.github/workflows/wow-v17-team-event-recoverable-refresh.yml`.

**Interface:** `classify_refresh_run(receipt: Mapping[str, Any]) -> dict[str, Any]`. Consumes real Daily response, returns `ZERO_COMPLETED_ROWS`, `PARTIAL_SCORING`, `SCORING_OBSERVED_NOT_ACCEPTED`, or `NO_ROWS_OBSERVED`, with `restoration_status=WORKING_NOT_COMPLETE`.

- [x] RED: focused pytest collection fails when the module does not exist; workflow integration test fails before wiring.
- [x] GREEN: implement fail-closed counter/terminal validation, check `python -m pytest -q tests/test_v17_phase1_restoration_signal.py` locally (9 core tests).
- [x] Wire existing hourly refresh via read-only checkout and summary, no extra job/schedule; add test to existing verify job.
- [ ] Run exact-head existing GitHub regression + required native CI, independent QA review.
- [ ] Protected merge/deploy and genuine schedule response readback, only when authorized.

### Task 2 — Sport-specific restore (separate isolated PRs, do not edit other owner branches)
- [ ] NBA #1950: verify real source replay/clock and injury/rotation/rest; RED tests for changed-starter/timestamp; repair canonical current scorer and fitted artifact/calibrator connection without bypassing attestation.
- [ ] NHL #1952: reconcile source-pinned historical cohort and actual goalie/stats/injury/rest receipt; RED tests on source drift and goalie changes; restore current full-game fitted scoring.
- [ ] NCAAF #1951 and NCAAB #1644: current canonical identity, exact sport serving fitted artifact and calibration; keep typed unsupported/identity holds.
- [ ] NFL #1896: stabilize existing numerical serving, publication and canonical Action receipt; defer challenger-model improvements.
- [ ] Confirm exact-once candidate reconciliation for every scoped sport and no generic substitute.

### Task 3 — Existing supported market delivery
- [ ] Props #487: exact player/stat/period/line/direction with Action, immutable forecast and calibrated lower bound. Distinct held/unsupported stat routes remain visible.
- [ ] Spreads #987: only genuine signed-line fitted cover model, correct push/settlement, canonical Action; no ML-derived covers.
- [ ] Independent QA verifies deployed Render web+worker SHA parity, actual Custom GPT Action/editor sync and terminal readback.

### Task 4 — Product acceptance, then separate Phase 2
- [ ] Three genuine scoring cycles across two local dates for currently active supported events; no invented event or certifications.
- [ ] SIRT grades exact spot checks, data freshness and incomplete-lane blockers; Independent Verification signs user-visible acceptance.
- [ ] Phase 1 closes only on evidence, not CI or issue comments. Then allocate opponent/game-script/NHL loss-pattern and calibration research to Phase 2.
