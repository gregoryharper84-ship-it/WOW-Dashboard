# PM-2026-09-14-001 — Daily response contract, terminal reducer, and lane/receipt semantics

- status: FIXED (partial — see Closure)
- severity: P0
- domain: V17 Daily runner / prop terminal reducer / host routing receipt semantics
- created_utc: 2026-09-14T00:00:00Z
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Impact

A live 2026-09-14 replay found six distinct defects in `POST /v17/daily-snapshot-run`
and its supporting reducer/manifest/receipt layers:

1. **PRIMARY** — the compact Daily route inlined the full governed package for
   every scored row/direction, so `max_props=12` and `max_props=3` both failed
   with `ResponseTooLargeError`; only `max_props=1` returned. The bounded
   Daily run could not complete for a normal client flow.
2. **CRITICAL** — a hard inner terminal (`terminal_label=NO_LOW_PROBABILITY`,
   `pick_rejected=true`) was upgraded by the outer Daily wrapper to a softer
   `outcome.status=HELD` / `row_status=HELD` (`RUN_INVALID_TERMINAL_UPGRADE`).
3. `/score-pick-request` carried an active `MLB_STATS_API_OFFICIAL_1IP_V1`
   hydration route and returned 1IP-native OOD terminals, but the advertised
   capability manifest named only `MLB / PITCHER_STRIKEOUTS` — silent partial
   coverage in the manifest (the OOD decision itself was correct).
4. A `max_props=0` run reported `canonicalized_count=298` with
   `zero_row_reason=NO_CANONICAL_CANDIDATES`; the zero rows were caused by the
   requested limit, not an absent canonical lane.
5. The host contract's `scoring_attempted=false` ("no required Action call
   occurred") and the backend's use of the same field name for "the
   specialist scorer ran" collided, so a direct 1IP Action receipt could read
   `scoring_attempted=false` after the Action had actually been invoked.
6. A `MODEL_REJECTED` row carrying a concurrent market blocker reported
   `infrastructure_blocked=true`, conflating a model decision with an
   infrastructure cause.

## Evidence

Live replay evidence captured 2026-09-14 (Reynaldo López one-row Daily replay
for item 2; `max_props=12`/`max_props=3` `ResponseTooLargeError` traces for
item 1; Will Warren 16.5 `MLB_1IP_LINE_OUTSIDE_CERTIFIED_SUPPORT` /
`REJECT_OOD` trace for item 3). Full evidence and reproduction detail are
recorded in `FIX-2026-09-14-001`.

## Root Cause

1. The Daily row builder embedded `market_api.score_prop`'s full response
   verbatim under `result.outcomes[].payload`, twice per row.
2. `_props_row_status()` could only ever emit `COMPLETED` or `HELD`; it never
   read the controlling model's `pick_rejected` receipt, so every rejection
   collapsed to `HELD`.
3. No repository-side prop lane manifest existed; capability was advertised
   as one aggregate `PROP_PROBABILITY` status, which cannot express a
   reachable-but-uncertified lane.
4. `_lane_reconciliation()` had no knowledge of the requested row limit, so
   limit-caused zero rows fell through to the `NO_CANONICAL_CANDIDATES`
   default; acquisition was also triggered on an empty *page* rather than an
   empty *lane*.
5. Two layers wrote one field name for two different facts.
6. The reducer set `infrastructure_blocked` from the mere presence of a
   market blocker rather than from what caused the terminal.

## Governance Classification

R1/R2-restorative deterministic implementation defects. Every terminal
change makes a row's reported status stricter or more precise, never softer;
no probability, calibration, threshold, or qualification-policy math changed.
`can_execute=false` and `V17_TERMINAL_REDUCER` precedence are unchanged.

## Linked Engineering Fixes

- FIX-2026-09-14-001

## Closure Criteria

1. A bounded Daily run (`max_props=12`) completes without
   `ResponseTooLargeError`, with full evidence still retrievable by page.
2. A row whose controlling model rejects it (`pick_rejected=true`) never
   reports a softer terminal than its own decision.
3. `/governance` advertises every reachable prop lane's true status,
   including uncertified-but-routed lanes.
4. A requested-limit-caused zero-row run reports `REQUESTED_ROW_LIMIT_ZERO`,
   not `NO_CANONICAL_CANDIDATES`.
5. Host-contract `action_invocation_attempted` and backend
   `specialist_scoring_attempted` are reported as distinct fields.
6. A model-decided terminal with a concurrent market blocker reports
   `infrastructure_blocked=false` with the blocker preserved as concurrent
   evidence.

## Closure Evidence (2026-09-15/16)

Items 1 and 2 (PRIMARY, CRITICAL) were independently verified against live
production persistence: three real 12-row Daily runs each carrying 469,134
bytes of row payload (39,094 B/row) now complete instead of failing
`ResponseTooLargeError`; 36 `NO_LOW_PROBABILITY` PROPS rows terminate
`REJECTED` (`terminal_upgraded_from_rejection=false` across all 850 sampled
rows, `row_status=COMPLETED` count zero for that cohort). `can_execute=false`
held on all 850 rows.

Items 3–6 were deployed in the same commit but were **not independently
observed** in the 2026-09-15T15:28Z–2026-09-16T15:23Z production evidence
window (no `/governance` read, no `max_props=0` run, and no fresh 1IP Action
receipt appeared in that window). This record therefore reflects the ledger
in `FIX-2026-09-14-001`'s own status: `DEPLOYED — PRIMARY and CRITICAL
VERIFIED IN PRODUCTION`, not a global `FIXED_VERIFIED`.

## Prevention / Follow-up

- Owner-run Action replay to close out items 3–6: read `/governance` for the
  declared 1IP lane, run Daily once with `max_props=0` to confirm
  `REQUESTED_ROW_LIMIT_ZERO`, and check one receipt for
  `action_invocation_attempted` vs `specialist_scoring_attempted`.
- This record was retroactively created on 2026-09-21 to close a ledger
  drift gap: `FIX-2026-09-14-001` existed on disk and was merged/deployed
  (PR #413) with no corresponding `PM-*.md` file and no `incident-ledger.json`
  entry, so the incident was invisible to ledger-driven nightly discovery.
  No code, test, or production behavior changed as part of adding this
  record.

---

V17 safety invariant: `can_execute=false`. This record cannot authorize, route, modify, approve, or cancel a wager/order.
