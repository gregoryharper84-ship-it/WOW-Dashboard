# WOW V17 NHL Status and Disposition Registry

Companion to [`failure_codes.md`](failure_codes.md). The failure registry stays the
single source of truth for typed *failure* codes. This file catalogues the
success and informational dispositions emitted by the NHL lanes so operators can
distinguish "this stage completed" from "this model is certified".

Every disposition below grants **no** probability, rank, certification,
promotion or execution authority, regardless of how positive its wording is. A
disposition is a stage receipt, not a model verdict, and none of them is
`MODEL_UNAVAILABLE` or `MODEL_INPUTS_INSUFFICIENT`.

The **Emitting module** column names the module that actually produces the
string, and `tests/test_nhl_status_registry.py` asserts each claim against that
module's source, so a renamed or relocated disposition fails the guard.

Lane order is evidence-first: identity → source admission → features → fit →
calibration → validation → pregame evidence → candidate persistence.

## NHL-prefixed dispositions

| Disposition | Lane / stage | Emitting module | Meaning | Grants authority? |
|---|---|---|---|---|
| `NHL_CANDIDATE_PREFLIGHT_READY` | Candidate package preflight | `v17/nhl_candidate_preflight.py` | Source identity, artifact checksum, disjoint chronological partitions and the recomputed dataset hash all match. The evidence is fit to persist as a CANDIDATE only. | None |
| `NHL_GOALIE_STATUS_UNCHANGED` | Frozen → current goalie handoff | `v17/nhl_candidate_preflight.py` | Both source-pinned snapshots agree on event, kickoff, starter identity and a confirmed status, and the current receipt is genuinely refreshed inside the 4-hour window. Evidence reconciliation only. | None |
| `NHL_PREGAME_GATE_INPUTS_COMPLETE` | Pregame orchestration | `v17/nhl_pregame_orchestration.py` | Receipts persisted and the gate passed, so required evidence, full-game settlement basis, canonical identity and independent goalie confirmation are all present. Inputs complete, explicitly not certification. | None |
| `NHL_PLAYER_BOX_OUTCOME_RESEARCH_ONLY` | NHL player box outcome research | `v17/nhl_player_box_outcome_research.py` | Player-game box outcomes were assembled as a research dataset; no fitted probability, ranking or publication. | None |
| `NHL_TOTALS_OFFICIAL_HISTORICAL_PROJECTION_VERIFIED` | Official fixture admission | `v17/nhl_total_goals_official_fixture.py` | A season projection passed 1,312-game coverage, two-mirror identity, pinned source and regime checksums, and REG/OT/SO label admission. | None |
| `NHL_TOTALS_REAL_SOURCE_RESEARCH_FEATURES_READY` | Prior-game feature reconstruction | `v17/nhl_total_goals_official_fixture.py` | Eligible prior-game feature rows were rebuilt from admitted seasons; no fit has occurred yet. | None |
| `NHL_TOTALS_REGIME_FIT_INPUTS_RECONCILED_RESEARCH_ONLY` | Feature → regime label bridge | `v17/nhl_total_goals_model_bridge.py` | Every feature row joined to an exact verified regime label with preserved lineage; research inputs only. | None |
| `NHL_TOTALS_RETROSPECTIVE_SOURCE_LABELS_RECONCILED` | D1 ↔ official regime join | `v17/nhl_total_goals_regime_source.py` | D1 events and official regime results reconciled one-to-one with no missing or extra labels. | None |
| `NHL_TOTALS_OFFICIAL_REGIME_COHORT_RECONCILED` | Official cohort fetch | `v17/nhl_total_goals_regime_source.py` | All requested seasons reached full 1,312-game and 2,624-mirror-instance coverage. | None |
| `NHL_TOTALS_OFFICIAL_REGIME_PARTIAL_RESEARCH_ONLY` | Official cohort fetch (research subset) | `v17/nhl_total_goals_regime_source.py` | A research team subset reconciled but coverage is explicitly unverified. This disposition can never stand in for a complete cohort. | None |
| `NHL_TOTALS_PARTIAL_RESEARCH_NO_FIT` | Offline replay (partial corpus permitted) | `v17/nhl_total_goals_offline_replay.py` | The corpus is incomplete and completeness was not required, so coverage is returned with no model fit and a null probability and lower bound. | None |
| `NHL_TOTALS_REAL_5SEASON_REGIME_REPLAY_RESEARCH_ONLY` | Five-season official replay | `v17/nhl_total_goals_official_fitted_replay.py` | The mixture and Poisson models were fitted on identical cohorts and compared. Class-C exploratory science, not certified probability. | None |
| `NHL_TOTALS_RESEARCH_MODEL_REPLAY_COMPLETED_NOT_CERTIFIED` | Offline replay | `v17/nhl_total_goals_offline_replay.py` | A full-corpus research model was fitted and replayed; serving registration, independent certification and publishability all remain false. | None |

`NHL_PREGAME_GATE_INPUTS_COMPLETE` is the orchestration status; the gate's own
return status is the unprefixed `INPUT_EVIDENCE_PASS_NOT_CERTIFICATION` below.

## Lane statuses (unprefixed)

These carry no sport prefix, so a code-only grep will not find them. They are the
terminal status values of the NHL lanes.

| Disposition | Lane / stage | Emitting module | Meaning | Grants authority? |
|---|---|---|---|---|
| `PREGAME_EVIDENCE_COMPLETE_NOT_CERTIFICATION` | Pregame orchestration | `v17/nhl_pregame_orchestration.py` | Receipts were persisted and the evidence gate returned a pass. Explicitly inputs-complete, not certification. | None |
| `INPUT_EVIDENCE_PASS_NOT_CERTIFICATION` | Pregame evidence gate return | `v17/nhl_pregame_evidence_boundary.py` | Validator status when nothing is missing and no blocker fired; still returns `probability_publishable=false`, `rank_eligible=false`, `can_execute=false`. | None |
| `HELD` | Gate, orchestration, confirmation, Gamecenter capture | `v17/nhl_pregame_evidence_boundary.py` | Generic fail-closed hold. Also emitted by orchestration, the confirmation receipt and the Gamecenter capture adapter; the accompanying blocker list names the exact typed cause. | None |
| `BLOCKED` | Model maintenance | `v17/nhl_model_maintenance.py` | Maintenance stopped at `blocked_stage` with a typed `code` and detail. Nothing was fitted or persisted. | None |
| `BLOCKED_TYPED` | CI replay runner | `v17/nhl_goalie_shot_runner.py` | The runner wrote a typed block report with the underlying code and exception type, then exited non-zero. | None |
| `CANDIDATE_BUILT` | Candidate pipeline build | `nhl_candidate_pipeline.py` | A CANDIDATE package was built with certification, promotion, publishability and execution all false. | None |
| `CANDIDATE_EVIDENCE_UPDATED` | Model maintenance persistence | `v17/nhl_model_maintenance.py` | Reconciled candidate evidence was persisted. Still no certification, promotion, publication or execution. | None |
| `ADVISORY_RESEARCH_ONLY` | Goalie/shot challenger report | `v17/nhl_goalie_shot_challenger.py` | Side-by-side V1/V2 research report. A passing research gate only routes to forward shadow and governed review. | None |
| `RESEARCH_REGIME_MIXTURE_FITTED_HOLD` | Regime mixture fit | `v17/nhl_total_goals_regime_mixture_research.py` | The mixture fitted but is held: no calibrated lower bound, and certification and promotion remain false. | None |
| `EXPERIMENT_CREATED` | Poisson challenger fit | `v17/nhl_total_goals_challenger.py` | Research experiment receipt created with `certified_calibrated_probability=false` and a null calibrated lower bound. | None |
| `PERSISTED` | Receipt persistence | `v17/nhl_pregame_orchestration.py` | Collected starter receipts were appended to the immutable receipt table before the gate ran. Evidence storage only. | None |
| `NO_RECEIPTS` | Receipt persistence | `v17/nhl_pregame_orchestration.py` | No receipt survived collection or attestation, so nothing was persisted and the gate proceeds on missing evidence. | None |
