# LLP blocker restoration — November 10, 2026 (America/Chicago)

Status: ENGINEERING_REPAIRS_VERIFIED_LOCALLY / PRODUCTION_RESTORATION_INCOMPLETE.
can_execute=false. No model certification, publication authorization, merge,
production deploy, live wager or order occurred in this repair session.

## Source and runtime identity

- Original checkout: work at db5e6b7c (older than the supplied report).
- Audited GitHub main: 4c8c1fb0d375b01352fcf35eea3a7be6a0999295.
- Working branch: fix/llp-blocker-restoration-20261110.
- Repair baseline: PR #2551 head b748e804a97c9e486e6ad481e7c9fd1d8f2fbeac.
- NCAAB source patch imported unchanged from PR #2550 head
  941f7f3948bd23ba051419069aca265236139cdd (five files).
- Render workspace confirmed by user: My Workspace / tea-da7s2ggu01pc73br7gg0.
- Render service: wow-governed-probability-engine / srv-da7sa9gu01pc73brt80g.
- Render deploy dep-dbotumss728c73am2c80 is live at 4c8c1fb0d375b01352fcf35eea3a7be6a0999295.
  The deployment API establishes this commit identity, not current prediction
  qualification or a repaired production replay.

## Engineering repairs

1. Daily terminal projection: nonpublishable rows now withhold publication and
   ranking flags throughout the effective result, including direction and
   qualification objects, before FULL transport or row-detail persistence.
   Original claims remain explicitly under prepublication_claim for audit.
   Numerical evidence, blockers, and terminal authority are preserved.
2. Newest snapshot selection: canonicalize PASS source snapshots before blocker
   filtering. A newer blocked snapshot can no longer expose an older clean
   snapshot for the same sporting identity. Stable pagination adds snapshot-ID
   ordering after event-start ordering.
3. Post-acquisition accounting: every read replaces the exclusion ledger, so
   newly acquired blocked candidates are recorded and stale exclusions cannot
   double-count a candidate subsequently cleared. Partial reads expose no
   partial exclusion ledger and cannot claim balanced accounting.
4. Independent conservation check: count raw rows and sporting-key duplicate
   merges during acquisition, independently of canonicalization output. Compare
   raw rows with scored + explicitly unscored + excluded + merged rows.
   A regression deliberately dropping a candidate now trips
   RUN_INVALID_PROP_CANDIDATE_ACCOUNTING. The old check was an identity.
5. Persisted exclusions: reconcile successful write receipt IDs against eligible
   canonical IDs and explicit exclusion IDs. An acquired ruled-out snapshot is
   accounted for without falsely reporting PROP_SNAPSHOT_INGESTION_EMPTY.
   Missing/unread persisted snapshots retain the ingestion blocker.
6. Provisional basis: ACTIVE, PROBABLE or DOUBTFUL designation cannot suppress
   PROBABLE_STARTER revalidation. Designation and participation basis remain
   separate dimensions; provisional rows retain risk and recheck obligation.
7. Retrieval chronology: a positive official recheck needs a timezone-aware
   retrieval time. Omitting now can no longer accept an unbounded/future capture.
8. NCAAB identity: integrated the existing canonical event-identity repair and
   its tests. This repairs identity routing only; it does not create or certify
   an NCAAB publication model.

Audit corrections: fixed NFL fixture endpoint provenance; qualified historical
in-memory readback, repeated-run and 12-publishable-row claims; distinguished
returned RETRIEVAL_FAILED from provider exceptions; identified historical test
count scopes as unreconciled rather than equivalent; current row count is no
longer described as proof of write history.

## Verification

Tests were added before each new behavioral fix; the original failures were
observed and then made green. The imported NCAAB suite passed 26 tests.

Final focused command (from repository root):

```sh
PYTHONPATH=artifacts/wow-engine .venv/bin/python -m pytest -q \
  artifacts/wow-engine/v17/test_active_season_phase_a_daily_chain.py \
  artifacts/wow-engine/v17/test_daily_snapshot_runtime.py \
  artifacts/wow-engine/v17/test_daily_terminal_reduction.py \
  artifacts/wow-engine/v17/test_daily_response_contract_acceptance.py \
  artifacts/wow-engine/v17/test_daily_runlevel_compaction_regression.py \
  artifacts/wow-engine/v17/test_player_availability_gate.py \
  artifacts/wow-engine/v17/test_provisional_availability_recheck.py \
  artifacts/wow-engine/v17/test_availability_final_refresh_proof.py \
  artifacts/wow-engine/v17/test_availability_api_route.py \
  artifacts/wow-engine/v17/test_real_source_participation_contracts.py \
  artifacts/wow-engine/v17/test_availability_snapshot_durability.py \
  artifacts/wow-engine/tests/test_v17_ncaab_event_identity.py
```

Result: 167 passed, 2 existing FastAPI lifecycle deprecation warnings.

Full backend command, from artifacts/wow-engine:
`../../.venv/bin/python -m pytest -q --tb=short`

Result: 9338 passed, 50 skipped, 4 failed, 114 subtests passed, 146 warnings.
The full run began before the last handoff/exclusion fix; the final focused run
above verifies that final code. The full suite is NOT reported green.

All four failures are ENVIRONMENT_ACCESS_GAP, with proxy CONNECT 403 Forbidden:

- test_v17_nfl_atd_gsis_live_source.py::test_actual_official_nfl_gsis_scoring_pdf_is_parsed_without_writes
- test_v17_nfl_atd_nflcom_live_source.py::test_real_nflcom_bears_falcons_pdf_scoring_identity_is_read_only
- test_v17_nfl_atd_official_proposals_live_source.py::test_all_five_real_official_scoring_candidates_without_persisting[MORE-MISS]
- test_v17_nfl_atd_official_proposals_live_source.py::test_all_five_real_official_scoring_candidates_without_persisting[LESS-HIT]

Blocked hosts: www.nflgsis.com and static.www.nfl.com. A direct production
/health/live probe was also denied CONNECT 403 by this execution environment.
No network rules were bypassed; no tests were weakened or newly skipped.
The repository's preexisting 50 skips remain limitations of this run.

Read-only development code review found an ingestion misclassification, which
was reproduced, fixed and re-reviewed (26 focused tests passed). The reviewer
reported no remaining findings in that follow-up. This is not organizational
SIRT or Release Authority acceptance and is not a production certification.
`git diff --check` passed.

## Every reported blocker: remaining disposition

| Blocker | Disposition / closure evidence still needed |
| --- | --- |
| Repaired code not deployed | Confirmed open by Render deploy API. Review/protected merge and exact repaired-SHA deployment required. |
| Independent latest-head QA | Development review performed; protected-lane exact-head acceptance still required. Old PR #2551 CI success is not acceptance of this new change. |
| Release Authority / semantic review | No acceptance issued or fabricated. Authorized review remains required. |
| Production replay / FIXED_VERIFIED | Open; repaired code is not live. Run deployed-version dry-run reconciliation after release. |
| Live official source confirmation | Local route/provenance tests only; authentic pregame reacquisition receipts still required. |
| MLB | Existing certified bridge retained; individual game evidence and governance still required. |
| NFL | Candidate registration retained; exact champion forward cohort, calibration and approval must satisfy the real requirements. The report's old 82/100 count was not independently refreshed. |
| NBA | No complete registered production publication bridge. Certified fitted artifact plus current feature hydration and replay remain required. |
| NHL | Registered research bridge is not certified artifact availability. Artifact promotion and goalie/team/special-teams/injury hydration remain required. |
| NCAAB | Identity repair integrated locally; model replay/calibration, fitted-model promotion and publication bridge remain required. |
| NCAAF | Complete governed publication/failure-path/dynamic-bound chain remains required. |
| WNBA | Certified artifact and current features remain required. |
| Soccer | Competition-scoped certified three-way artifact/replay remains required. |
| Tennis | Certified retirement-aware model and replay remain required. |
| MMA | Certified fitted fight artifact lifecycle remains required. |
| PGA | Fitted field/head-to-head distribution and governed bridge remain required. |
| Boxing | Fitted fight specialist and governed bridge remain required. |
| Cricket | Exact fitted specialist/artifact and governed bridge remain required. |
| Cross-sport producer verification | No new producer certification was granted. Requires authentic evidence, freshness/identity validation and acceptance. |
| Final refresh deployment | Local loopholes repaired and covered; production behavior not proven until deployment/reacquisition. |
| Odds provider credentials | Restoration unverified. Render log retrieval returned HTTP 503 (Loki unavailable); it does not establish credential health. |
| Spreads | Research/shadow fitting does not grant publication certification. Exact market replay and acceptance remain required. |
| Totals | No all-sports certification asserted; market/sport-specific specialists remain required. |
| Game props | Dedicated market/stat specialist remains required; no moneyline substitution added. |
| Daily full-board accounting | Source defects repaired locally. Bounded scoring is not full-board qualification; production replay still required. |
| Settlement / persistence / exactly once | Repeated terminal consistency and in-memory readback are not production persistence proof. Deployed exact-once receipts still required. |

The local final-entrypoint registry still has seven registered sports and six
without registered bridges; only MLB is statically certified. No registry flags,
cohort counts or certification thresholds were changed to manufacture readiness.

## Release sequence

1. Enable permitted network access for the official NFL PDF hosts and production
   health origin; rerun the failed live-source tests and inspect their receipts.
2. Review the combined source repair (availability PR #2551 + identity PR #2550
   + this follow-up), obtain required exact-head CI/QA/SIRT/semantic/Release
   Authority acceptance, and use the protected merge path.
3. Deploy the accepted commit to the confirmed Render service; verify both the
   deploy record and application-reported SHA.
4. Capture real official pregame refresh, durable blocker preservation, terminal
   flags, candidate conservation and exact-once settlement/persistence receipts.
5. Independently finish each sport/market certification with real fitted
   artifacts and forward-graded data. Until then retain native hold/unavailable
   semantics; can_execute remains false.
