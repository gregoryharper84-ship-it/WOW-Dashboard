# WOW V17 user-journey health

Updated: 2026-09-23

Status: **FAIL — NO_END_TO_END_GOVERNED_PROP_RESULT**

This status is intentionally stricter than component health. A healthy backend, repository CI, mounted route, historical editor evidence, successful Scout discovery, or available fitted model does not by itself prove that the product is usable from ChatGPT.

## Golden user journey

The production acceptance prompt is:

```text
Use full model and provide me the best props across all sports.
```

The journey is healthy only when the live production path proves all of the following in sequence:

1. The request originates from the production `WOW_BETTING_ENGINE` ChatGPT host.
2. The live host invokes `scoreWowPickRequest` on `POST /score-pick-request` at the production Render Action origin. `scoreWowV17PickRequest` is a compatibility alias, not the canonical live operation name.
3. Current pregame prop inventory is acquired. A genuinely empty slate is valid only with explicit source evidence; silent prop-source degradation is not healthy.
4. Attached multi-page source boards are reconciled page by page. A page that remains unreadable after the available page-level fallback stays typed as `SOURCE_PAGE_UNREADABLE:<page_number>` and prevents false full-board completion.
5. Each prop is routed to exactly one controlling WOW prop specialist.
6. Supported rows return the governed numeric probability package required by the lane, including calibration/lower bound where required.
7. Unsupported or failed rows preserve their exact typed governed failure. Sportsbook implied probability, external projections, hit rates, or generic reasoning may not be relabeled as governed model probability.
8. `V17_TERMINAL_REDUCER` remains the sole global terminal authority and `can_execute=false` remains invariant.
9. Rankable rows are returned to ChatGPT and displayed from actual governed receipts. Discovery-only candidates do not satisfy the journey.

## Health states

```text
PASS
  = a production ChatGPT canary completed the full path and returned at least one valid governed row, or a legitimate explicit empty-slate result with source evidence.

FAIL_ACTION_INVOCATION
  = the live GPT did not invoke the live pick-request Action for the user request.

FAIL_PROP_ACQUISITION
  = current props were expected/available but zero prop rows reached governed routing without an explicit typed source blocker.

FAIL_SOURCE_INGESTION
  = an attached board could not be completely reconciled after the available page-level fallback; this is not MODEL_UNAVAILABLE.

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
- historical live editor save/reload success;
- repository host-contract changes without a current editor save/reload and authenticated Action result;
- model-capability registry presence;
- Scout discovery success;
- locally reconstructed model output;
- manual research or external sportsbook/analytics projections.

The latest full-path production ChatGPT canary receipt is the authority for this status.

## Current recovery state

The backend route and governed scoring infrastructure are independently testable, but the production WOW editor is not currently attested to match the latest repository host contract. PR #766 changes the PrizePicks live-host addendum after the last historical editor evidence, so current parity requires a new editor save/reload and fresh-chat acceptance.

The remaining proof includes live `/health`, `scoreWowPickRequest`, immutable receipt lookup, and a multi-page PrizePicks source-ingestion acceptance under the PR #766 completeness contract.

Until that current live editor resync, Action acceptance, and golden production prompt complete the full governed path:

```text
USER_JOURNEY_HEALTH = FAIL
REASON = NO_END_TO_END_GOVERNED_PROP_RESULT
LIVE_GPT_EDITOR_SYNC = RESYNC_REQUIRED_AFTER_PR766__LIVE_ACTION_ACCEPTANCE_REQUIRED
can_execute = false
```
