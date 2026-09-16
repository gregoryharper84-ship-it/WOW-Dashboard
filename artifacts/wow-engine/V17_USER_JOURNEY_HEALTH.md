# WOW V17 user-journey health

Updated: 2026-09-16

Status: **FAIL — NO_END_TO_END_GOVERNED_PROP_RESULT**

This status is intentionally stricter than component health. A healthy backend, verified live editor, green repository CI, successful Scout discovery, mounted route, or available fitted model does not by itself prove that the product is usable from ChatGPT.

## Golden user journey

The production acceptance prompt is:

```text
Use full model and provide me the best props across all sports.
```

The journey is healthy only when the live production path proves all of the following in sequence:

1. The request originates from the production `WOW_BETTING_ENGINE` ChatGPT host.
2. The live host invokes `scoreWowPickRequest` on `POST /score-pick-request` at the production Render Action origin. `scoreWowV17PickRequest` is a compatibility alias, not the canonical live operation name.
3. Current pregame prop inventory is acquired. A genuinely empty slate is valid only with explicit source evidence; silent prop-source degradation is not healthy.
4. Each prop is routed to exactly one controlling WOW prop specialist.
5. Supported rows return the governed numeric probability package required by the lane, including calibration/lower bound where required.
6. Unsupported or failed rows preserve their exact typed governed failure. Sportsbook implied probability, external projections, hit rates, or generic reasoning may not be relabeled as governed model probability.
7. `V17_TERMINAL_REDUCER` remains the sole global terminal authority and `can_execute=false` remains invariant.
8. Rankable rows are returned to ChatGPT and displayed from actual governed receipts. Discovery-only candidates do not satisfy the journey.

## Health states

```text
PASS
  = a production ChatGPT canary completed the full path and returned at least one valid governed row, or a legitimate explicit empty-slate result with source evidence.

FAIL_ACTION_INVOCATION
  = the live GPT did not invoke the live pick-request Action for the user request.

FAIL_PROP_ACQUISITION
  = current props were expected/available but zero prop rows reached governed routing without an explicit typed source blocker.

FAIL_PERSISTENCE
  = Scout/research persistence or board materialization failed, or a workflow reached success by skipping the persistence acceptance path.

FAIL_GOVERNED_SCORING
  = rows reached a controlling model but no valid governed result or exact typed failure package returned.

FAIL_RESPONSE_HANDOFF
  = governed rows completed but the live GPT did not receive/display the canonical result.
```

## False-green rule

`USER_JOURNEY_HEALTH=PASS` must never be inferred from any of the following alone:

- workflow conclusion `success`;
- skipped verification steps;
- repository CI success;
- backend health endpoint success;
- route-mounted or HTTP-200 evidence without the golden user request;
- live editor save/reload success;
- model-capability registry presence;
- Scout discovery success;
- locally reconstructed model output;
- manual research or external sportsbook/analytics projections.

The latest full-path production ChatGPT canary receipt is the authority for this status.

## Current recovery state

The 2026-09-16 editor reconciliation independently established `LIVE_GPT_EDITOR_SYNC=VERIFIED`, the production backend is V17 active, and `/score-pick-request` is mounted. Those are prerequisites, not proof of the golden journey.

Until the golden production prompt crosses the live Action boundary and returns the governed result contract:

```text
USER_JOURNEY_HEALTH = FAIL
REASON = NO_END_TO_END_GOVERNED_PROP_RESULT
LIVE_GPT_EDITOR_SYNC = VERIFIED 2026-09-16
can_execute = false
```
