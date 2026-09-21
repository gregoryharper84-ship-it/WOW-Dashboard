# WOW V17 Prop Hydration Audit — H10 NFL UTC Slate Rollover

Date: 2026-09-20
Scope: WOW_PROP_LANE acquisition / identity plumbing only
Execution authority: `can_execute=false`

## Production reproduction

The authenticated production self-acceptance replay used:

- Event: Indianapolis Colts at Kansas City Chiefs
- Player: Patrick Mahomes
- Stat: Passing Yards
- Line: 221.5
- Direction: MORE
- Kickoff: 2026-09-21T00:20:00Z (7:20 PM CT on September 20)
- ESPN provider event alias: `401872945`
- WOW canonical test identity: `WOW:NFL:2026-09-20:IND@KC`

The deployed `/score-pick-request` route returned HTTP 200 with a typed row hold. Interactive hydration logged:

`failure_codes=PROP_EVENT_IDENTITY_CONFLICT`

and the production self-acceptance terminated:

`code=PROP_EVENT_IDENTITY_CONFLICT mode=AUTO_HYDRATED_E2E_CONTRACT_FAIL`

The failure occurred before specialist inference.

## Root cause

`nfl_prop_auto_hydration._target_event` converted the supplied kickoff to UTC and queried ESPN using only:

`event_start.strftime("%Y%m%d")`

For this Sunday-night U.S. kickoff, the UTC date is September 21 while ESPN's NFL scoreboard groups the game on the September 20 league slate. The hydrator therefore queried the wrong single scoreboard date and found zero candidate events.

This is distinct from the H1–H9 canonical-event/alias defects repaired in PR #621. The canonical binding layer was not comparing the opaque WOW ID to the ESPN alias; the provider lookup failed before binding could occur.

## Repair

NFL event hydration now:

1. queries a bounded previous/current/next UTC calendar-date window around the kickoff;
2. deduplicates ESPN rows by provider event ID before candidate matching;
3. retains the existing 90-minute start-time tolerance, current-team requirement and pregame-status check;
4. leaves ESPN IDs as provider aliases and does not promote them into WOW canonical identity;
5. preserves V17 terminal authority and `can_execute=false`.

## Regression contract

`test_nfl_prop_utc_slate_rollover.py` reproduces the Colts–Chiefs case exactly:

- target kickoff `2026-09-21T00:20:00Z`;
- ESPN event appears only on `dates=20260920`;
- event alias is `401872945`;
- teams are KC and IND;
- expected provider resolution succeeds without weakening identity checks.

A second regression ensures the same ESPN alias returned on multiple adjacent scoreboard dates is deduplicated and cannot create a false multi-candidate conflict.

No sporting probability, calibration, ranking, market pricing, publication, or execution behavior is changed by this repair.
