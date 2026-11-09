# Active-season E2E repair — status (2026-11-09)

Base: `8231e22a`. Branch: `freebuff/ce25c36cad5716d2677d452e`.
Mode: research only. `can_execute=false` everywhere; no wager or order path is
touched. Nothing here is `FIXED_VERIFIED`; no deployment or production replay
was performed.

## Phase A — Daily run-row-detail chain

Delivered: `prop_candidate_accounting` on the Daily response, plus
`v17/test_active_season_phase_a_daily_chain.py`.

The gap it closes: `_prop_manifest_rows` silently dropped acquired candidates
whose evidence was blocked, whose start had passed, or whose event was on
another slate. Those rows were neither scored nor explained, so a run could read
as a complete board while candidates had already vanished. Each dropped row now
carries a typed reason (`EVIDENCE_BLOCKED`, `EVENT_NOT_PREGAME`,
`NOT_REQUESTED_SLATE`), and `MAX_PROPS_LIMIT` covers eligible rows beyond the
requested scoring ceiling.

The ledger reconciles:

```text
manifest_rows_seen = scored_rows + eligible_not_scored_rows + excluded_rows
```

with `balanced` and a blocking `RUN_INVALID_PROP_CANDIDATE_ACCOUNTING` if that
identity ever fails. It is present on compact and FULL responses.

Proven by the new tests, against the real `run_daily_snapshot`:

- Every acquired candidate has exactly one disposition, and excluded ones never
  become scored rows.
- Reconciliation balances and every row terminates once.
- A 49-candidate slate with 12 publishable rows is represented without reading
  as full qualification; `rows_completed < manifest_rows_seen`.
- Durable row detail, read back through paged retrieval, agrees row-for-row on
  identity and terminal status with the compact response.
- Repeating the run produces identical terminals and reconciliation
  (idempotent row identity), and a late `PLAYER_RULED_OUT` stays `PURGED` even
  when the same payload claims `probability_publishable`/`rank_eligible`.

Not proven: availability is enforced at the snapshot write boundary (persisted
`blockers`), not re-evaluated when Daily reads the manifest. Daily trusts the
writer. Route-level tests prove the gate writes those blockers for ruled-out,
unverified and provisional evidence; a manifest row written without a blocker
but with unavailable `role_status` would still be scored. Closing that requires
either selecting `role_status` in the manifest read and applying the gate, or a
database constraint; it is not done here.

## Phase B — NFL production-priority verification

Source reality, from `nfl_prop_auto_hydration.py`: availability comes from
ESPN's athlete profile (`AVAILABILITY_SOURCE =
ESPN_NFL_ATHLETE_INJURY_REPORT`). The hydrator deliberately sets
`fresh = False` and `report_complete = False` because that endpoint is undated
and does not certify a complete official report. The gate therefore treats it
as `UNVERIFIED`, which is correct per this mandate: the absence of an injury
entry on an undated athlete profile is not clearance.

Consequence, stated plainly: the automatic NFL hydrator can produce an explicit
rule-out, but it can never clear participation. An NFL candidate cannot reach
the Verified Pool from this source alone. A game-specific official NFL report
(injury report with report date, game designation, and completeness) is missing
from the repository; without it, NFL qualification stays closed by design.

Status: `HELD_BY_MISSING_OFFICIAL_GAME_REPORT_SOURCE`. Not a code defect to
patch around, and no fallback was added.

## Phase C — NBA and NHL capability recovery

Not advanced in this pass. The Daily prop lane is MLB-only
(`daily_prop_acquisition.py`: `SPORT = "MLB"`), while cross-sport winner
discovery routes by registered bridge and returns a typed `MODEL_UNAVAILABLE`
when a sport has none. No probability was fabricated for NBA or NHL, and no
lane was promoted.

Required before those lanes can qualify: a certified fitted artifact per
statistic, feature hydration, calibration, and its own acceptance evidence.
Those are governance/model decisions, not plumbing repairs.

## Phase D — Exact market and platform evidence

Not advanced in this pass. The existing separation is preserved: a missing
market blocks money and final-pick qualification but does not erase a modeled
sporting probability, and no sportsbook-implied probability is substituted.
Active-sport market observation timestamps, settlement identity and price
evidence remain unproven for NFL/NBA/NHL.

## Phase E — Daily response and publication integrity

Partially proven by the Phase A tests: paged retrieval, oversized slate
handling, compact/detail agreement, and duplicate-run determinism. Worker
restarts, database failures mid-run, and Action transport interruptions are
covered only by pre-existing contract tests, not by a live Action exercise.

## Phase F — Deployment and original-scenario replay

Blocked. No independent QA/SIRT acceptance, no protected merge, no Render
deployment authority, and no production credentials in this workspace.
Production remains `4c8c1fb0d375b01352fcf35eea3a7be6a0999295`. The original
November 9 NFL injury-status failure has not been replayed against a deployed
backend.

## Summary

| Phase | State |
| --- | --- |
| A | Candidate accounting implemented and proven locally; writer-trust gap documented |
| B | NFL clearance impossible from the current official source; explicitly held |
| C | Not advanced; no fabricated probability |
| D | Separation preserved; active-sport market evidence unproven |
| E | Pagination/determinism/idempotency proven; transport and restart unproven |
| F | Blocked on authorization and credentials |
