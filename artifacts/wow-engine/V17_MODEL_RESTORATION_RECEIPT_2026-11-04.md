# V17 model restoration receipt — 2026-11-04

```text
can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
WORKING_BRANCH = freebuff/b45b277542429f777af32f00
BASELINE_MAIN = 287d41483 (merge of #2427)
STATUS = SOURCE_LANES_INTEGRATED__PRODUCTION_CERTIFICATION_BLOCKED
```

This receipt records what was actually integrated and verified in a local
repository worktree. It is not a production certification and does not
publish a governed probability. Every number below comes from a pytest run
against this branch, not from CI or from the live Render deployment.

## 1. Lanes integrated on the working branch

| Lane | Disposition | Evidence |
| --- | --- | --- |
| NBA source PR #2386 | **Integrated** (`64e0a92f5`, conflict-free) | 284 passed / 22 skipped |
| NBA integration wrapper #2428 | **Superseded** — closed unmerged; #2386 merged directly | n/a |
| NHL PR #2395 | **Already on main** — diff vs `main` is one whitespace line | 52 passed baseline |
| Release automation #2429 | **Integrated** (`99edcfa7c`, conflict-free) | 402 passed |
| Release automation #2430 | **Integrated** (`66937ae1e`, conflict-free) | 402 passed |
| AMRT PR #1843 | **Already superseded by main — do not merge** | see §3 |

### NBA lane contents now on the branch

- `v17/nba_current_pregame_scorer.py` — fail-closed fitted pregame scorer seam
  (identity → feature lineage → rotation → certification → champion artifact →
  certified calibrator → separately approved dynamic bounds → calibrated output)
- `v17/nba_event_identity.py`, `v17/nba_points_*`, `nba_scalar_*` lineage
  hardening (canonical SportsDataVortex namespace; ESPN/BallDontLie provenance
  holds instead of silent mixing)
- `migrations/20261007_basketball_calibration_artifact_evidence.sql`
- `migrations/20261102_basketball_certified_calibrator_artifact_rpc.sql`
- `v17/basketball_team_event_calibration_artifact.py`,
  `v17/basketball_team_event_forward_features.py`,
  `v17/multisport_team_event_calibration.py` (bounds receipt binding)
- `v17/team_event_bridge_runtime.py`, `v17/team_event_model_registry_audit.py`

## 2. Verification actually run on this branch

| Suite | Files | Result |
| --- | --- | --- |
| Basketball / NBA / team-event / NHL boundary | 19 | **284 passed, 22 skipped** |
| Release, QA, render and governance gates | 11 | **402 passed** |
| AMRT, resident supervisor, Claude runtime | 11 | **271 passed** |
| Cross-sport consumers (certification replay, publication guard, NHL totals, NFL governed publication) | 14 | **178 passed** |
| Baseline before integration | 6 | **52 passed** |

1,187 passed, 22 skipped, 0 failed across those five batches. The 22 skips are the
PostgreSQL-mirror and live-credential cases that require a real database;
they are skipped, not passed, and are called out in §4.

## 3. AMRT PR #1843 is stale, not pending

Every file #1843 adds already exists on `main`, and `main`'s copies are
strictly larger. Merging it would *delete* working code:

```text
v17/amrt_operational_lanes.py                  -56 lines
v17/engineering_resident_supervisor.py        -560 lines
test_v17_engineering_resident_supervisor.py   -872 lines
docs/wow/engineering/WOW_AMRT_RECOVERY_TEAM.md  -5 lines
```

The AMRT lane on `main` passes 271 tests. The correct disposition for #1843
is close-as-superseded, which is a repository action outside this receipt.

## 4. Why the model is still not "restored" in the governed sense

The source lane is integrated and green, but the governing capability
manifest refuses to publish. Measured from this branch:

```json
{
  "MLB": {"certification_state": "CERTIFIED", "capability_status": "AVAILABLE"},
  "NBA": {"certification_state": "NOT_CERTIFIED", "capability_status": "MODEL_UNAVAILABLE",
           "capability_blocker": "TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED"},
  "NHL": {"certification_state": "NOT_CERTIFIED", "capability_status": "MODEL_UNAVAILABLE",
           "declared_identity": "NHL_GAME_WIN_PROBABILITY_EXPERT_V1"}
}
```

`CERTIFIED_TEAM_EVENT_SPORTS` contains MLB only. NBA is not present even as a
declared identity; NHL is declared-only, and the manifest states explicitly
that a declared specialist identity is a routing identity, not certification.

The NBA scorer itself is already written to stop at `MODEL_RESEARCH_ONLY_HOLD`
even on a fully passing certification path: it returns
`probability_publishable=false`, `rank_eligible=false`, and blockers
`LLP_EVENT_GOVERNANCE_NOT_PROVEN` +
`NBA_IMMUTABLE_PREDICTION_RECEIPT_UNAVAILABLE`. There is no code defect to fix
here — the remaining gap is production evidence.

Required to actually flip NBA/NHL to `AVAILABLE`, none of which can be
performed from a source worktree:

1. Apply `20261007_basketball_calibration_artifact_evidence.sql` and
   `20261102_basketball_certified_calibrator_artifact_rpc.sql` to the governed
   Supabase schema.
2. Register the certified NBA champion artifact, its certified calibrator and
   a **separate** `NBA_DYNAMIC_BOUNDS_V1` approval receipt binding that
   calibrator's SHA-256 in the protected registry
   (`v17/fitted_team_event_certification.py` / `wow_basketball_certified_*` RPCs).
3. Only then admit `NBA` to `CERTIFIED_TEAM_EVENT_SPORTS` — a governance
   decision that changes probability behavior and is a documented stop
   condition for an engineering-only change.
4. Produce the immutable pregame prediction receipt from the live runtime so
   `NBA_IMMUTABLE_PREDICTION_RECEIPT_UNAVAILABLE` can clear.
5. Repeat 1–4 for NHL against its own fitted artifact, plus valid goalie /
   rest-travel pregame inputs at the current event boundary.
6. Reconcile the deployed Render SHA against this branch and capture live
   scoring receipts.

## 5. Honest status language

```text
BACKEND_RUNTIME                = not re-verified (no production access from this worktree)
MODEL_CAPABILITY_NBA           = MODEL_UNAVAILABLE (source integrated, artifact not certified)
MODEL_CAPABILITY_NHL           = MODEL_UNAVAILABLE (declared identity only)
REPOSITORY_GOVERNANCE          = 1,187 targeted tests green on working branch
PROTECTED_MAIN                 = unchanged; this work sits on a feature branch pending PR
LIVE_GPT_EDITOR_SYNC           = RESYNC_REQUIRED_AFTER_PR766__LIVE_ACTION_ACCEPTANCE_REQUIRED
USER_JOURNEY_HEALTH            = FAIL (no end-to-end governed production result)
can_execute                    = false
```
