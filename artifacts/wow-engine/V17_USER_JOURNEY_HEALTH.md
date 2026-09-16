# WOW V17 user-journey health

Updated: 2026-09-16

Status: **FAIL — NO_END_TO_END_GOVERNED_PROP_RESULT**

This status is intentionally stricter than component health. A green workflow, green CI suite, healthy backend endpoint, successful Scout discovery, or available fitted model does not by itself make the product usable from ChatGPT.

## Golden user journey

The production acceptance prompt is:

```text
Use full model and provide me the best props across all sports.
```

The journey is healthy only when the live production path proves all of the following in sequence:

1. The request originates from the production `WOW_BETTING_ENGINE` ChatGPT host.
2. The live host exposes and invokes `scoreWowV17PickRequest` on `POST /score-pick-request` at the canonical Render Action origin.
3. Current pregame prop inventory is acquired. A genuinely empty slate is allowed only when supported by explicit source evidence; silent prop-source degradation is not healthy.
4. Each prop is routed to exactly one controlling WOW prop specialist.
5. Supported rows return the exact governed numeric probability package required by the lane, including calibration/lower bound where required.
6. Unsupported or failed rows preserve their exact typed governed failure. No market-implied probability, external projection, hit rate, or generic reasoning may be relabeled as governed model probability.
7. `V17_TERMINAL_REDUCER` remains the sole global terminal authority and `can_execute=false` remains invariant.
8. Rankable rows are returned to ChatGPT and displayed from actual governed receipts. Discovery-only candidates do not satisfy the journey.

## Health states

```text
PASS
  = a production ChatGPT canary completed the full path and returned at least one valid governed row or a legitimate explicit empty-slate result with source evidence.

FAIL_ACTION_EXPOSURE
  = the live GPT did not expose/invoke the canonical Action.

FAIL_PROP_ACQUISITION
  = current props were expected/available but zero prop rows reached governed routing without an explicit typed source blocker.

FAIL_PERSISTENCE
  = Scout/research persistence or board materialization failed, or a workflow reached success by skipping the persistence acceptance path.

FAIL_GOVERNED_SCORING
  = rows reached a controlling model but no valid governed result/typed failure package returned.

FAIL_RESPONSE_HANDOFF
  = governed rows completed but the live GPT did not receive/display the canonical result.
```

## False-green rule

`USER_JOURNEY_HEALTH=PASS` must never be inferred from any of the following alone:

- workflow conclusion `success`;
- skipped verification steps;
- repository CI success;
- backend health endpoint success;
- model-capability registry presence;
- Scout discovery success;
- locally reconstructed model output;
- manual research or external sportsbook/analytics projections.

The latest full-path canary receipt is the authority for this status.

## Current recovery gate

Until the live editor is saved/reloaded with the canonical Action schema and a real production ChatGPT canary succeeds:

```text
USER_JOURNEY_HEALTH = FAIL
REASON = NO_END_TO_END_GOVERNED_PROP_RESULT
LIVE_GPT_EDITOR_SYNC = PENDING
can_execute = false
```
