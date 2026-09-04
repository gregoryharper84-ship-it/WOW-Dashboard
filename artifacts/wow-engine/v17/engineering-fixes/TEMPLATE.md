# FIX-YYYY-MM-DD-NNN — Fix Title

## Status

PROPOSED | IN_PROGRESS | TESTING | DEPLOYED | VERIFIED | ROLLED_BACK | CLOSED

## Linked Postmortem(s)

- PM-YYYY-MM-DD-NNN

## Problem Statement

Concise description of the defect or failure being repaired.

## Root Cause Addressed

State the confirmed root cause this change addresses. If root cause is not confirmed, mark the fix as experimental and do not present it as permanent remediation.

## Scope

- Components:
- Files:
- Routes/endpoints:
- Models/lanes:
- Persistence/schema impact:
- GPT editor/action impact:

## Change

Describe the implementation precisely enough to audit later.

## Governance Invariants

Confirm each applicable invariant:

- [ ] `can_execute=false` remains unchanged.
- [ ] No live wager/order execution path was introduced.
- [ ] Controlling specialist ownership remains intact.
- [ ] Typed model/scorer/completion failures remain preserved.
- [ ] No sportsbook implied probability or external projection is relabeled as governed model probability.
- [ ] Probability/calibration fields are not modified solely to satisfy card/portfolio concerns.
- [ ] No secret or service-role credential is exposed.

## Tests

### Unit

- 

### Contract

- 

### Regression

- 

### Acceptance

- 

## Deployment

- Branch:
- Commit SHA:
- PR:
- Deploy ID:
- Environment:
- Deployed at:

## Production Verification

- Health/governance check:
- Reproduction request/run ID:
- HTTP result:
- Terminal status:
- `scoring_attempted`:
- Expected result:
- Observed result:

## Rollback

- Rollback trigger:
- Rollback procedure:
- Last known good commit/deploy:

## Result

State whether the issue is fixed, partially fixed, blocked, or rolled back. Do not claim FIXED without verification evidence.

## Follow-up

List remaining cleanup, monitoring, test coverage, or architecture work.

---

V17 terminal authority remains `V17_TERMINAL_REDUCER`; no engineering fix may override it.
