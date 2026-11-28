# FIX-2026-11-28-001 — Feed the multi-sport live canary from acquired pregame rows

- status: VERIFIED_CLOSED
- linked_postmortem: PM-2026-11-28-001
- implementation_owner: ENGINEERING_AGENT
- risk: R1
- pull_request: 2904
- merge_commit: d1458c3173587cf6bac77e13819a5c3491c80a95
- review_status: PASS
- qa_status: PASS
- release_status: NOT_APPLICABLE
- created_utc: 2026-11-28T03:40:23Z
- updated_utc: 2026-11-28T04:40:27Z
- closed_utc: 2026-11-28T04:40:27Z
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Root Cause Being Repaired

The canary had no acquisition adapter to the governed pregame producers and no
bundle handoff between its eligibility check and live step, so it could only
report the stale static `CONFIGS` slate (exit 78).

## Acceptance Criteria

- Canary acquires real pregame rows from the governed MLB/NFL/WNBA producers behind `WOW_CANARY_ACQUIRE_FIXTURES` (default off).
- `source_type` stays `AUTONOMOUS_DISCOVERY`; `can_execute=false`; the no-write sink suppresses every snapshot write.
- Four-rows-per-sport contract, `require_publishable=(sport == "MLB")` and the interactive latency ceiling unchanged.
- Check step persists a validated bundle and the live step consumes it, so one dispatch acquires once.
- Fail closed: exit 75 for acquisition or bundle corruption, 78 for an empty slate; an upstream-refused date (404/406) is an empty slate while transport failures stay typed.
- Static mode unchanged; exact-head CI plus independent QA/Release.

## Allowed Files

- `v17/pregame_canary_fixtures.py`
- `v17/prop_snapshot_sink.py`
- `v17/multisport_prop_live_canary.py`
- `v17/nfl_prop_evidence_control_plane.py`
- `v17/mlb_prop_evidence_control_plane.py`
- `v17/test_v17_pregame_canary_fixtures.py`
- `v17/test_mlb_prop_evidence_control_plane.py`
- `.github/workflows/wow-v17-nfl-prop-live-canary.yml`

## Implementation

PR #2904 at exact head `a825e6391b6d9b935f1bc5a3f81de63f35faecc8`, merged as
`d1458c3173587cf6bac77e13819a5c3491c80a95`. Adds the injectable acquisition
adapter, the `NO_WRITE` sink, the bundle emit/consume handoff and the workflow
wiring; no static-fixture behavior changed.

## Regression Test

62 affected tests passed (canary, acquisition adapter, MLB and NFL
control-plane suites), covering empty-slate exit 78, corrupt-bundle exit 75 and
404/406 unserved-date classification.

## Independent Review

PASS — exact-head Independent QA App and Release Authority checks both
succeeded before merge.

## QA

PASS — live eligibility, live canary and stress steps succeeded in dispatched
run 46106105938 on the merge commit.

## Validation Gates

- targeted reproduction
- relevant unit/integration tests
- neighboring failure-path/contract tests
- `can_execute=false` and dry-run invariants
- V17_TERMINAL_REDUCER authority
- required GitHub checks

## Deployment

Not applicable: the repair is canary/workflow code, not a runtime service
change. No Render deployment is produced by this fix.

## Production Verification

Fresh dispatched run 46106105938 on merge commit `d1458c317` completed success
with the live canary and stress steps executed (NFL-only coverage; MLB/WNBA out
of season). No new P0/P1 issue appeared. Push run 46106101546 also succeeded.
Release status is `NOT_APPLICABLE`: no runtime deployment exists for this
CI-only repair, so no `PRODUCTION_VERIFIED` claim is made.

## Rollback

Revert the merge commit for FIX-2026-11-28-001.
