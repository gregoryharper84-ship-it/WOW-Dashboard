# PM-2026-11-28-001 — Multi-sport live canary reports stale static fixtures instead of acquired pregame rows

- status: MERGED_PENDING_DEPLOY
- severity: P1
- domain: V17 multi-sport prop live canary / pregame evidence acquisition
- team_contract_version: "1.0"
- primary_subsystem: DEPLOYMENT_RUNTIME
- secondary_subsystems: []
- workflow_stage: REPORTER_CLOSURE
- current_owner: REPORTER_AGENT
- reproduction_status: REPRODUCED
- root_cause_status: CONFIRMED
- risk_class: R1
- protected_contracts_touched: []
- created_utc: 2026-11-28T03:40:23Z
- updated_utc: 2026-11-28T04:14:00Z
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Impact

Every dispatch of `wow-v17-nfl-prop-live-canary` exercised only the hardcoded
`CONFIGS` slate (NFL `2026_05_TB_DAL`, MLB `MLB:849822`, WNBA
`WNBA:1042600202`), all October games. The check step reported
`LIVE_CANARY_NOT_RUN_STALE_STATIC_FIXTURES` (exit 78), so live eligibility could
never be claimed and an active in-season slate was never exercised through the
canary. No probability math, calibration, gate or threshold was affected.

## Evidence

- Pre-repair `check-live-fixtures` with acquisition unset returned
  `LIVE_CANARY_NOT_RUN_STALE_STATIC_FIXTURES`, exit 78, against the static
  `CONFIGS` (12 rows, latest 2026-10-09T00:15Z).
- `future_fixture_sports()` requires all four rows per sport to be in the
  future, so a stale slate can never be reported as live coverage.
- No acquisition provider was wired into the canary at base main
  `6cb857732cbc388b1952b604f73475d03b5addc9`.
- At repair head `a825e6391b6d9b935f1bc5a3f81de63f35faecc8` with
  `WOW_CANARY_ACQUIRE_FIXTURES=1`, NFL acquired 1,210 candidates for
  2026-11-29 and selected 4 rows; MLB/WNBA returned 0 candidates on all three
  local dates (out of season). 5 snapshot upserts were suppressed by the
  `NO_WRITE` sink. 62 affected tests passed.

## Root Cause

The canary only knew the static `CONFIGS` fixtures. It had no adapter to the
governed MLB/NFL/WNBA pregame evidence producers and no bundle handoff between
its eligibility check and live step, so there was no path to real rows even
during an active slate. Root cause status: CONFIRMED, confidence HIGH.

## Repair

PR #2904 (exact head `a825e6391b6d9b935f1bc5a3f81de63f35faecc8`) added an
injectable acquisition adapter over the existing governed producers, an
explicit `WOW_CANARY_ACQUIRE_FIXTURES` gate (default off), a validated bundle
written by the check step and consumed by the live step, and a `NO_WRITE`
snapshot sink. Fail-closed statuses are preserved: exit 75 for acquisition or
bundle corruption, 78 for an empty slate, and 404/406 from the MLB schedule API
classified as an unserved date. `source_type` stays `AUTONOMOUS_DISCOVERY`,
`can_execute=false`, and the four-rows-per-sport contract with
`require_publishable=(sport == "MLB")` is unchanged.

## Verification

- PR #2904 merged to protected `main` at 2026-11-28T04:07:59Z; merge commit
  `d1458c3173587cf6bac77e13819a5c3491c80a95`.
- Manual `workflow_dispatch` run
  [46106105938](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/46106105938)
  (run #16) completed **success** in 2m40s. Every step passed, including
  `Check future-dated fixture eligibility` and `Run future-dated governed live
  canaries and stress`; the live step only executes when eligibility is true,
  so it confirms acquisition produced eligible future rows.
- Merge-triggered `push` run
  [46106101546](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/46106101546)
  (run #15) also completed **success** on the same commit.
- Coverage was NFL-only because MLB and WNBA are out of season on 2026-11-28.

## Governance Classification

R1: bounded canary/acquisition wiring only. No probability, calibration,
terminal semantics, gate, threshold, auth, secrets or database schema change;
`can_execute=false` throughout. Exact-head CI plus independent QA/Release
governance checks passed before merge.

## Linked Engineering Fixes

- FIX-2026-11-28-001

## Release Disposition

Release status: `NOT_APPLICABLE`. This is canary/workflow-only code with no
runtime deploy artifact; the dispatched run 46106105938 on merge commit
`d1458c317` is the acceptance evidence, alongside push run 46106101546.
`PRODUCTION_VERIFIED` is not claimed because no `deployed_commit` or
`deployment_id` exists for a CI-only fix.

## Closure Criteria

Merged and verified through a dispatched run on the merge commit, with
`release.status=NOT_APPLICABLE` recorded. Only the Reporter `FIXED_VERIFIED`
receipt remains before `VERIFIED_CLOSED`; that receipt is deliberately not
recorded here.
