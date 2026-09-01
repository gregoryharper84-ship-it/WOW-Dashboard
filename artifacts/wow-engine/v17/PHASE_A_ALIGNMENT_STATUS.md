# WOW v17 Phase A — Alignment Status

As of: 2026-08-31 (original Phase-A audit); converged onto current `main` on 2026-09-01.

This is a candidate/shadow status record. WOW v17 is not active and production remains governed by WOW v16 Clean Core.

## 2026-09-01 convergence pass

The original Phase-A branch (`chatgpt/v17-phase-a-custom-engine-alignment-20260831`,
head `3d2609037d2075f7968bc77c31cdd1798790a373`) was cut from `main@4ef3940` and never
merged (PR #95 remains draft). Between that base and today, `main` advanced 25
commits unrelated to v17 — the mandatory Scout/Research runtime barrier, Agent
Runtime worker-namespaced idempotency, MLB calibration-health/prospective-specialist
reconciliation, and the NFL moneyline P0/P1 fail-closed contracts — while the
Phase-A branch itself advanced 57 commits of its own that never saw those changes.
Rather than merge or rebase the stale branch, this candidate was reconstructed by
copying the 16 Phase-A files unmodified onto current `main` (`da33cbe4739b98bd0975d9a665dfb620ccca7190`)
on a fresh branch. No source line in `v17/host_routing.py`,
`v17/team_event_request_runtime.py`, or `api_v17_candidate.py` needed to change to
import cleanly against today's `api_ncaaf_acceptance:app` / `api_g11` — all 32
Phase-A tests pass unmodified, and the full `artifacts/wow-engine` suite
(601 passed, 3 skipped, 0 failed) shows no regression from adding them.

```text
RENDER_REPOSITORY_SHA_PARITY = PASS (reverified 2026-09-01)
  main = da33cbe4739b98bd0975d9a665dfb620ccca7190
  render live deploy (dep-dabdhrajobas73c5umjg, status=live) = da33cbe4739b98bd0975d9a665dfb620ccca7190
```

Two open items surfaced during convergence that this pass does **not** resolve,
because resolving them would mean choosing governance/probability semantics that
are not this pass's to choose:

```text
OPEN_01_SCOUT_RESEARCH_BARRIER_SCOPE
  The mandatory Scout -> Research barrier added since Phase-A
  (agent_runtime/coordinator_scout_research.py) is enforced only inside the
  async Agent Runtime durable-job pipeline (discovery -> scout -> research ->
  evidence -> controlling specialist). The synchronous POST /score-event
  bridge in api_g11.py that this v17 candidate's /score-team-event reuses for
  MLB does not go through that barrier -- it calls _bridge_rpc() directly and
  fails closed on its own ratification/ceiling gates. This predates Phase-A
  and is not a regression introduced here, but the September 1 architecture
  decision (see repo handoff) explicitly requires Scout/Research ahead of
  "individual ML requests" too, in focused-scout mode. Whether /score-event
  itself must gain a Scout/Research precondition, or whether its existing
  independent ratification gate is an accepted equivalent, is a governance
  decision for independent review, not an implementation default.

OPEN_02_V17_BRIDGE_FUNCTIONS_LIVE_BUT_UNMIGRATED
  v17/sql/20260831_v17_mlb_event_governance_bridge.sql defines
  wow_v17_team_failure_path_gate() and wow_v17_mlb_team_event_governance_bridge(),
  and the latter calls public.wow_run_event_postmodel_gates(...) and
  public.wow_run_event_final_gates(...) as pre-existing functions. Those two
  functions do not exist in any migration file in this repository. Querying
  the Supabase project "wow-engine-validation" (iczfhsmjrrafhvcpmqhr), which
  also holds wow_event_predictions, wow_runtime_capabilities, and the
  wow_nfl_* P1 tables, confirms all four v17 bridge functions already exist
  live there. The project "gregory.harper84@gmail.com's Project"
  (zchzcveqqemuwypifqyx) has none of this schema. This repo could not
  independently confirm which project backs the live Render service's
  DATABASE_URL (no read access to service environment variables), so treat
  "wow-engine-validation" as the likely but not certificate-grade-verified
  backing store. Either way, these two prerequisite functions and the v17
  bridge functions itself are schema drift: applied to a live database but
  never captured as a checked-in migration. That is a
  DATABASE_SCHEMA_BOOTSTRAP_GAP (per this repo's failure classification), not
  a design defect -- the Python bridge caller fails closed to
  LLP_EVENT_GOVERNANCE_NOT_PROVEN if the RPC is ever actually missing.
```

Nothing above changes any gate, probability, or ceiling. Both are flagged for
independent review per the Phase-A certification sequence, not decided here.

## A1/A2 remediation — 2026-09-01

Per explicit governance instruction, both open findings above were resolved
(not merely flagged) on this branch, and are pending independent review
rather than self-certified as final.

```text
A1_SCOUT_RESEARCH_BARRIER = ENFORCED
  v17/team_event_request_runtime.py now runs
    request -> Scout -> Research -> controlling sport specialist
  ahead of any event_api.score_event call. Implemented by driving
  agent_runtime.runner_scout_research.execute_envelope synchronously,
  in-process, for a single ad-hoc candidate -- the exact same pure worker
  handlers the durable Agent Runtime coordinator dispatches through Celery
  for full-slate/prop runs. No second Scout/Research implementation was
  created. Scout/Research remain evidence-acquisition only: authority
  leakage is independently rejected inside execute_envelope itself
  (validate_non_predictive_output), not by anything added here. A BLOCKED
  stage raises SCOUT_RESEARCH_BARRIER_BLOCKED (409) before the specialist is
  ever called; proven by test_mandatory_scout_research_barrier_blocks_specialist_when_a_stage_fails
  and the global-scout equivalent (both assert score_event was never
  invoked). Unsupported sports still fail closed to MODEL_UNAVAILABLE; the
  barrier runs ahead of the sport dispatch so future specialists inherit it
  without additional wiring.

A2_EVENT_GATE_FUNCTIONS_MIGRATION = CAPTURED
  artifacts/wow-engine/migrations/20260901_event_terminal_gate_functions_capture.sql
  records the exact live source (via pg_get_functiondef against the
  "wow-engine-validation" Supabase project, iczfhsmjrrafhvcpmqhr) of
  wow_run_event_postmodel_gates, wow_run_event_final_gates, and the seven
  functions they call (wow_audit_event_probability_card,
  wow_assess_event_calibration_health, wow_apply_event_decision_governor,
  wow_evaluate_event_rank_eligibility, wow_reduce_event_terminal_label,
  wow_run_event_final_refresh, wow_mark_event_publishable), plus the
  anon/authenticated execute revokes already in effect live. Verified by
  applying the migration inside a BEGIN/ROLLBACK transaction against that
  project: it executed with no errors and was then rolled back, so
  production/live behavior is unchanged and nothing was actually applied.
  Confirmed no other repository migration defines these names (no
  conflict). NOT resolved, and out of scope for this pass: the four
  underlying tables these functions read/write (wow_event_predictions,
  wow_calibrators, wow_event_evidence, wow_event_scoring_evidence) also
  predate every migration file in this repository -- the same
  DATABASE_SCHEMA_BOOTSTRAP_GAP category, called out again as a remaining
  blocker below rather than backfilled from introspection.
```

Updated blocker matrix (supersedes the B01-B05 list above only where noted):

```text
B01 WOW live Custom GPT editor configuration -- still not re-attested.
B02 LLP live Custom GPT editor -- still requires post-contract re-attestation.
B03 Candidate CI and shadow-service acceptance -- still pending.
B04 LLP direct-vendor Actions reclassification -- still open.
B05 Independent implementation review -- still required, now covering this
    A1/A2 remediation in addition to the original Phase-A candidate.
B06 (NEW) wow_event_predictions / wow_calibrators / wow_event_evidence /
    wow_event_scoring_evidence table DDL is still live-only, not captured
    as a migration. A2 captured the function layer only.
```

```text
V17_CUTOVER_ALLOWED = false
CAN_EXECUTE = false
```

## Resolved in this Phase-A branch

```text
RENDER_REPOSITORY_SHA_PARITY = PASS
  main/live = 4ef39405702ce38682b28733f767206ebf28a2d5

CUSTOM_ENGINE_LANE_OWNERSHIP = IMPLEMENTED_CANDIDATE
  PLAYER_PROP -> WOW_BETTING_ENGINE
  TEAM_EVENT / MONEYLINE / OUTRIGHT_WINNER / UPSET -> LLP_TEAM_BETTING_ENGINE

REQUESTER_HOST_VS_CONTROLLING_ENGINE_SEPARATION = IMPLEMENTED_CANDIDATE
  WOW or PROJECT_CHAT may originate a team/event request without becoming the
  controlling team model; the backend resolves LLP_TEAM_BETTING_ENGINE.

HOST_LOCAL_TERMINAL_AUTHORITY = FALSE
GLOBAL_TERMINAL_AUTHORITY = V17_TERMINAL_REDUCER

GENERIC_TEAM_EVENT_INGRESS = IMPLEMENTED_CANDIDATE
  POST /score-team-event
  MLB -> existing governed MLB event adapter
  unsupported sports -> MODEL_UNAVAILABLE, fail closed

V17_CANDIDATE_APP_ISOLATION = IMPLEMENTED
  api_v17_candidate:app is distinct from api_ncaaf_acceptance:app
  importing candidate code does not add v17 routes to the accepted v16 app
  accepted v16 startup/shutdown hooks are preserved

RECOMMENDATION_LEDGER_ROUTE_PARITY = IMPLEMENTED_CANDIDATE
  /record-recommendations
  /settle-recommendations
  mounted from existing audited ledger implementation
  candidate served routes now match candidate Action responsibilities

CANONICAL_WOW_ACTION_SCHEMA = PREPARED_CANDIDATE
CANONICAL_LLP_ACTION_SCHEMA = PREPARED_CANDIDATE
  same eventual governed Render origin
  same bearer auth family
  LLP schema contains no prop-scoring route
  WOW schema delegates team/event requests to LLP controlling engine
  both schemas are CANDIDATE ONLY until the approved target backend serves them

CUSTOM_GPT_EDITOR_ATTESTATION_PACKETS = PREPARED
  WOW_BETTING_ENGINE_EDITOR_ATTESTATION.md
  LLP_TEAM_BETTING_ENGINE_EDITOR_ATTESTATION.md

LEGACY_REPLIT_PRIMARY_ROUTING_ALLOWED = FALSE
CAN_EXECUTE = FALSE
```

## Still blocking v17 cutover

```text
B01 WOW live Custom GPT editor configuration has not been directly re-attested.
B02 LLP live Custom GPT editor requires post-contract re-attestation.
B03 Candidate CI and separate shadow-service acceptance are pending.
B04 LLP direct-vendor Actions must be reclassified/proven EVIDENCE_ONLY or removed;
    prior auth gaps must not be assumed fixed.
B05 Independent review of the implementation branch is still required before merge.
```

## Unsupported sports behavior

A cross-sport ingress does not imply a fitted model exists for every sport. Unsupported team/event sports terminate as `MODEL_UNAVAILABLE` until a certified adapter/model is registered. No market-implied or qualitative fallback is permitted.

## Certification sequence

```text
1. Candidate CI
2. Separate Render candidate shadow deployment
3. Candidate route/health/host-contract acceptance
4. Independent code review
5. WOW Custom GPT editor attestation
6. LLP Custom GPT editor attestation + vendor Action cleanup proof
7. Merge Phase-A only after review passes
8. Keep V17_CUTOVER_ALLOWED=false until final migration approval
```
