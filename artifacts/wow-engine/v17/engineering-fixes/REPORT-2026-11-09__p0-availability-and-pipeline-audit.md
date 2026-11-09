# P0 report — 2026-11-09 availability incident and end-to-end pipeline audit

Status date: 2026-11-09. Branch audited: `freebuff/ce25c36cad5716d2677d452e`.
Baseline/HEAD: `4c8c1fb0` (`test(v17): chronology evidence fail-closed audit (#2540)`).
Production: `https://wow-governed-probability-engine.onrender.com` reports
`deployed_sha=4c8c1fb0d375b01352fcf35eea3a7be6a0999295`, so production code equals
this audit baseline. The availability repair below is **not deployed** (it is
uncommitted), so it is not part of the verified production capability.

Invariants preserved everywhere: `can_execute=false`,
`DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true`,
`terminal_authority=V17_TERMINAL_REDUCER`, no gate weakened, no test relaxed.

---

## 1. Root cause of the 2026-11-09 incident

Run `v17-daily-a2b7f50e-5ebd-4cab-a6c9-20e39edea2cc` admitted participants the
club had already ruled out into probability scoring. No payload for that run is
stored in the repository (no artifact, log, or Supabase row is checked in), so
the incident is reproduced from its inputs, not from a captured body.

Root cause: identity and availability were conflated. Hydration recorded roster
identity (`role_status.status = ACTIVE_CURRENT_ESPN_ROSTER`) and nothing else.
The pick-request validator required a role label, so a player who was on the
active roster but officially `OUT` passed validation and was scored. Two
independent gaps followed from that:

1. no official participation evidence was required before scoring; and
2. an absent availability fact was read as "not a problem" rather than
   "unverified".

The repair separates the two facts and makes absence a ranking blocker.

## 2. Issue disposition

| # | Handoff issue | Disposition | Evidence |
|---|---|---|---|
| 1 | Ruled-out/unavailable players scored | `FIXED_VERIFIED` (branch) | `v17/player_availability_gate.py`; 3 replay tests; 87/144/781-test runs below |
| 2 | Unverified availability admitted to Verified Pool | `FIXED_VERIFIED` (branch) | `PLAYER_AVAILABILITY_UNVERIFIED` blocks `rank_eligible` at any probability; `HELD`, not `COMPLETED` |
| 3 | Acquisition/normalization: current offer vs historical threshold vs missing market evidence | `PARTIALLY_REMEDIATED` | `market_evidence_hardening.classify_freshness(historical=True)` forces `current_market_evidence=False`; autonomous discovery lines are `AUTONOMOUS_DISCOVERY`, never a market line. No licensed current-offer feed is available in this environment, so platform-offer verification remains open (see §6) |
| 4 | All-sports Daily accounting; incomplete scoring reported as full board | `PARTIALLY_REMEDIATED` | Row accounting is explicit and reconciled (`_reconcile` → `rows_unclassified`, `balanced`; `_lane_reconciliation` → held/zero-row reason; `handoff_reconciliation` → prewrite/persisted/scored balances). Residual gap: `run_status` is run-lifecycle (`COMPLETED`/`COMPLETED_WITH_ACQUISITION_BLOCKERS`) and does not encode board quality; changing that enum is an Action-schema/governance decision, so it was not done unilaterally |
| 5 | Evidence binding, market comparison, payout/settlement, audit persistence, terminal reduction, immutable authorization | `PARTIALLY_REMEDIATED` | Terminal reduction verified by tests; settlement/authorization paths reviewed but not re-proven end-to-end (no licensed market feed, no Supabase credentials here) |
| 6 | Missing fitted specialists | `BLOCKED` | `prop_capability_manifest` declares 12 `BUILD_REQUIRED` families with no controlling specialist. Fitting/certifying them is Class C model work requiring labeled corpora and Model Excellence approval; substituting market-implied probability is prohibited |
| 7 | Integration/regression tests | `FIXED_VERIFIED` | New gate, binding, and incident-replay suites; 781 prop tests green |
| 8 | Reviewed PRs, deployment artifacts, verified production versions, acceptance replays | `BLOCKED` | Cannot create a reviewed PR, approve a deploy, or run a production acceptance replay without push authorization and production credentials. Production SHA was verified read-only (§7) |

## 3. What the repair changes (behavioral contract)

- `PLAYER_RULED_OUT` — official designation removes the player (`OUT`, `IR`,
  `INJ`, `PUP`, `NFI`, `SUSPENDED`, `INACTIVE`, `EXEMPT`, `DNP`, …). Raised in
  `_validate_evidence` before any scorer call. Verdict
  `PARTICIPATION_INVALIDATED`, `pick_rejected=True`, Daily `PURGED`.
- `PLAYER_AVAILABILITY_UNVERIFIED` — no official participation evidence, or
  retrieval failed. Scoring still runs so the row stays visible for research,
  but `rank_eligible=False`, `downstream_money_evaluation_allowed=False`, and
  the runtime terminal is `HELD` (not `COMPLETED`, not `REJECTED`). Daily
  classifies it `HELD`, so it cannot be counted as an approved row.
- `DOUBTFUL`/`QUESTIONABLE` — playable, reported as `risk_designation=True`.
  The repair is not a blanket refusal.
- Ordering guarantees: the unverified branch sits after capability/scorer/
  input/model-rejection branches, so `MODEL_UNAVAILABLE`,
  `MODEL_SCORER_FAILED`, `MODEL_INPUTS_INSUFFICIENT`,
  `MODEL_OUTPUT_INVALID`, and genuine `NO_LOW_PROBABILITY` are preserved. It
  overrides only model-supported, qualified, and market-blocked labels.
- The block is applied after the scorer's blockers and is idempotent, so no
  downstream stage can drop it; a scorer payload claiming `rank_eligible=True`
  cannot lift it.

### Evidence dimensions (post-review)

Clearance is recorded on three separate dimensions so they cannot be conflated:

- `designation` — an injury/availability designation only (`OUT`, `IR`,
  `DOUBTFUL`, …). An MLB role fact never populates it.
- `basis` — the official participation fact that named the player for the game:
  `PROBABLE_STARTER`, `CONFIRMED_STARTER`, `POSTED_BATTING_ORDER`,
  `FRESH_OFFICIAL_REPORT_UNLISTED`, `FRESH_OFFICIAL_REPORT_CLEARED`.
- provenance — `source`, `athlete_id`, `event_id`, `team`, `captured_at`,
  `fresh`, `report_complete`.

`PROVISIONAL_BASES` (`PROBABLE_STARTER`) clear availability but set `risk=True`,
which flags the row for the final pregame refresh to recheck the same official
source. `fresh=False` (stale), `report_complete=False` (incomplete/not-submitted
report) and any failed retrieval resolve to `UNVERIFIED` even when a playable
basis is present. An explicit `OUT` designation still wins over any basis.

### Phase 1 — source-backed participation contracts (2026-11-09)

`v17/test_real_source_participation_contracts.py` drives the providers against a
pinned corpus in `v17/fixtures/participation/`. Together with the wiring, replay
and gate suites it is **75 passed**.

**Provenance, stated plainly:** the corpus reproduces each provider's documented
response shape; it is *not* a network capture. No archived official responses
exist in this repository, and MLB/WNBA were out of season on 2026-11-09.
`manifest.json` sets `captured: false`, records the source endpoint per file and
pins every SHA-256; editing a fixture without updating the manifest fails the
suite. Replay over this corpus is contract evidence, never live pregame
verification. Fresh-feed confirmation is deferred to the next eligible slate.

| Check | Result |
|---|---|
| Official player/event identity binding | MLB schedule resolves `gamePk`/team/venue; WNBA resolves matchup+team section; NFL resolves athlete id |
| Exact endpoint + response shape | recorded per file in `manifest.json` |
| Report completeness + freshness | WNBA "not yet submitted" raises; `report_complete`/`fresh` gate to `UNVERIFIED` |
| Explicit OUT / inactive / unavailable | MLB hydration refuses a non-probable starter, WNBA raises, NFL yields `PLAYER_RULED_OUT` |
| Unlisted on a complete current report | WNBA full target-section proof can clear; NFL athlete-profile absence is `UNVERIFIED` until an independent dated, complete injury report is proven |
| Missing / stale / incomplete / conflicting / failed | distinct `UNVERIFIED` reason per state |
| Probable vs confirmed starter | `PROBABLE_STARTER` provisional (`risk=True`); `CONFIRMED_STARTER` not |
| Posted vs unposted order | `POSTED_BATTING_ORDER` clears; unposted holds |
| Late scratch / post-hydration change | `NOT_IN_POSTED_BATTING_ORDER` holds with its own reason |

Two provider defects were found and fixed by this suite:

- a hitter absent from a **posted** order produced the same evidence as an
  unposted lineup, so a late scratch was indistinguishable from pending news;
  `_batting_order_state()` now reports `PLAYER_NOT_IN_POSTED_ORDER`;
- NFL athlete detail absence previously carried `FRESH_OFFICIAL_REPORT_UNLISTED`
  despite lacking a complete independently dated injury-report receipt. The
  endpoint now returns `OFFICIAL_REPORT_COMPLETENESS_UNVERIFIED` and
  `fresh=False, report_complete=False`. Explicit matched `OUT` remains a
  `PLAYER_RULED_OUT` terminal; a missing, mismatched, or empty record cannot
  establish positive availability. This is contract-tested, not live-certified.

### Pre-merge review findings (2026-11-09)

Each automated-review finding was reproduced or dismissed against `a3c6403e`.
Five were real and are fixed; one is partially addressed.

| Finding | Disposition |
|---|---|
| P0 NFL athlete endpoint 404 | **Confirmed live.** `site.api.espn.com/apis/site/v2/sports/football/nfl/athletes/4241457` returned 404; `site.api.espn.com/apis/common/v3/.../athletes/4241457` returned 200 with the `athlete` envelope. The hydrator now uses common/v3 with a core-API fallback chain and records `source_endpoint` on the evidence. |
| P0 clearance without complete provenance | **Confirmed.** Clearance now requires source, capture time, `fresh=True`, athlete, event, and team. Otherwise the gate returns `UNVERIFIED` with reason `AVAILABILITY_PROVENANCE_INCOMPLETE` and the missing fields on the receipt. NFL availability is bound to the resolved event/team (`bind_availability_to_event`) because the athlete record alone names neither. |
| P0 `PROBABLE_STARTER` rank-eligible before recheck | **Confirmed.** Provisional bases set `requires_revalidation`; the route emits `PLAYER_AVAILABILITY_RECHECK_REQUIRED`, which both the qualification policy and the terminal reducer treat as a Verified-Pool block. The route test proves the row is scored for research yet held. |
| P0 WNBA report "complete" before the team section is parsed | **Confirmed.** The parser that production actually uses (`wnba_injury_status.availability_from_report`, swapped in by the hydration router) and the base provider both bound the section with the opposing team header and require parsed status rows before an absence can read as no designation. New code: `WNBA_INJURY_REPORT_SECTION_INCOMPLETE`. |
| P0 WNBA OUT through the wrong error path | **Confirmed.** An explicit OUT now returns a ruling (`availability=UNAVAILABLE`, `designation=OUT`); hydration emits it, the runtime raises `PLAYER_RULED_OUT`, and Daily purges. QUESTIONABLE/DOUBTFUL still refuse uncertified unconditional model adjustment. |
| P1 blocker persistence through every consumer | **Partially addressed.** New route-level tests (`v17/test_availability_api_route.py`) cover OUT rejected before the scorer, missing evidence held with scoring still permitted, provisional held with the recheck receipt, and a changed ruling rejected on the second request. Durable-snapshot persistence and every Daily/specialist consumer are not yet individually proven. |

PR-check reconciliation: the counts above are for this commit. The repository's
docstring-coverage and patch-coverage bot results cannot be reproduced locally;
new public functions carry docstrings and the new branches (provenance gap,
provisional recheck, bounded WNBA section, incomplete section, NFL fallback and
binding) each have a test.

### Durable persistence and Daily reduction (finding 6)

The route computed the availability verdict and then dropped it:
`_snapshot_payload` wrote `hydration_status="PASS"` and `blockers=[]`
unconditionally. Every re-reader of `wow_prop_evidence_snapshots` therefore saw
a ready row -- the SQL reader, the live self-acceptance check, and any later
Daily reconstruction -- while the hold existed only in the HTTP response. That
was the persistence half of the finding, and it was real.

The verdict is now written to the row's `blockers` column. It is deliberately
excluded from the evidence fingerprint so the same evidence keeps one
`source_snapshot_id` when a later recheck clears the hold; the verdict is not
evidence identity. `wow_prop_evidence_snapshot()` already returns
`RUN_INVALID_ACQUISITION_INCOMPLETE` with the stored blockers whenever the
column is non-empty, so the durable row now enforces the route's decision.

Evidence, in two layers:

- `v17/test_availability_snapshot_durability.py` (5 tests) drives the real
  route: an unverified row persists `PLAYER_AVAILABILITY_UNVERIFIED`, a
  provisional row persists `PLAYER_AVAILABILITY_RECHECK_REQUIRED`, a cleared
  row persists `[]`, a ruled-out row writes no snapshot at all, and the Daily
  reducer classifies the durable labels as `HELD` (both holds) and `PURGED`
  (`PLAYER_RULED_OUT`).
- `test_prop_snapshot_min3_postgres.py` (17 tests, 2 new) executes the actual
  migrations against PostgreSQL 16 and proves that `role_status.availability`
  and the blocker survive insert -> `wow_prop_evidence_snapshot()` -> re-read,
  returning `ok=false` and `probability_publishable=false`.

The database runs were against an unprivileged embedded PostgreSQL 16 built
from the project's own migration SQL. The sandbox has no root and no usable
Docker socket, so this is not the CI integration service; it is a real server
executing the same SQL, and the results are reproducible only with that binary
present. No production database was contacted.

Not proven here: the Daily run-row-detail table and any specialist consumer
that rebuilds the verdict from a snapshot are still not exercised, and no
production replay has happened. Status therefore remains
`PARTIALLY_REMEDIATED`.

### Phase 4 — workstream status

| Workstream | Status |
|---|---|
| Availability enforcement | local verified, PR #2551 open |
| Fresh all-sports prop acquisition | not started |
| Real platform-offer / exact-line verification | blocked: no licensed market feed |
| Candidate coverage / Daily reconciliation | not started |
| Market, payout, settlement binding | blocked: no licensed market feed |
| Specialist certification (12 families) | blocked: no labeled corpora / Class C certification |
| Arithmetic audit persistence | not started |
| Response pagination | not started |
| Recommendation-ledger authorization | not started |

## 4. November 9 replay

`v17/test_nov9_availability_incident_replay.py` replays the three historical
states through the real boundary (`_validate_evidence` → qualification policy →
terminal reducer → `_completed_scored_outcome` → Daily classification):

| Replay | Result |
|---|---|
| `ROSTER_ONLY` (pre-repair hydration) | `UNVERIFIED` → `HELD`, `rank_eligible=False`, not publishable |
| Control: same model package, availability cleared | `COMPLETED`, `rank_eligible=True` (isolates the gate) |
| `OUT` | `PLAYER_RULED_OUT:OUT` raised before probability; Daily `PURGED` |
| `OUT` + p=0.99 qualified package | still blocked; money evaluation disallowed |
| `DOUBTFUL` | validates, `AVAILABLE`, `risk_designation=True` |
| Scorer claims eligibility at p=0.99 | `rank_eligible=False`, `model_qualified=False` |

The control row proves the block is the availability fact, not a broken scorer.

## 5. Test evidence

Run from `artifacts/wow-engine` with the project virtualenv:

- Availability, route and snapshot-durability suites in a focused run:
  **51 passed**.
- Prop + WNBA + snapshot surface, source contracts, pick-request and schema
  suites with the ephemeral Postgres enabled: **1103 passed, 0 skipped**.
  Without a database the same set is **880 passed, 15 skipped**; the 15 are the
  `test_prop_snapshot_min3_postgres.py` acceptance cases.
- Action/editor schema compatibility: **33 passed**
  (`test_pick_request_action_contract.py`, `tests/test_v17_live_editor_compat.py`,
  `v17/test_v17_candidate_contracts.py`).
- No production-path test was skipped or weakened. One synthetic fixture in
  `test_pick_request_runtime.py` was updated to carry verified availability
  evidence, because a row that models a verified-available player must now
  supply that evidence to complete; its assertions were unchanged.

Honest limitation: these are local unit/integration results. They do not prove
production behavior, and the uncommitted repair is not deployed.

## 6. External blockers

- **Licensed/current platform offers.** No credential or feed is available in
  this environment, so "current offer" cannot be observed and verified against
  a platform. Autonomous discovery lines remain candidate-only.
- **Production data.** No Supabase service credentials, so no live row,
  settlement, audit-persistence, or calibration-cohort verification.
- **Fitted specialists (12 families).** Requires labeled corpora plus
  certification/calibration under Class C governance.
- **Deployment and PR review.** Pushing, merging, and deploy approval are
  outside this session's authorization; `main` is protected.
- **MLB/WNBA availability wiring — since resolved.** Both hydration paths emit
  official participation evidence in the gate's canonical shape via
  `participation_evidence()`. MLB records `basis=PROBABLE_STARTER`
  (`confirmation=PROBABLE`, `risk=True`) from the official probable-pitcher
  listing, or `basis=POSTED_BATTING_ORDER` (`confirmation=CONFIRMED`) from the
  posted official batting order; neither claims injury clearance. A pitcher the
  schedule no longer names probable fails hydration with
  `MLB_STARTER_STATUS_UNRESOLVED` and never scores. WNBA records
  `basis=FRESH_OFFICIAL_REPORT_UNLISTED` with `report_complete=True` from the
  fresh official injury report; an unsubmitted team section raises
  `WNBA_INJURY_REPORT_NOT_SUBMITTED` instead of clearing.

## 7. Source, capability, and production matrices

**Source dependency (per lane)**

| Lane | Official source | Market evidence | Publication gate |
|---|---|---|---|
| MLB pitcher props | MLB Stats API schedule + official box scores | none/current-offer unverified | fitted specialist + exact line policy |
| NFL props | ESPN roster + NFL athlete injury report | none/current-offer unverified | fitted specialist |
| WNBA props | official schedule/CDN | none | `SUPPORTED_HOLD_ONLY` |
| NBA props | registry candidate | none | `CANDIDATE_ONLY` |
| Team/event | discovery feed + provider bridges | odds proxy/rundown | bridge certification |
| NHL/SOCCER/TENNIS/PGA/MMA/BOXING/CRICKET | — | — | `BUILD_REQUIRED` |

**Capability (from `v17/prop_capability_manifest.DECLARED_PROP_LANES`)**

- `CERTIFIED_PRODUCTION` (9): MLB `PITCHER_STRIKEOUTS`, `PITCHING_OUTS`,
  `STRIKES_THROWN`, `BALLS_THROWN`, `PLATE_APPEARANCES`; NFL `PASSING_YARDS`,
  `RUSHING_YARDS`, `RECEIVING_YARDS`, `ANYTIME_TD`.
- `SUPPORTED_HOLD_ONLY` (5): MLB `1ST_INNING_PITCHES_THROWN`; WNBA `POINTS`,
  `REBOUNDS`, `ASSISTS`, `THREE_POINTERS_MADE`.
- `CANDIDATE_ONLY` (9): WNBA composites (4), NBA scalars (6 incl. PRA family),
  NFL/NBA/WNBA/MLB fantasy score (5) — counted per lane in the manifest.
- `BUILD_REQUIRED` (remainder): NBA threes, NFL passing TDs/attempts/
  completions/rush attempts/receptions, all NCAAF, NCAAB, NHL, SOCCER, TENNIS,
  PGA, MMA, BOXING, CRICKET.

**Production**

- Service `wow-governed-probability-engine`, runtime `V17_ACTIVE`,
  `can_execute=false`, `/health` live check returned `status=ok` with
  `deployed_sha=4c8c1fb0d375b01352fcf35eea3a7be6a0999295` = local HEAD.
- Because the repair is uncommitted, production does **not** yet contain the
  availability block.

## 8. Changed files

- `v17/player_availability_gate.py` (new): designation policy, `UNVERIFIED`
  handling, receipt.
- `pick_request_runtime_core.py`: pre-score enforcement, availability in the
  normalized evidence, `premodel_blockers` plumbing, `HELD` terminal status.
- `qualification_policy_v2.py`: participation and participation-unverified
  branches; rank/money denial.
- `prop_terminal_reducer_v2.py`: participation branches; terminal labels.
- `v17/daily_terminal_reduction.py`: purge set for ruled-out only.
- `nfl_prop_auto_hydration.py`: ESPN athlete injury-report retrieval, latest
  entry wins, `RETRIEVAL_FAILED` → `UNVERIFIED`.
- Participation wiring: `prop_auto_hydration.py`,
  `prop_auto_hydration_workload.py`,
  `prop_auto_hydration_plate_appearances.py`,
  `v17/mlb_pitcher_fantasy_score_hydration.py`,
  `wnba_prop_auto_hydration.py`, `v17/wnba_composite_auto_hydration.py`.
- `v17/player_availability_gate.py`: canonical `participation_evidence()`
  builder so providers cannot drift in shape.
- Tests: `v17/test_player_availability_gate.py`,
  `v17/test_player_availability_runtime_binding.py`,
  `v17/test_nov9_availability_incident_replay.py`,
  `v17/test_mlb_wnba_participation_wiring.py` (new),
  `test_pick_request_runtime.py` (fixture evidence only).
