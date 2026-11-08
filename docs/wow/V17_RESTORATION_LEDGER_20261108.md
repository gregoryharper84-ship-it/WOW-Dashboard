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
| A1 Production environment identity | BLOCKED | No authorized production write/read credentials in this environment; connected Supabase project is the validation project, not production. Public read-only `/health` now reports `deployed_sha=db5e6b7c`, `RENDER`/`SUPABASE`, `can_execute=false`; worker revision, database project identity, Action request and receipt row remain unproven | — | — | Public production `/health` and `/governance` readback (2026-11-08) | Authorized production readback for web and worker revisions, database binding, Action request, evidence lookup and receipt | Do not request secrets in chat; owner binds read-only production access or runs the documented readback |
| A2 Action-to-backend evidence ingestion | WORKING_NOT_COMPLETE | Fallback acquisition used raw caller identity. Session 2 reconciled lookup and revalidation to the actual writer (stripped `event_id`, uppercase sport, whitespace-collapsed player, alias-resolved canonical stat) and added a read-only evidence readback plus receipt audit. The reported 422 pair was `SELF_ACCEPTANCE`, not a live Action call | [#2526](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/pull/2526) (draft at session start; ready for review after exact-head green) | `fix/prop-evidence-fallback-canonical-identity-20261108` / `f6ab9e14` + `0f93f596` (ledger commit follows) | Canonical repair, readback and adjacent suites **140 passed, 23 integration-gated skips**; real-Postgres integration file **8 passed**; exact head `0f93f596` all checks **SUCCESS** including durable Agent Runtime integration and governed probability backend | Production readback/deployment not executed; live Action parity, production request id and receipt remain unproven; CodeRabbit review pending ready-for-review | Deploy the readback and run it with a production request id/identity |
| A3 Worker processing | BLOCKED | No production queue/worker access; queue admission was not accepted as scoring | — | — | None | Production Render worker + DB access | Prove claim, specialist invocation, retry idempotency and durable receipt |
| A4 Immutable receipts | BLOCKED | Depends on A1–A3 and a certified specialist | — | — | None | Production evidence | Verify receipt fields end-to-end |
| B NBA restoration | BLOCKED | `CERTIFIED_TEAM_EVENT_SPORTS` contains only MLB; `TEAM_EVENT_BRIDGES` registers MLB and NFL at bootstrap; `nba_current_pregame_scorer.py` is explicitly unregistered and stops at `NBA_DYNAMIC_BOUNDS_POLICY_NOT_APPROVED`; no certified NBA artifact or current-season data was proven | #2504 (conflict resolved; still uncertified) | `integration/nba-2472-onto-2502` / `f50cc5de` | Merged protected main `db5e6b7c`; only `wow-engine-verify.yml` conflicted, resolved by union of main's live-SHA job and both NBA real-Postgres jobs; YAML validated; PR CI pending | Certified fitted artifact, calibration, current pregame data, independent acceptance | Do not register an uncertified seam; complete artifact promotion and certification first |
| C NFL restoration | WORKING_NOT_COMPLETE | Artifact/row publishability provenance defect is the subject of open PR #2512; forward evidence is 81/100 grades per work order | #2512 | `engineering/p0-1896-nfl-row-provenance-20261107` / `e8671058` | Exact head `e8671058` all checks **SUCCESS**, including `WOW Independent QA exact-head` and `WOW Release Authority exact-head`; CodeRabbit and testdriver findings marked addressed in `2259fdd..e8671058`; local provenance and Pick'em suites **49 passed** | 19 genuine forward grades, authorized protected merge, and semantic SIRT signoff (App checks are deterministic, not a SIRT verdict) | Preserve the verified head; do not rewrite flags or manufacture grades |
| D NHL restoration | BLOCKED | Registered candidate path is not a certified operational model; goalie/rest/Elo/PP/PK/calibration inputs and an active certification were not proven | — | — | Repo inspection | Certified artifact + calibration + full evidence hydration | Keep typed `MODEL_INPUTS_INSUFFICIENT` until real evidence exists |
| E NCAAF restoration | BLOCKED | No fitted NCAAF artifact, feature rows, calibrator, or predictions were proven; discovery returned zero events | — | — | Repo inspection | Historical feature pipeline, reproducible fit, calibration, discovery diagnosis | Build pipeline only from validated historical source data |
| F Cross-sport discovery | UNPROVEN | `rundown_sport_registry` does include NCAAF id 1 and NFL id 2, so a missing provider id is not supported by code inspection; zero-event results need live diagnostics | — | — | Code inspection | Live provider/acquisition receipts | Diagnose date/timezone/feed coverage on production evidence |
| G Engineering autonomy | DISPATCHED_BLOCKED | The canonical provider dispatcher is disabled at repository level, so a `workflow_dispatch` there returned 422. The existing ChatGPT worker accepted a manual recovery dispatch for #1688 / `P0_AUTONOMY_RESTORE` at `main` `db5e6b7c`, but failed at `Recheck openai provider breaker under the global writer mutex` before any implementation step. Auto-repair run `42486632353` completed with no eligible HOLD target | — | `main` `db5e6b7c`; no worker branch created | ChatGPT worker run `42487106043`; job failed before Engineering Lead/implementation; no tests ran | OpenAI provider breaker is open; canonical dispatcher is disabled | Reset/recover the breaker and enable the existing dispatcher; then re-dispatch the same bounded objective |
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
| Change | `_canonical_identity()` now mirrors the writer, reuses the live `_canonical_stat` alias map, covers the trimmed event id, and supplies the same canonical keys to the fallback revalidation copy (the caller's request object is not mutated). Event/line identity, `captured_at < event_start_time`, `hydration_status='PASS'`, the primary RPC, calibration, publication, and execution gates are unchanged. A separate read-only readback route captures request/terminal identity without executing scoring. |
| Caller audit | 5 callers use `repair_prop_evidence`. `score_prop` and `calibration_publication_api` consume `effective_source_snapshot_id`; the NBA scalar and WNBA composite bridges are research-only; the fantasy forward-capture bridge keys on the requested frozen snapshot and holds with `HELD_SOURCE_SNAPSHOT_NOT_FOUND` if a different fallback was used, consistent with `PROP_RESEARCH_FROZEN_SNAPSHOT_MISMATCH`. |
| Tests | Canonical repair, readback, acceptance and adjacent suites → **140 passed, 23 skipped** (Postgres tests require `WOW_AGENT_RUNTIME_INTEGRATION=1`). |
| Integration coverage | `test_prop_evidence_repair_postgres.py` runs the real `wow_prop_evidence_snapshot` function against the real evidence table in an ephemeral schema: missing requested snapshot → canonical fallback → RPC revalidation → exact event/player/sport/stat/line; pregame, hydration, identity and incomplete-evidence violations fail closed; row count proves no fabrication. Wired into `wow-engine-verify`. |
| Readback commit | `8c9217af001674d0e4aa4ee5a8ea48084b9df5e0` |
| CI | Exact-head run requested after push; result recorded below |
| Independent QA | Not obtained |
| Deployment | None |
| Production verification | None. The hidden readback route was exercised locally: unauthenticated 401, authenticated 200, `EXPECTED_SELF_ACCEPTANCE_PROBE`, `can_execute=false`. No production database or Render service was reached. |
| Rollback | Revert `8c9217af` and `21f930b6`; both changes are isolated and have no data migration. |
| Remaining blocker | Production credentials/deployment and a live Action request id are required to exercise the readback against production; the validation receipt audit already disproves the reported pair as a live Action failure. |

## PROP_EVIDENCE_SNAPSHOT_NOT_FOUND attribution result (2026-11-08)

Read-only query of the connected **validation** ledger
`iczfhsmjrrafhvcpmqhr` (not the production database):

- 4,089 `wow_action_invocation_receipts`; 2,256 are `/score-prop`.
- The 20 most recent `/score-prop` 422s all have
  `caller_class=SELF_ACCEPTANCE` and
  `validation_code=PROP_EVIDENCE_SNAPSHOT_NOT_FOUND`.
- The pair at `2026-11-08 05:37:03.835693Z`
  (`d649a460-5888-48b4-835b-5a454c762309`) and
  `2026-11-08 05:37:07.145400Z`
  (`b4ed2527-c112-4b31-85c5-a62ca34555d6`) is the MORE/LESS pair from
  `api_prod_market_acceptance._run_prop_live_self_acceptance`.
- That probe deliberately uses a random `source_snapshot_id`, event id
  `WOW:PROP:LIVE:SELF_ACCEPTANCE:NO_EVIDENCE`, and
  `X-WOW-Caller-Class: SELF_ACCEPTANCE`. Its `_validate_probe_response`
  requires exactly 422 / `PROP_EVIDENCE_SNAPSHOT_NOT_FOUND` /
  `specialist_invoked=false` / no numeric probability fields.

Readback result for both actual receipts:
`reported_failure_attribution=EXPECTED_SELF_ACCEPTANCE_PROBE`,
`reported_cause_supported=false`,
`reported_cause_basis=SELF_ACCEPTANCE_PROBE_EXPECTS_422`,
`can_execute=false`.

Conclusion: **the reported cause is disproved as a live Action/scoring
evidence failure** for the recorded pair. The 422 is the expected fail-closed
response to a deliberately nonexistent snapshot id. This does not prove an
Action call cannot hit a real evidence gap; it proves these receipts are not
that call. Production receipts were not available, so the production half
remains unproven.

### Readback path

- `prop_evidence_readback.py` exposes `diagnose_prop_evidence`,
  `identity_fingerprint`, `classify_evidence_state`, and
  `log_prop_evidence_failure`.
- `POST /v17/prop-evidence-readback` is authentication-gated and
  `include_in_schema=False`.
- CLI: `python -m prop_evidence_readback --request-json @request.json` or
  `--request-id <id>`.
- Classifications: `EVIDENCE_READY`, `EVIDENCE_NOT_READY`,
  `SNAPSHOT_ROW_MISSING`, `SNAPSHOT_EXISTS_UNDER_OTHER_ID`,
  `SNAPSHOT_ID_POINTS_TO_OTHER_IDENTITY`, `SNAPSHOT_ROW_MISSING_LOOKUP_PARTIAL`,
  `PRIMARY_LOOKUP_INFRA_UNAVAILABLE`, `PRIMARY_LOOKUP_TYPED_FAILURE`.
- The failure boundary logs
  `WOW_V17_PROP_EVIDENCE_FAILURE identity_fingerprint=... event_id=...
  sport=... stat_type=... line=... source_snapshot_id=... request_id=...
  caller_class=... code=... can_execute=false`; player names are excluded.
- The endpoint returns no player names, game logs, box scores, or
  probabilities.

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

Still not claimed: the live 422 cause, the production Action request or receipt,
deployment, independent semantic acceptance, or merge. Those require authorized
production readback and an external reviewer respectively.

### Session 2 continuation — exact-head correction and cross-PR reconciliation

- CI failure at `5c49ab11`: the durable Agent Runtime integration job failed
  because the new Postgres test asserted the caller's `stat_type` became
  `pass-yds`. The repair intentionally leaves the caller request untouched
  (`model_copy` is applied only to the fallback), so the assertion contradicted
  its own non-mutation guarantee. Corrected to `pass-yards` in `0f93f596`.
- Reproduced the entire integration file against a real PostgreSQL 16 in this
  sandbox with the same base DDL and migrations in an ephemeral schema:
  **8 passed**, including requested-snapshot-missing → canonical fallback → RPC
  revalidation and every fail-closed freshness/hydration/identity case. The
  disposable cluster was destroyed; no project or production database was
  touched.
- Exact head `0f93f596` rollup: **SUCCESS** for CodeQL, change-impact gate, rapid
  regression, governed backend shards 0–3, durable Agent Runtime integration,
  release contracts, additional shards, required-three regression and governed
  probability backend. CodeRabbit was skipped only because the PR was still a
  draft at that head; ready-for-review is requested after this ledger commit so
  the review covers the final head.
- #2512 exact head `e8671058`: all checks **SUCCESS**, including
  `WOW Independent QA exact-head` and `WOW Release Authority exact-head`.
  CodeRabbit's provenance finding is marked addressed in
  `2259fdd..e8671058`; the testdriver duplicate describes the same defect. The
  local docstring-only commit was deliberately not pushed: it would replace a
  verified head with one carrying no new engineering content. The 19 genuine
  forward grades and the authorized protected merge remain external.
- #2504: merged protected main `db5e6b7c` into the NBA candidate. The only
  conflict was `wow-engine-verify.yml`; the resolution keeps main's live-SHA
  ancestry observation and both NBA real-Postgres contract jobs, with all five
  governed-backend prerequisites unchanged. Pushed as `f50cc5de`. Exact head
  `f50cc5de` is fully **SUCCESS**, including CodeRabbit, both NBA real-Postgres
  contracts, `WOW governed probability backend`, `WOW Independent QA exact-head`
  and `WOW Release Authority exact-head`. This proves the conflict resolution and
  the exact-head gates only; the PR remains an uncertified Class C candidate with
  no certified NBA artifact, calibration, or current-season pregame corpus.
- NHL and NCAAF maintenance lanes: their fail-closed contract suites pass
  (**44 passed**), but neither proves a certified operational model or real
  historical training corpus. No generic output or training row was created.
- Production readback via public `/health` and `/governance`: deployed SHA
  `db5e6b7c`, RENDER/SUPABASE, `can_execute=false`; 7 registered team-event
  sports and 2 model-ready (MLB, NFL); NBA/NCAAF unregistered. This does not
  prove an Action request, worker claim, evidence lookup, or immutable receipt.
- Autonomous engineering: no controller dispatch was made in this session. CI
  check runs are durable receipts for authored work, not a substitute for a
  `wow-engineering` App dispatch. Objective G remains UNVERIFIED.

### Session 2 review correction — readback identity and boundary capture

The ready-for-review head `2794fbad` exposed five unresolved review findings.
All five were addressed in code; none was dismissed as stale:

- `prop_evidence_readback.normalize_identity` now delegates stat canonicalization
  to the writer's governed `PROP_STAT_ALIASES` resolver instead of a local
  uppercase/hyphen transform, so NFL `PASS_YARDS` resolves to `PASSING_YARDS`
  and runtime-registered aliases (WNBA `PTS` → `POINTS`) resolve identically.
- The candidate lookup applies canonical sport/player/stat filters in the
  database before its 25-row bound, so distractor rows cannot hide an exact
  identity and produce a false `SNAPSHOT_ROW_MISSING`. A window that reaches the
  bound without a different id is `PARTIAL`, never proof of absence.
- The primary readback RPC keeps the caller's raw stat label; only row/candidate
  matching and the fingerprint alias-resolve. The MLB `K` →
  `PITCHER_STRIKEOUTS` regression checks the raw RPC argument, fingerprint, row
  match and candidate match.
- Live Action 422s are attributed to evidence only for explicit evidence failure
  codes; non-evidence and null codes return `LIVE_ACTION_OTHER_422` with
  `reported_cause_supported=null`.
- The readback route no longer chains an exception into its 503 response
  (CodeQL stack-trace exposure).
- `score_prop` now captures the non-PII identity/request/caller correlation and
  re-raises the original `HTTPException` unchanged when the primary evidence RPC
  fails with 422/503; infrastructure failures are not relabeled as absence.

Regression coverage: **44 passed** across the readback, score-route and repair
identity suites, including >25 distractor rows, the partial-window case, the MLB
raw-RPC alias case, both primary exception paths, and the NFL/WNBA alias cases. A
new exact-head CI/CodeRabbit run is required; this entry does not certify the
findings resolved until that run and independent review complete.
`can_execute=false` is unchanged.

## Escalation blockers

`BLOCKER-001`
`EXACT_FAILURE`: No production Render/Supabase credentials or readback in this environment.
`EVIDENCE`: Sandbox has no Render token or production database URL; only a validation Supabase integration is connected.
`AFFECTED_WORKSTREAM`: A1, A3, A4, and production acceptance for all sports.
`OWNER_REQUIRED`: Repository owner / release authority.
`MINIMUM_ACTION_NEEDED`: Provide production read access (or run the documented readback) for web/worker SHA, database project, and evidence/receipt rows.
`WORKAROUND_ATTEMPTED`: Repo/build inspection and validation of the shared contract only.
`OTHER_WORK_CONTINUING`: The A2 identity repair, readback path and regression tests; production execution remains unavailable.

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

## Session 2 final review correction and autonomy receipt

- A further semantic review finding showed that a requested snapshot id can point
  to the requested row under the writer's canonical stat alias while the primary
  RPC returns `PROP_EVIDENCE_IDENTITY_MISMATCH` because it compares the raw
  request label. `classify_evidence_state` now reports
  `PRIMARY_LOOKUP_STAT_ALIAS_MISMATCH` with
  `GOVERNED_EVIDENCE_EXISTS_BUT_PRIMARY_STAT_ALIAS_MISMATCH` instead of falsely
  claiming the id points to a different identity. The new regression covers an
  MLB `K` request against a stored `PITCHER_STRIKEOUTS` row and asserts the raw
  RPC argument remains `K`.
- Focused readback, score-route and repair identity suites: **45 passed** after
  this correction; `git diff --check` clean.
- Objective G receipt: the canonical provider dispatcher refused dispatch with
  422 because the workflow is disabled. The existing ChatGPT worker accepted a
  manual recovery dispatch for #1688 (`P0_AUTONOMY_RESTORE`) at `main`
  `db5e6b7c`, run
  [42487106043](https://github.com/gregoryharper84-ship-it/WOW-Dashboard/actions/runs/42487106043).
  The job failed at `Recheck openai provider breaker under the global writer
  mutex` before the Engineering Lead or implementation steps; no worker branch,
  commit, or test result exists. The existing auto-repair controller run
  `42486632353` completed successfully with no eligible HOLD target. No
  competing controller was created.

## Honest completion statement

The overall restoration is **WORKING_NOT_COMPLETE**. No sports model was
certified, no protected merge was performed, no deployment was made, and no
production scoring receipt was verified. The executed engineering results are
the bounded A2 fallback-identity repair and the read-only evidence readback
(17 focused tests, 4 acceptance tests, 4 synthetic self-acceptance tests). The
validation ledger disproves the reported 422 as a live Action failure: it is the
expected self-acceptance probe. Production readback, real model evidence, and
independent authority remain blocked or absent.

## Session 3 — canonical effective scoring identity

- **Defect:** `repair_prop_evidence()` recovered canonical evidence, but
  `_server_owned_inference_request()` still hashed the raw caller
  event/player/stat labels. Equivalent requests such as `K` versus
  `PITCHER_STRIKEOUTS`, or padded player/event text, could therefore produce
  different `market_identity_id` and `prediction_id` values for the same
  governed evidence.
- **Change:** added `_canonical_scoring_identity()` and
  `_effective_scoring_request()`. The effective copy is derived from the
  validated evidence and drives the inference request, settlement resolution,
  market audit, discrete model, settlement lane and persistence. The original
  caller request is never mutated and is returned in `request_identity_audit`.
  Line, direction, period/settlement semantics, market quotes and seed remain
  as offered.
- **Regression:** `tests/test_prop_canonical_effective_identity.py` submits a
  canonical request and an alias/padded request with a different requested
  snapshot id through the real `/score-prop` route, real repair fallback, real
  discrete scoring engine and real `ledger.insert_prediction()` idempotency.
  It asserts one evidence snapshot, one market identity, one prediction id,
  one inserted row, unchanged line/direction, and raw caller audit fields.
- **Readback acceptance:** `test_api_prod_market.py` now exercises
  `/v17/prop-evidence-readback` with missing auth (401), empty/partial identity
  (422), unavailable client and unexpected diagnosis failure (sanitized 503),
  and a valid read-only response that does not expose player names, game logs,
  box scores or probabilities and does not call scoring/repair/mutation.
- **CodeQL disposition:** removed the readback's `str(exc)` response field
  (stable `INVALID_IDENTITY` code instead) and wrapped the whole endpoint in a
  sanitized 503 boundary. Exact-head CodeQL annotation must be re-read after
  push; a green workflow alone is not accepted as proof.
- **Local verification:** focused suites **54 passed**; broader affected
  suites **44 passed, 8 skipped** (all skips require the ephemeral Postgres
  integration service); docstring coverage **100%** in the three changed source
  modules; `git diff --check` clean.
- **Authority:** no merge, deployment, production request, fabricated receipt
  or certification. `can_execute=false` throughout.

### Session 3 shard-order correction

- Exact-head CI at `7ae34341` exposed two order-dependent failures that the
  focused runs did not. Neither was accepted as a flake.
- **Calibration registry:** the new regression registered its test calibrator
  at import time, but other shard modules clear the process-global adapter
  registry. The test now snapshots, registers per test, and restores the prior
  registry, so it no longer depends on import order and does not leak state.
- **Market identity alignment:** canonical evidence resolved WNBA `REB` to
  `REBOUNDS`, while the market audit compared the quote's raw `REB`. That is a
  genuine identity disagreement, not just test noise. The audit now normalizes
  quote event/player/stat/period with the same writer resolver used by the
  evidence identity; line, side, price, freshness and settlement basis are
  unchanged. The two-way market test now forces the alias path and asserts the
  effective request and both audited quotes resolve to `REBOUNDS`.
- **Local shard rerun:** shard 0 **2299 passed, 39 skipped**; shard 1
  **2870 passed, 7 skipped**; shard 2 **1845 passed, 9 skipped**; shard 3
  **2183 passed, 1 skipped**. Focused affected set **98 passed, 8 skipped**
  (all skips require the ephemeral Postgres integration service).
- `can_execute=false`; no merge or deployment.

### Session 3 route-preflight alias correction

- CodeRabbit's exact-head review at `c5aa2e75` found a remaining production
  alias gap: `_preflight_prop_route` looked up the caller's raw `stat_type`
  (`K`) before evidence repair, while the specialist and certified-artifact
  registries are keyed by `PITCHER_STRIKEOUTS`. A real MLB alias request could
  therefore terminate as `MODEL_UNAVAILABLE` before canonical evidence was
  ever consulted. The regression's permissive stubs had hidden this.
- `_preflight_prop_route` now derives a canonical `route_stat` with the same
  writer resolver used by repair and passes it to both
  `_controlling_specialist_provider` and `_prop_route_artifact`. The caller
  request and the blocked-route `requested_route` audit fields remain raw and
  unchanged.
- The end-to-end regression's specialist and artifact stubs now assert they
  receive the canonical `PITCHER_STRIKEOUTS` value, so the alias request fails
  if preflight regresses to raw `K`.
- **Local rerun after the correction:** focused affected set **54 passed,
  8 skipped**; shard 0 **2299 passed, 39 skipped**; shard 1 **2870 passed,
  7 skipped**; shard 2 **1845 passed, 9 skipped**; shard 3 **2183 passed,
  1 skipped**. All skips require the ephemeral Postgres integration service.
  AST docstring coverage remains **100%** in `api_prod_market.py`,
  `prop_evidence_readback.py` and `prop_evidence_repair.py`; `git diff --check`
  clean.
- `can_execute=false`; no merge or deployment.

### Session 3 readback diagnostics hardening

- CodeRabbit's next exact-head pass found two remaining readback issues. The
  stat-alias diagnosis was emitted even when the primary RPC mismatch was not
  the stat (for example, a player spelling difference), and the candidate query
  selected game-log payloads it never used.
- `classify_evidence_state` now receives `raw_stat_differs`. Only a real raw
  stat-label difference can produce `PRIMARY_LOOKUP_STAT_ALIAS_MISMATCH`; a
  canonical row with a non-stat primary mismatch produces
  `PRIMARY_LOOKUP_CANONICAL_IDENTITY_MISMATCH` and the distinct
  `GOVERNED_EVIDENCE_EXISTS_BUT_PRIMARY_IDENTITY_MISMATCH` basis. A focused
  counterexample test covers the player/event case.
- `_CANDIDATE_COLUMNS` now selects only the identity fields, not game logs, box
  scores or role payloads. The end-to-end regression's primary-fetch double now
  requires exact canonical event, sport, player and stat values, so it can no
  longer hide a fallback that stops carrying canonical identity.
- **Final local rerun:** focused affected set **55 passed, 8 skipped**; shard 0
  **2299 passed, 39 skipped**; shard 1 **2870 passed, 7 skipped**; shard 2
  **1846 passed, 9 skipped**; shard 3 **2183 passed, 1 skipped**. All skips
  require the ephemeral Postgres integration service.
- **Docstring disposition:** all 56 functions in the three changed production
  modules are documented. New acceptance tests are documented. Docstrings were
  deliberately not added to trivial test protocol doubles; if CodeRabbit's
  threshold still counts those, a review-authority waiver is required rather
  than boilerplate. This is not self-approved.
- `can_execute=false`; no merge or deployment.

### Session 5 — CodeRabbit exact-head reconciliation

- CodeRabbit completed successfully at `48940fa5` but still flagged one
  actionable test gap and a docstring warning at 73.97% against the 80%
  threshold. Its final review also noted that padded sport casing could still
  reach route lookup before evidence repair.
- `_preflight_prop_route` now strips and upper-cases the route sport before
  calling the writer's canonical stat resolver and before specialist/artifact
  lookup. The caller request remains unchanged for audit, and the error payload
  still reports the caller's raw stat label.
- The noncanonical regression payload now includes `"  mlb  "` as well as the
  padded event, player and `K` alias, so the route stubs can only succeed after
  sport and stat canonicalization.
- Added a negative two-way market regression using a different event and
  participant on each side. It proves both sides are withheld from the scorer,
  the market lane holds with `WOW_HOLD_MARKET_IDENTITY_MISMATCH`, the model
  probability is preserved, and `can_execute` remains false.
- Added eight substantive test docstrings covering fingerprint stability,
  ready/absent/partial classification, identity and alias attribution, and
  sanitized infrastructure failure. Production module docstrings remain
  complete; no boilerplate was added to protocol doubles.
- **Local rerun:** focused affected set **56 passed, 8 skipped**; shard 0
  **2299 passed, 39 skipped**; shard 1 **2871 passed, 7 skipped**; shard 2
  **1846 passed, 9 skipped**; shard 3 **2183 passed, 1 skipped**. All skips
  require the ephemeral Postgres integration service.
- `can_execute=false`; no merge or deployment.
