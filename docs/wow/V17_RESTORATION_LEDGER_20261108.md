# WOW V17 Restoration Ledger — 2026-11-08

Work order: `WOW-V17-FREEBUFF-RESTORATION-001`
Engineering session: Freebuff, branch `fix/prop-evidence-fallback-canonical-identity-20261108`
Terminal authority: `V17_TERMINAL_REDUCER` — `can_execute=false`

This ledger records only what was directly inspected or executed in this session.
Items sourced from the work order are labelled `REPORTED`, not `VERIFIED`.
No production credential, production deployment, model certification, or
independent acceptance was available or claimed.

## Baseline reconciliation

| Item | Observed state | Evidence |
|---|---|---|
| Work-order main SHA | `12d8e65e3f5acc178074209ebaab689c2c073e2b` | Work order; now an ancestor of live main |
| Live `origin/main` at session start | `db5e6b7c8e97079dc52ede0e36cc2b0e6e5bbc8c` | authenticated `git fetch`; merge commit of PR #2523 |
| Handoff staleness | Main moved past the handoff baseline; PR #2523 is already merged | `git log origin/main` |
| PR #2504 | Open, head `a5315bab1aa980e698661510e0679852a63e3172`, `mergeable_state=dirty`, 39 files, 28 review comments | GitHub integration read |
| PR #2512 | Open, head `e86710587512e2f4a5af48196bf65b185782f944`, 4 files | GitHub integration read |
| PR #2522 | Open, head `27a32a54f8ecc33a386059cabe46321a54e02262`, 5 docs/test files for Action origin and gateway boundaries | GitHub integration read |
| PR #2523 | Merged into main (`db5e6b7c8`) | `git log origin/main` |
| PR #2526 (session 2 start) | Open draft, head `e6ddf3d3e66eeecf3a287ed9816ed0f5e526eecd`, 3 files | GitHub integration read |

PR #2504's own body states it is a Class C candidate, not certified or merged,
with zero persisted NBA `SCHEDULED` source projections, `timestamp_attestation=UNVERIFIED`,
missing NBA specialist/lower-bound certification, and no independent SIRT
acceptance. It must not be treated as NBA restoration.

## Objective ledger

| Objective | Status | Root cause / exact blocker | Existing PR | Branch / commit | Verification | Remaining blocker | Next action |
|---|---|---|---|---|---|---|---|
| A1 Production environment identity | BLOCKED | No Render or production Supabase credentials/readback available in this environment; connected Supabase project is the validation project, not production | — | — | Read-only repo inspection only | Production access / owner-provided credentials | Bind production secrets and prove web+worker deployed SHA and database project |
| A2 Action-to-backend evidence ingestion | WORKING_NOT_COMPLETE | Fallback acquisition used raw caller identity. Session 2 reconciled it to the actual writer: stripped `event_id`, uppercase sport, whitespace-collapsed player, alias-resolved canonical stat, and revalidation of the fallback copy through the original RPC. Event/line/freshness/hydration/identity checks unchanged | [#2526](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2526) (draft) | `fix/prop-evidence-fallback-canonical-identity-20261108` | 123 focused/adjacent tests pass, 15 integration-gated skips; Postgres end-to-end test added to `wow-engine-verify` | The reported live 422 cannot be attributed without production readback; `PROP_EVIDENCE_SNAPSHOT_NOT_FOUND` also legitimately means no snapshot row exists | Run the production Action and query the evidence ledger read-only |
| A3 Worker processing | BLOCKED | No production queue/worker access; queue admission was not accepted as scoring | — | — | None | Production Render worker + DB access | Prove claim, specialist invocation, retry idempotency and durable receipt |
| A4 Immutable receipts | BLOCKED | Depends on A1–A3 and a certified specialist | — | — | None | Production evidence | Verify receipt fields end-to-end |
| B NBA restoration | BLOCKED | `CERTIFIED_TEAM_EVENT_SPORTS` contains only MLB; `TEAM_EVENT_BRIDGES` registers MLB and NFL at bootstrap; `nba_current_pregame_scorer.py` is explicitly unregistered and stops at `NBA_DYNAMIC_BOUNDS_POLICY_NOT_APPROVED`; no certified NBA artifact or current-season data was proven | #2504 (conflicted, uncertified) | — | Code inspection | Certified fitted artifact, calibration, current pregame data, independent acceptance | Do not register an uncertified seam; complete artifact promotion and certification first |
| C NFL restoration | WORKING_NOT_COMPLETE | Artifact/row publishability provenance defect is the subject of open PR #2512; forward evidence is 81/100 grades per work order | #2512 | — | PR body read; not independently re-run | 19 genuine forward grades, independent certification, exact-head review | Review and accept #2512 on its exact head; do not flip flags |
| D NHL restoration | BLOCKED | Registered candidate path is not a certified operational model; goalie/rest/Elo/PP/PK/calibration inputs and an active certification were not proven | — | — | Repo inspection | Certified artifact + calibration + full evidence hydration | Keep typed `MODEL_INPUTS_INSUFFICIENT` until real evidence exists |
| E NCAAF restoration | BLOCKED | No fitted NCAAF artifact, feature rows, calibrator, or predictions were proven; discovery returned zero events | — | — | Repo inspection | Historical feature pipeline, reproducible fit, calibration, discovery diagnosis | Build pipeline only from validated historical source data |
| F Cross-sport discovery | UNPROVEN | `rundown_sport_registry` does include NCAAF id 1 and NFL id 2, so a missing provider id is not supported by code inspection; zero-event results need live diagnostics | — | — | Code inspection | Live provider/acquisition receipts | Diagnose date/timezone/feed coverage on production evidence |
| G Engineering autonomy | UNVERIFIED | Workflow status counts are not execution receipts; no dispatch or lease proof was inspected | — | — | None | GitHub Actions and production queue access | Prove one bounded repair dispatched and receipted |
| H Repository governance | BLOCKED | Protected merge and independent QA/Release Authority are external authorities; engineering cannot self-certify | #2504, #2512, #2522, #2523 | — | PR metadata read | Authorized independent review and merge | Complete exact-head review on each PR; never merge on stale green |
| I Model calibration/learning | SAFE_HOLD | No model weight, calibration coefficient, threshold, or bound was changed | — | — | Diff review | Genuine forward cohorts | Preserve baseline; no isolated-outcome tuning |

## Code change in this session

| Field | Value |
|---|---|
| Objective | A2 — governed prop evidence fallback identity |
| Files | `artifacts/wow-engine/prop_evidence_repair.py`, `artifacts/wow-engine/tests/test_prop_evidence_repair_identity.py`, `artifacts/wow-engine/test_prop_evidence_repair_postgres.py`, `.github/workflows/wow-engine-verify.yml` |
| Branch | `fix/prop-evidence-fallback-canonical-identity-20261108` |
| PR | [#2526](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2526) (draft; no merge/certification claimed) |
| Commit | code `21f930b608ba0a568df55a8828ee81354df1f102`; ledger `e6a51ddd129af21e764745c7da83060af59e539d` |
| Defect | The primary `wow_prop_evidence_snapshot` RPC compares sport/stat case-insensitively but event id and player exactly. The fallback locator queried the table with raw caller strings, while `pick_request_runtime_core._validate_evidence` / `_snapshot_payload` persist stripped `event_id`, uppercase `sport`, whitespace-collapsed `player`, and the alias-resolved canonical `stat_type`. A padded/lowercase/alias caller could therefore miss the row it wrote and be reported as `PROP_EVIDENCE_SNAPSHOT_NOT_FOUND`. |
| Change | `_canonical_identity()` now mirrors the writer, reuses the live `_canonical_stat` alias map, covers the trimmed event id, and supplies the same canonical keys to the fallback revalidation copy (the caller's request object is not mutated). Event/line identity, `captured_at < event_start_time`, `hydration_status='PASS'`, the primary RPC, calibration, publication, and execution gates are unchanged. |
| Caller audit | 5 callers use `repair_prop_evidence`. `score_prop` and `calibration_publication_api` consume `effective_source_snapshot_id`; the NBA scalar and WNBA composite bridges are research-only; the fantasy forward-capture bridge keys on the requested frozen snapshot and holds with `HELD_SOURCE_SNAPSHOT_NOT_FOUND` if a different fallback was used, consistent with `PROP_RESEARCH_FROZEN_SNAPSHOT_MISMATCH`. |
| Tests | `.venv/bin/python -m pytest -q tests/test_prop_evidence_repair_identity.py test_pick_request_publication_research.py test_pick_request_runtime.py test_v17_nfl_prop_canonical_identity_proof.py v17/test_fantasy_score_candidate_bridge.py v17/test_fantasy_score_forward_cohort_runtime.py v17/test_mlb_pitcher_fantasy_score_forward_loop.py test_prop_snapshot_min3_postgres.py` → **123 passed, 15 skipped** (Postgres tests require `WOW_AGENT_RUNTIME_INTEGRATION=1`) |
| Integration coverage | `test_prop_evidence_repair_postgres.py` runs the real `wow_prop_evidence_snapshot` function against the real evidence table in an ephemeral schema: missing requested snapshot → canonical fallback → RPC revalidation → exact event/player/sport/stat/line; pregame, hydration, identity and incomplete-evidence violations fail closed; row count proves no fabrication. Wired into `wow-engine-verify`. |
| CI | Exact-head run requested after push; result recorded below |
| Independent QA | Not obtained |
| Deployment | None |
| Production verification | None |
| Rollback | Revert `21f930b6`; the change is isolated to the fallback query and has no data migration |
| Remaining blocker | A production Action call plus read-only evidence-ledger inspection is required to prove the live 422 cause |

## Session 2 — PR #2526 completion

- Corrected the regression that asserted a padded event id was preserved. The
  writer strips it, so the fallback lookup now asserts the trimmed value.
- The writer-contract test derives the persisted snapshot with the real
  `_validate_evidence` / `_snapshot_payload` functions and asserts that
  `_canonical_identity` returns exactly the persisted event id, sport, player and
  stat type, including the live alias map (`pass-yards` → `PASSING_YARDS`).
- Full-loop unit coverage: requested snapshot absent → canonical fallback found →
  revalidated through the injected original validator → exact identity asserted;
  a fallback the validator rejects is not upgraded; lookup failure preserves the
  original blocker; non-repairable primary failures never query the ledger.
- Real-Postgres coverage as described above. It is the semantic acceptance
  evidence for the fallback path and is skipped locally without the CI service.
- `can_execute=false` remains enforced by the repair annotation and by every
  assertion. No model, calibration, bound, threshold or terminal authority changed.

Still not claimed: the live 422 cause, production evidence, deployment,
independent semantic acceptance, or merge. Those require production readback and
an external reviewer respectively.

## Escalation blockers

`BLOCKER-001`
`EXACT_FAILURE`: No production Render/Supabase credentials or readback in this environment.
`EVIDENCE`: Sandbox has no Render token or production database URL; only a validation Supabase integration is connected.
`AFFECTED_WORKSTREAM`: A1, A3, A4, and production acceptance for all sports.
`OWNER_REQUIRED`: Repository owner / release authority.
`MINIMUM_ACTION_NEEDED`: Provide production read access (or run the documented readback) for web/worker SHA, database project, and evidence/receipt rows.
`WORKAROUND_ATTEMPTED`: Repo/build inspection and validation of the shared contract only.
`OTHER_WORK_CONTINUING`: The A2 identity repair and its regression test.

`BLOCKER-002`
`EXACT_FAILURE`: Independent certification and protected release authority are absent.
`EVIDENCE`: PR #2504 body states no independent SIRT acceptance; PR #2512 body requires exact-head independent QA/Release App checks.
`AFFECTED_WORKSTREAM`: B, C, D, E, H.
`OWNER_REQUIRED`: SIRT / Independent Verification / Release Authority.
`MINIMUM_ACTION_NEEDED`: Independent exact-head semantic review and authorized merge.
`WORKAROUND_ATTEMPTED`: None; self-certification is prohibited.
`OTHER_WORK_CONTINUING`: Evidence and CI preparation only.

`BLOCKER-003`
`EXACT_FAILURE`: NBA has no proven certified active/promoted artifact or current-season data, and PR #2504 is conflicted.
`EVIDENCE`: `CERTIFIED_TEAM_EVENT_SPORTS` contains only MLB; `nba_current_pregame_scorer.py` refuses to publish without certification and dynamic bounds policy.
`AFFECTED_WORKSTREAM`: B.
`OWNER_REQUIRED`: Model governance + SIRT.
`MINIMUM_ACTION_NEEDED`: Certified artifact/calibration and real pregame evidence.
`WORKAROUND_ATTEMPTED`: Code-path inspection; no uncertified registration was created.
`OTHER_WORK_CONTINUING`: A, C review, F diagnosis.

`BLOCKER-004`
`EXACT_FAILURE`: NFL forward cohort is 19 genuine grades short of the existing 100-grade target.
`EVIDENCE`: Work-order record; PR #2512 body confirms 81/100 and excludes backfill.
`AFFECTED_WORKSTREAM`: C.
`OWNER_REQUIRED`: Time and genuine pregame forecasts.
`MINIMUM_ACTION_NEEDED`: Accumulate genuine forward grades.
`WORKAROUND_ATTEMPTED`: None; backfill is prohibited.
`OTHER_WORK_CONTINUING`: A, C code provenance.

`BLOCKER-005`
`EXACT_FAILURE`: NHL and NCAAF operational models are not proven; required evidence and calibration artifacts are absent from the verified inventory.
`EVIDENCE`: Work-order inventory and `all_sport_capability_readiness` / bridge registry inspection.
`AFFECTED_WORKSTREAM`: D, E.
`OWNER_REQUIRED`: Model recovery + data engineering.
`MINIMUM_ACTION_NEEDED`: Real hydration, reproducible fitting, calibration, and certification.
`WORKAROUND_ATTEMPTED`: None; manufacturing evidence is prohibited.
`OTHER_WORK_CONTINUING`: A.

`BLOCKER-006`
`EXACT_FAILURE`: Live Custom GPT Action parity cannot be verified from the repository.
`EVIDENCE`: Editor sync documents state `RESYNC_REQUIRED_AFTER_PR766__LIVE_ACTION_ACCEPTANCE_REQUIRED`; PR #2522 documents gateway boundaries but cannot prove the editor.
`AFFECTED_WORKSTREAM`: A2, A5.
`OWNER_REQUIRED`: Custom GPT editor owner.
`MINIMUM_ACTION_NEEDED`: Save/reload the canonical Action schema and run a fresh production chat.
`WORKAROUND_ATTEMPTED`: None.
`OTHER_WORK_CONTINUING`: Code and contract repair.

## Honest completion statement

The overall restoration is **WORKING_NOT_COMPLETE**. No sports model was
certified, no protected merge was performed, no deployment was made, and no
production scoring receipt was verified. The only executed engineering result is
the bounded A2 fallback-identity repair with 4 passing focused tests. Everything
else above remains blocked on production access, real model evidence, or
independent authority — none of which can be produced by this environment.
