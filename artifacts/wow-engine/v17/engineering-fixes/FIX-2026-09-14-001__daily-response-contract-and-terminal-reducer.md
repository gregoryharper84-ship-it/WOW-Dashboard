# FIX-2026-09-14-001 — Daily response contract, terminal reducer, and lane/receipt semantics

## Status

TESTING

Code complete and green on the full backend suite. Not yet deployed, so
production verification below is unfilled and `FIXED_VERIFIED` remains false.

## Linked Postmortem(s)

- Reproduced production incident, live replay (2026-09-14):
  `DAILY_RESPONSE_SERIALIZATION_TOO_LARGE`, `TERMINAL_REDUCER_UPGRADE`,
  `1IP_CAPABILITY_MANIFEST_DRIFT`, `ZERO_ROW_REASON_MISCLASSIFIED`,
  `SCORING_ATTEMPTED_SEMANTICS_AMBIGUOUS`,
  `INFRASTRUCTURE_BLOCKED_SEMANTICS_SUSPECT`.

## Problem Statement

1. **PRIMARY — Daily response serialization.** `/v17/daily-snapshot-run`
   inlined the entire governed package for every scored row and direction
   (prediction, evidence ledger, acquisition packet, model artifact metadata,
   numerical-engine output, objective lanes, backend traversal). `max_props=12`
   and `max_props=3` both failed with `ResponseTooLargeError`; only
   `max_props=1` returned. The bounded Daily run could not complete for a
   normal client flow.
2. **CRITICAL — Terminal reducer upgrade.** For a one-row Daily replay
   (Reynaldo López) the controlling model returned
   `terminal_label=NO_LOW_PROBABILITY`, `model_qualified=false`,
   `pick_rejected=true`, `probability_rank_eligible=false`, while the outer
   Daily wrapper returned `outcome.status=HELD` / `row_status=HELD`. A hard
   inner terminal was upgraded to a softer hold —
   `RUN_INVALID_TERMINAL_UPGRADE`.
3. **1IP capability manifest drift.** `/score-pick-request` carries an active
   `MLB_STATS_API_OFFICIAL_1IP_V1` hydration route and returns 1IP-native
   terminals (`MLB_1IP_LINE_OUTSIDE_CERTIFIED_SUPPORT` / `REJECT_OOD` for Will
   Warren 16.5), but the advertised manifest named only
   `MLB / PITCHER_STRIKEOUTS`. The OOD decision itself is correct and is
   preserved; the defect is silent partial coverage in the manifest.
4. **Zero-row reason misclassified.** A `max_props=0` run reported
   `canonicalized_count=298` together with
   `zero_row_reason=NO_CANONICAL_CANDIDATES`. The zero rows were caused by the
   requested limit, not by an absent canonical lane.
5. **`scoring_attempted` ambiguity.** The host contract defines
   `scoring_attempted=false` only when no required Action call occurred, while
   the backend used the same name for "the specialist scorer ran". A direct 1IP
   Action receipt therefore read `scoring_attempted=false` after the Action had
   actually been invoked.
6. **`infrastructure_blocked` semantics.** A `MODEL_REJECTED` row carrying a
   concurrent market blocker reported `infrastructure_blocked=true`, conflating
   a model decision with an infrastructure cause.

## Root Cause Addressed

1. Confirmed: the Daily row builder embedded `market_api.score_prop`'s full
   response verbatim under `result.outcomes[].payload`, twice per row.
2. Confirmed: `_props_row_status()` could only ever emit `COMPLETED` or `HELD`.
   It read publication/rank flags and never read the controlling model's
   `pick_rejected` receipt, so every rejection collapsed to `HELD`.
3. Confirmed: there was no repository-side prop lane manifest at all; prop
   capability was advertised only as one aggregate `PROP_PROBABILITY` status,
   which cannot express a reachable-but-uncertified lane.
4. Confirmed: `_lane_reconciliation()` had no knowledge of the requested row
   limit, so limit-caused zero rows fell through to the
   `NO_CANONICAL_CANDIDATES` default. A second defect shared the root cause:
   acquisition was triggered on an empty *page* (`not prop_rows`) rather than an
   empty *lane*, so `max_props=0` also invoked the producer needlessly.
5. Confirmed: two layers wrote one field name for two different facts.
6. Confirmed: the reducer set `infrastructure_blocked` from the mere presence of
   a market blocker rather than from what caused the terminal.

## Scope

- Components: V17 Daily runner, prop terminal reducer, host routing receipt,
  prop response semantics, governance capability advertisement.
- Files:
  - added `v17/daily_terminal_reduction.py`
  - added `v17/daily_response_contract.py`
  - added `v17/prop_capability_manifest.py`
  - added `v17/sql/20260914_v17_daily_run_row_detail.sql`
  - modified `v17/daily_snapshot_runtime.py`, `v17/host_routing.py`,
    `v17/prop_response_semantics.py`, `prop_terminal_reducer_v2.py`,
    `pick_request_runtime.py`, `pick_request_runtime_core.py`,
    `mlb_1ip_ingress_runtime.py`, `api_prod.py`, `api_prod_market.py`,
    `v17/openapi.wow-betting-engine.v17.yaml`
- Routes/endpoints: `POST /v17/daily-snapshot-run` (compact by default);
  added `GET /v17/daily-snapshot-run/{run_id}/rows`
  (`readWowV17DailySnapshotRowDetail`); `GET /governance` now advertises
  declared prop lanes.
- Models/lanes: no model, distribution, calibration, line, or ranking behaviour
  changed. MLB 1IP declared `SUPPORTED_HOLD_ONLY`.
- Persistence/schema impact: new `public.wow_v17_daily_run_row_detail`
  (repository source only; RLS enabled, `anon`/`authenticated` revoked,
  `can_execute` constrained false). Not applied to production by this change.
- GPT editor/action impact: `DailySnapshotRequest.response_mode` added
  (`COMPACT` default, `FULL` for internal audit); one new read-only operation.

## Change

**Compact transport with paged evidence.** Daily persists the full per-row
package once to `wow_v17_daily_run_row_detail`, then returns compact rows
carrying identity, terminal, reduction audit, per-direction terminal and
calibrated probability fields, and a `detail_ref`. The complete evidence is read
back in bounded pages through `readWowV17DailySnapshotRowDetail`. Evidence is
relocated, not discarded. A detail-persistence failure fails closed with a typed
`DAILY_ROW_DETAIL_PERSISTENCE_UNAVAILABLE` blocker and
`detail_available=false`; `response_mode=FULL` still returns everything inline
for audit.

**Lowest-terminal reduction.** `v17/daily_terminal_reduction.py` ranks terminals
`INVALID < PURGED < REJECTED < HELD < COMPLETED` and reduces a row to the lowest
stage terminal. Stage classification reads the controlling model's own
`pick_rejected`/`terminal_label` receipt, including when the rejection arrives
as a typed HTTP error. `assert_no_terminal_upgrade()` reverts and flags any
softer-than-lowest result as `RUN_INVALID_TERMINAL_UPGRADE`. One scoped
exception is retained and audited: an approved stage keeps the row `COMPLETED`,
because two-sided props assess directions independently and the complement of a
favoured side is rejected by construction — collapsing on that would destroy
every valid approved row. The moneyline official-publication guard likewise no
longer softens a hard inner rejection into a hold.

**Prop lane manifest.** `v17/prop_capability_manifest.py` declares every
reachable prop lane with its true status and is advertised under
`/governance`. `MLB / PITCHER_STRIKEOUTS` stays `CERTIFIED_PRODUCTION`;
`MLB / 1ST_INNING_PITCHES_THROWN` is declared `SUPPORTED_HOLD_ONLY`,
`route_active=true`, `declared_skill_status=TEST_ONLY`,
`publication_allowed=false`, blocker
`MLB_1IP_ARTIFACT_PROSPECTIVE_CERTIFIED_NOT_PROMOTED`, with
`exact_line_support_policy=EXACT_CERTIFIED_LINES_ONLY_REJECT_OOD`. The certified
line set is *not* hardcoded: the manifest records that it is resolved from
`wow_prop_fitted_model_artifacts.validation_metrics.validated_lines` at score
time. Undeclared lanes fail closed as `NOT_DECLARED`.

**Zero-row reason.** `_lane_reconciliation()` now receives the requested limit,
reports `requested_row_limit`, and types limit-caused zero rows as
`REQUESTED_ROW_LIMIT_ZERO`. Explicit upstream failure typings keep precedence,
and a genuinely empty lane still reports `NO_CANONICAL_CANDIDATES`. Acquisition
is now triggered by an empty canonical lane rather than an empty page.

**Receipt semantics split.** `validate_full_model_action_receipt()` reports
`action_invocation_attempted` (the host contract's fact) and
`specialist_scoring_attempted` (the backend's fact, `UNKNOWN` when the backend
did not report it, never inferred from host invocation). The backend emits
`specialist_scoring_attempted`. `scoring_attempted` is retained at both layers
as a backward-compatible alias with its existing per-layer meaning.

**Terminal cause.** `PropTerminalDecision` gains `terminal_cause`
(`MODEL_JUDGMENT` / `INFRASTRUCTURE` / `EVENT` / `MODEL_SUPPORTED` /
`UNEVALUATED`) and `concurrent_infrastructure_blockers`. A model-decided
terminal reports `infrastructure_blocked=false` with the market blocker listed
as concurrent; the blocker itself is still preserved in `blockers`.
`infrastructure_blocked` is telemetry only — no gate consumes it.

## Governance Invariants

- [x] `can_execute=false` remains unchanged.
- [x] No live wager/order execution path was introduced.
- [x] Controlling specialist ownership remains intact.
- [x] Typed model/scorer/completion failures remain preserved.
- [x] No sportsbook implied probability or external projection is relabeled as
      governed model probability.
- [x] Probability/calibration fields are not modified solely to satisfy
      card/portfolio concerns.
- [x] No secret or service-role credential is exposed.

No gate was weakened. Every terminal change in this fix makes a row's reported
status stricter or more precise, never softer.

## Tests

### Unit

- `v17/test_daily_terminal_reduction.py` — terminal ranking, lowest-wins
  reduction, stage classification, unknown-status fail-closed, upgrade guard.
- `v17/test_prop_capability_manifest.py` — 1IP declared hold-only with
  exact-line policy; undeclared lanes fail closed; manifest advertises every
  active route.

### Contract

- `v17/test_receipt_semantics_split.py` — invoked Action whose scorer never ran
  is not reported as uninvoked; specialist fact is `UNKNOWN` rather than
  inferred; model rejection with a market blocker is not infrastructure-blocked.
- `v17/test_daily_response_contract_acceptance.py::test_published_action_contract_declares_the_paged_retrieval_route`
  — published Action contract matches the installed route surface.

### Regression

- Full backend suite: **1971 passed, 3 skipped** (baseline before this fix:
  1931 passed, 3 skipped). No test was deleted, skipped, or weakened.
- `test_mlb_1ip_exact_line_contract.py` unchanged and passing: Will Warren 16.5
  remains `REJECTED` / `REJECT_OOD` /
  `MLB_1IP_LINE_OUTSIDE_CERTIFIED_SUPPORT`, `infrastructure_blocked=false`.
- Two existing Daily tests now request `response_mode="FULL"` so they assert the
  identical full-payload evidence they asserted before compaction.

### Acceptance

- `test_twelve_row_daily_run_stays_within_client_response_budget` —
  `max_props=12` completes; compact response is 24,089 B, under both the 50 KB
  budget and the ~100 KB client limit.
- `test_full_mode_reproduces_the_oversized_response_the_compact_contract_avoids`
  — the same run inlined is 221,137 B (3 rows: 56,313 B), reproducing the
  original `ResponseTooLargeError` condition.
- `test_no_low_probability_stays_rejected_through_the_outer_wrapper` —
  `row_status=REJECTED`, `lowest_stage_terminal=REJECTED`,
  `rows_rejected=1`, `rows_held=0`, reconciliation balanced.
- `test_full_evidence_is_retrievable_in_pages_after_compaction` — untrimmed
  evidence ledger and objective lanes still readable by page.
- `test_requested_limit_zero_is_not_reported_as_no_canonical_candidates` —
  `canonicalized_count=298`, `zero_row_reason=REQUESTED_ROW_LIMIT_ZERO`.

## Deployment

- Branch: `claude/wizardly-planck-9uz0ni`
- Commit SHA: (pending)
- PR: (none requested)
- Deploy ID: (not deployed)
- Environment: (not deployed)
- Deployed at: (not deployed)

## Production Verification

Not yet performed. Git state is not production state.

- Health/governance check: pending
- Reproduction request/run ID: pending
- HTTP result: pending
- Terminal status: pending
- `scoring_attempted`: pending (verify `action_invocation_attempted` and
  `specialist_scoring_attempted` separately)
- Expected result: `max_props=12` returns without a response-size failure; a
  `NO_LOW_PROBABILITY` row returns `row_status=REJECTED`; Will Warren 16.5
  returns `REJECT_OOD`.
- Observed result: pending

## Rollback

- Rollback trigger: any Daily run returning a softer terminal than its stage
  ladder, or compact rows missing a terminal field a client depends on.
- Rollback procedure: revert this commit. The new SQL table is additive and
  unreferenced after revert; no migration rollback is required because the
  table is not applied to production by this change.
- Last known good commit/deploy: `a487d6d`.

## Result

**PARTIALLY COMPLETE — NOT VERIFIED IN PRODUCTION.** All six reported defects
are repaired in source with acceptance coverage, and the full backend suite is
green. `FIXED_VERIFIED=false` until a production replay confirms the three
acceptance conditions against the deployed SHA.

## Follow-up

- Apply `v17/sql/20260914_v17_daily_run_row_detail.sql` under independent review
  before the compact contract is relied on in production; until it exists, Daily
  reports `detail_available=false` with a typed blocker and `response_mode=FULL`
  remains the only full-evidence path.
- Pre-existing and out of scope for this fix: with `WOW_V17_ACTIVE=1`,
  `api_ncaaf_acceptance.app.openapi()` raises `PydanticUserError` for an
  unresolved `market_api.ScorePropRequest` forward reference. Verified present
  on the unmodified baseline. The published Custom GPT Action contract is the
  hand-maintained `v17/openapi.wow-betting-engine.v17.yaml`, which validates.
- Consider retention/pruning for `wow_v17_daily_run_row_detail`.

---

V17 terminal authority remains `V17_TERMINAL_REDUCER`; no engineering fix may override it.
