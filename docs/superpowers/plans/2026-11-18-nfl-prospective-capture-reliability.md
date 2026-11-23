# NFL Prospective Capture Reliability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure upcoming NFL fixtures are eligible early and an entirely evidence-held prospective capture can never report successful execution.

**Architecture:** Reuse `v17/experiments/nfl_prospective/runner.py`, the single existing GitHub scheduled workflow and immutable Supabase ledger. Modify only the lookahead, schedule minutes, capture accounting and CI receipt gate; no new model or scheduler.

**Tech Stack:** Python 3.11, pytest, GitHub Actions, existing disposable PostgreSQL 16 CI.

**Spec:** `docs/superpowers/specs/2026-11-18-nfl-prospective-capture-reliability.md`

## Global constraints

- Reuse #2076 and existing PRs #2706/#2707 without duplicating blockers.
- No weakening kickoff/source-hash/identity guards or immutable DB trigger.
- Credentials may appear only in the protected-main scheduled/manual job.
- No wagers, automatic certification or serving updates; `can_execute=false`; `V17_TERMINAL_REDUCER` remains controlling.

## Review focus

- First Wednesday 12:17 UTC sweep must see Thursday 2026-11-19 20:15 ET Colts@Texans.
- Past kickoff, already-scored events and events beyond 48 hours must remain excluded.
- An empty window and an already-frozen event must remain idempotent success, not false incidents.
- Eligible-but-all-held and inconsistent counters must cause typed failure with preserved artifact.
- Scheduled jobs must not run on PR, and no new unscoped backend privileges.

### Task 1: Capture time eligibility

**Files:** `artifacts/wow-engine/v17/experiments/nfl_prospective/test_capture_reliability.py`, `runner.py`.

- [x] Write fixture regression for Wednesday 12:17 UTC and strict 48-hour cutoff.
- [x] Run test first: 36-hour runner fails.
- [x] Set `MAX_LOOKAHEAD_HOURS=48`.
- [x] Run regression green.

### Task 2: Honest evidence status and workflow gate

**Files:** `test_capture_reliability.py`, `runner.py`, `.github/workflows/wow-v17-nfl-prospective-shadow.yml`.

- [x] Write failing tests for all-held and impossible row accounting.
- [x] Implement typed status and `run_ok` without altering model outputs.
- [x] Move cron to minute 17; add always-run JSON verification after grading.
- [x] Add new tests to hosted CI pytest list; preserve existing artifact upload.
- [x] Verify local scoped 26 passes/1 PostgreSQL skip on archived source; compileall green.

### Task 3: Protected integration and live acceptance

- [ ] Open ordinary isolated GitHub PR and verify exact changed paths.
- [ ] Run current-branch CI with disposable PostgreSQL, existing adjacent tests, native required checks and reviewer findings; fix any failures with red-green tests.
- [ ] Protected merge only under current repository governance; verify exact main merge SHA.
- [ ] Inspect next scheduled/manual `capture.json` receipt, latest schedule source, and actual immutable DB rows; do not claim success from cron configuration alone.
- [ ] Independently grade settled games, check future pregame cutoff and receipt linkage; only then assess certification.
