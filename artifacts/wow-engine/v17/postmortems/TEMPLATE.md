# PM-YYYY-MM-DD-NNN — Incident Title

## Status

OPEN | MITIGATED | FIXED | VERIFIED | CLOSED

## Summary

One-paragraph description of what failed or behaved unexpectedly.

## Impact

- User-visible impact:
- Model/probability impact:
- Governance impact:
- Data/persistence impact:

## Detection

- Detected at:
- Detected by:
- First known bad run/request:
- Last known good run/request:

## Evidence

Record immutable evidence only: request/run IDs, HTTP status, terminal status, logs, model/scorer status, relevant commit/deploy identifiers, screenshots, or source artifacts.

## Root Cause

Describe the actual failure mechanism. Separate confirmed cause from contributing factors and hypotheses.

## V17 Classification

- BACKEND_RUNTIME:
- MODEL_CAPABILITY:
- REPOSITORY_GOVERNANCE:
- LIVE_GPT_EDITOR_SYNC:
- Terminal status:
- `scoring_attempted`:

## Controlling Lane / Specialist

Identify the exact controlling specialist or route. Scout/Research evidence must not be substituted for model output.

## Failure Semantics

Preserve the backend's typed failure. If the selected model was invoked and failed, timed out, or produced an invalid package, do not relabel the result as `MODEL_UNAVAILABLE`.

## Remediation

- Engineering fix ID(s):
- Temporary mitigation:
- Permanent fix:

## Verification

- Regression test(s):
- Acceptance test(s):
- Production verification:
- Verified commit/deploy:

## Prevention / Follow-up

Document monitoring, test coverage, contract validation, or architectural changes needed to prevent recurrence.

## Closure

- Closed at:
- Closed by:
- Final status:
- Linked engineering fix(es):

---

V17 safety invariant: `can_execute=false`. This record cannot authorize, route, modify, approve, or cancel a wager/order.
