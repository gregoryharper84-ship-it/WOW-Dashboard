# AUDIT-2026-11-11-001 — Main-scoped workflow check exposure

- **Incident:** #2422 (follow-up to FIX-2026-11-11-001 / PR #2608, #2611)
- **Status:** AUDIT_COMPLETE_TARGETED_FIXES_ONLY
- **Authority:** `can_execute=false`; read-only source audit, no runtime proof.
- **Reconciled:** 2026-11-25 against `main` @ `80423e73` — source-only recount,
  reproduced identically at `c1c6d359`. No workflow was modified by this
  reconciliation.
- **Pinned:** 2026-11-26 — two governed writers are pinned never-isolate in the
  isolation contract. No workflow was modified by that pin either.

## Scope and method

Every `.github/workflows/*.yml` whose `on:` includes `schedule` or
`workflow_run` runs against the protected `main` tip, so GitHub attaches its job
check runs to that SHA and Render's `checksPass` trigger waits on them. A job is
counted as **isolated** only when every one of its steps is
`continue-on-error: true` (job-level `continue-on-error` is insufficient: the
job check still concludes `failure`).

Since 2026-11-25 the governed Render services set `autoDeployTrigger: "off"` in
`render.yaml`, so `checksPass` no longer starts a deploy. The deploy path is now
`wow-v17-required-checks-deploy.yml`, which requires the three ruleset-required
checks to be green on the exact current `main` SHA before it POSTs a deploy.
Optional observer failures therefore no longer block deploys through
`checksPass`, but they still attach red check runs to `main` commits they did
not produce and still misreport autonomous health, so the isolation invariant
below is unchanged.

Four counting boundaries matter when comparing snapshots of this document:

- **Triggers.** The counts below use `schedule` + `workflow_run` only. Adding
  `push: branches: [main]` widens the current population to 76 workflows / 127
  jobs, and `workflow_call`-only workflows (e.g.
  `wow-v17-spread-forward-production-canary.yml`) are reachable on `main` only
  through a caller job, so they are counted under the caller, not separately.
- **Job `if:` gating.** A job inside a main-scoped workflow whose condition
  restricts it to `pull_request` never attaches a check run to `main`. Three
  such jobs exist today and inflate the raw "unisolated" count.
- **Reusable-workflow calls.** A job with `uses:` has no steps; it inherits the
  called workflow's isolation and is counted separately.
- **Gate taxonomy.** Authoritative gate jobs are counted apart from optional
  observers, so `isolated + gates + optional = jobs`.

## Counts

### Historical post-fix worktree count (audit base `e9b3faac` + patches)

Post-fix worktree count, derived from the `e9b3faac` base plus observer-isolation patches (**not** a measurement of `e9b3faac` itself):

```text
MAIN_SCOPED_WORKFLOWS = 48
JOBS                  = 81
ISOLATED_JOBS         = 11   (Scout, 4 Engineering auto-repair, readback, reconciler, 4 read-only observers)
AUTHORITATIVE_GATE_JOBS = 3  (Independent QA, Release Authority, verification)
UNISOLATED_OPTIONAL_JOBS = 67 across 39 workflows
```

A recount of the same scope confirms these figures: `e9b3faac` itself measures
48 workflows / 81 jobs, and PR #2614's merge commit `7a43757c` measures 48 / 81
with exactly 11 fully isolated jobs. The numbers above were accurate at merge
time and are superseded below.

### Reconciled count (measured, `main` @ `80423e73`)

```text
MAIN_SCOPED_WORKFLOWS    = 49
JOBS                     = 84
ISOLATED_JOBS            = 17
AUTHORITATIVE_GATE_JOBS  = 3   (wow-v17-independent-qa-check::qa,
                                wow-v17-release-authority-check::admit-release, ::release)
UNISOLATED_OPTIONAL_JOBS = 64 across 40 workflows
```

Reconciliation arithmetic: `17 isolated + 3 gates + 64 optional = 84 jobs`.
All non-fully-isolated jobs, gates included, total 67 (58 with no isolated
steps, 5 partially isolated, 4 reusable-workflow calls). Excluding the 3
PR-only jobs leaves 61 optional jobs across 38 workflows that genuinely attach
a check run to `main`.

`wow-v17-render-production-deploy.yml` is `disabled_manually` under Lean
Deployment V4 and is listed for completeness, not as live exposure. The
successor writer `wow-v17-required-checks-deploy.yml` is **active** (workflow id
424593071, created 2026-11-19) and is discussed below.

## What is stale, and why

| Claim | Measured on `main` | Why it moved |
| --- | --- | --- |
| `MAIN_SCOPED_WORKFLOWS = 48` | 49 | `wow-v17-nfl-prospective-shadow.yml` (schedule + push main) and `wow-v17-required-checks-deploy.yml` (workflow_run on main) were added after the audit; `wow-v17-log-incident-intake.yml` left the scope by dropping to `pull_request`-only. Net +1. |
| `JOBS = 81` | 84 | +5 added (`nfl-prospective-shadow::verify`, `::capture-and-grade`, `nhl-model-maintenance::refresh-timeline`, `required-checks-deploy::deploy`, `morning-green-continuation::ordinary-sweep`), −2 removed (`log-incident-intake::intake`, `::verify-source`). Net +3. |
| `ISOLATED_JOBS = 11` | 17 | Six more jobs became fully isolated after merge: `basketball-model-maintenance::maintain`, `continuous-intelligence::learn`, `first-six-model-maintenance::maintain`, `first-six-transport-rescue::rescue`, `post-deploy-verification-orchestrator::acceptance-receipts`, and the new `nhl-model-maintenance::refresh-timeline`. |
| `AUTHORITATIVE_GATE_JOBS = 3` | 3 | Count holds, but the annotation is wrong: the three jobs are `qa` plus `admit-release` and `release`. `wow-verify` / `wow-engine-verify` are PR-triggered required contexts, not main-scoped jobs. |
| `UNISOLATED_OPTIONAL_JOBS = 67 across 39` | 64 across 40 | Five existing jobs left the optional-unisolated set by becoming isolated, four new unisolated jobs arrived, two left with `log-incident-intake`, and one workflow gained unisolated work. Net −3 jobs, +1 workflow. |
| `Acceptance evidence` row: "not isolated" | `acceptance-receipts` is 8/8 isolated | Only `deployment-readback` (0/5) and the four reusable-workflow-call jobs remain unisolated in that workflow. |
| "Fixed (cumulative)" table lists 6 workflows | 17 isolated jobs | The table never listed `wow-v17-scout-persist-resume.yml::resume-persistence` or the four `wow-v17-engineering-auto-repair.yml` jobs, although the count line referenced them; the six later isolations were also unlisted. |
| Historical list: `kalshi-weather-external-wake.yml 1` | 0 genuine | The listed job is `verify`, gated `github.event_name == 'pull_request'`, so it never attaches to `main`. The main-scoped `heartbeat` job is isolated. |
| Historical list: `wow-v17-log-incident-intake.yml 2` | not in scope | The workflow now triggers on `pull_request` only. |
| Historical list: `wow-v17-post-deploy-verification-orchestrator.yml 6` | 5 | `acceptance-receipts` is now isolated; `deployment-readback` plus four reusable-workflow calls remain. |
| Historical list: `wow-v17-morning-green-continuation.yml 1` | 2 | `ordinary-sweep` was added and is not isolated. |
| Historical list: `wow-v17-nhl-model-maintenance.yml 2` | 2 (different jobs) | `refresh-timeline` was added and is isolated; `maintain` and `verify` remain unisolated, so the count matches while the composition does not. |
| Historical list: `wow-engineering-24x7.yml 4` | 3 genuine | `test` is `pull_request`-only. |
| Historical list omits two main-scoped workflows | `nfl-prospective-shadow` (2 jobs), `required-checks-deploy` (1 job) | Both postdate the audit. |

Two omissions deserve emphasis because they are not read-only observers:

- `wow-v17-required-checks-deploy.yml::deploy` is a sanctioned Render writer
  (POST `/v1/services/<id>/deploys` only) introduced because `checksPass`
  skipped main commits carrying a failing optional check, and now the single
  production deploy path since native auto-deploy was turned off on 2026-11-25.
  It must stay fail-closed and must **not** be isolated; its own comment states
  that non-advancing outcomes already end green. Pinned in
  `NEVER_ISOLATED_WRITER_STEPS` on 2026-11-26 (see below).
- `wow-v17-nfl-prospective-shadow.yml::capture-and-grade` runs the prospective
  capture/grade writer against Supabase. It belongs in the autonomous-writer
  group requiring action-graph review, not in the read-only observer group. The
  action-graph review is complete and the job is now pinned never-isolate in
  `artifacts/wow-engine/tests/test_v17_optional_observer_checkspass_isolation.py`
  (see below); it is deliberately **not** in `OPTIONAL_MAIN_SCOPED_JOBS`.

### Never-isolate pins (contract-enforced, 2026-11-26)

Neither workflow below is an optional observer: on these checks a green
conclusion asserts that a governed write actually happened. Both are pinned in
`NEVER_ISOLATED_WRITER_STEPS` so a later blanket-isolation sweep cannot flip
them green.

| Workflow | Job | Pinned steps | Why it stays fail-closed |
| --- | --- | --- | --- |
| `wow-v17-nfl-prospective-shadow.yml` | `capture-and-grade` | `Capture genuine prekickoff challenger receipts`; `Grade only independently reconciled completed NFL games`; `Verify capture receipt is not all-held` | Service-role Supabase writer over the frozen prospective ledger. Its `run_ok` is false only for `CAPTURE_ACCOUNTING_INCONSISTENT` and `EVIDENCE_ALL_HELD`; `NO_EVENTS_IN_CAPTURE_WINDOW` and `EXISTING_OR_HELD` already end green, so a red check is a real integrity or evidence failure and not schedule noise. Isolation would report a capture that never happened. |
| `wow-v17-required-checks-deploy.yml` | `deploy` | `Deploy exact current main SHA once required checks are green` | Sole production deploy path since native auto-deploy was turned off on 2026-11-25. It POSTs Render deploys only after the three ruleset-required checks are green on the exact current `main` SHA, and `v17.required_checks_deploy` already ends green on non-advancing outcomes (superseded SHA, required checks pending or failed, pre-write read failure). Isolation could therefore only conceal a real deploy, identity or health failure. |

Isolation is deliberately *not* the guard for these two exposures. Since
2026-11-25 the deploy path gates on the three required check names filtered to
app `15368` (`REQUIRED_REGRESSION_CHECKS`) rather than on `checksPass`, and
neither pinned job is one of those contexts, so a red writer check can neither
block nor advance a deploy. Main-SHA consumers
(`wow-v17-engineering-auto-repair.yml`, `wow-v17-morning-green-continuation.yml`,
`wow-engineering-24x7.yml`) select check runs by exact name and app id rather
than by "any failing check", so a red writer check cannot provoke a spurious
repair or a false hold either. What remains is the explicit pin above.

## Why this audit did not blanket-isolate all remaining jobs

Isolation is not a free flag. `continue-on-error` makes a failed step's
conclusion `success`, so **later steps in the same job run**; it also turns a
genuine operational failure green. Applying it across the remaining jobs without
reading each action graph would:

- let post-verification mutation steps run after a failed verification (duplicate
  PRs, dispatches, comments, issue/secret writes);
- convert authoritative gate failures (QA/Release/required regressions) into
  deploy approval — explicitly forbidden;
- hide scheduled model/evidence outages that downstream digests rely on.

The runbook invariant (`WOW_V17_LEAN_RELEASE_V4`) therefore requires typed
receipts plus guards, per workflow. Classified exposure groups:

| Group | Examples | Disposition |
| --- | --- | --- |
| Authoritative merge/deploy gates | `wow-v17-independent-qa-check`, `wow-v17-release-authority-check` | Must stay fail-closed; never isolated |
| Required-context producers | `wow-verify`, `wow-engine-verify` | Must stay fail-closed; never isolated |
| Sanctioned deploy writers | `wow-v17-required-checks-deploy`, `wow-v17-render-production-deploy` (disabled) | Must stay fail-closed; never isolated; the active writer is contract-pinned (`NEVER_ISOLATED_WRITER_STEPS`) |
| Read-only observers / recovery | `wow-v17-lean-production-readback`, `wow-v17-main-verification-reconciler` | Fixed here: all steps isolated, typed reasons preserved |
| Acceptance evidence | `wow-v17-post-deploy-verification-orchestrator` | `acceptance-receipts` isolated; `deployment-readback` intentionally fail-closed |
| Autonomous writers | model maintenance, snapshots, Scout persist/refresh, prospective capture, dispatchers, secret sync, issue tickers | Not isolated in this change: each needs action-graph review so a failed verification cannot be followed by a write. Prospective `capture-and-grade` has since been reviewed and pinned never-isolate |
| Disabled controller | `wow-v17-render-production-deploy` | Retired; must not be re-enabled |

## Fixed (cumulative)

| Workflow | Job | Change |
| --- | --- | --- |
| `wow-v17-scout-persist-resume.yml` | `resume-persistence` | Steps isolated; `SCOUT_PERSISTENCE_SOURCE_UNAVAILABLE`, `SCOUT_PERSISTENCE_RECOVERY_SCAN_INCOMPLETE`, `SCOUT_PERSISTENCE_RECEIPT_UNCONFIRMED`; dependent steps guard on a resolved source run id |
| `wow-v17-engineering-auto-repair.yml` | `discover` | Steps isolated; `HOLD_TARGET_DISCOVERY_UNAVAILABLE`, `HOLD_TARGET_DISCOVERY_INVALID` |
| `wow-v17-engineering-auto-repair.yml` | `adoption-readonly-preflight` | Steps isolated; `SOURCE_ADOPTION_DISCOVERY_HOLD`, `TARGET_ADOPTION_REQUEST_INVALID_HOLD` |
| `wow-v17-engineering-auto-repair.yml` | `adopt-reviewed-owner-source` | Steps isolated; `ENGINEERING_APP_IDENTITY_UNAVAILABLE` |
| `wow-v17-engineering-auto-repair.yml` | `route-hold-findings` | Steps isolated; `HOLD_*` / `RECOVERY_*` route codes retained |
| `wow-v17-lean-production-readback.yml` | `production-readback` | Checkout, invariant check, receipt upload and disclosure isolated; invariant mismatch emits `READBACK_EXECUTION_INVARIANT_MISMATCH` |
| `wow-v17-main-verification-reconciler.yml` | `reconcile` | Step isolated; `MAIN_VERIFICATION_RECONCILER_SHA_INVALID` typed; superseded and re-dispatch outcomes recorded in the step summary |
| `wow-v17-action-origin-keepalive.yml` | `keepalive` | GET-only heartbeat isolated; `ACTION_ORIGIN_KEEPALIVE_UNREACHABLE` typed, success receipt `ACTION_ORIGIN_KEEPALIVE_OK`; a truncated 200 with a curl transport error is a failure, not a green |
| `kalshi-weather-external-wake.yml` | `heartbeat` | Read-only wake/DB-read probe isolated; `KALSHI_WEATHER_WAKE_PROBE_FAILED` typed, success receipt `KALSHI_WEATHER_WAKE_PROBE_OK`; bounded by `WAKE_HEARTBEAT_DEADLINE_EPOCH` |
| `wow-v17-golden-product-acceptance.yml` | `product-acceptance` | Tests, liveness GET and receipt steps isolated; `GOLDEN_ACCEPTANCE_BACKEND_LIVENESS_UNAVAILABLE` typed; false-green boundary unchanged; receipt carries the checked-out SHA, `UNVERIFIED` when provenance fails |
| `wow-v17-nightly-engineering-scan.yml` | `nightly-engineering-scan` | All 11 steps isolated; `/health` and `/governance` probes emit `NIGHTLY_SCAN_PRODUCTION_PROBE_UNAVAILABLE`; evidence artifact retained |
| `wow-v17-nhl-model-maintenance.yml` | `refresh-timeline` | Steps isolated; `NHL_SOURCE_REFRESH_CHECKOUT_UNAVAILABLE`, `NHL_SOURCE_REFRESH_RUNNER_FAILED` |
| `wow-v17-basketball-model-maintenance.yml` | `maintain` | Steps isolated; `BASKETBALL_MODEL_MAINTENANCE_INCOMPLETE`, `BASKETBALL_MAINTENANCE_BLOCKED` |
| `wow-v17-first-six-model-maintenance.yml` | `maintain` | Steps isolated; `FIRST_SIX_MODEL_MAINTENANCE_INCOMPLETE`, `FIRST_SIX_ANCILLARY_MAINTENANCE_BLOCKED` |
| `wow-v17-first-six-transport-rescue.yml` | `rescue` | Steps isolated; `FIRST_SIX_TRANSPORT_RESCUE_INCOMPLETE` |
| `wow-v17-continuous-intelligence.yml` | `learn` | Steps isolated; `CONTINUOUS_INTELLIGENCE_LEARN_INCOMPLETE`, `LEARNING_BACKEND_RESPONSE_INVALID` |
| `wow-v17-post-deploy-verification-orchestrator.yml` | `acceptance-receipts` | Steps isolated; `POST_DEPLOY_RECEIPT_RECONCILE_INCOMPLETE`, `ACCEPTANCE_LANES_FAILED`; `deployment-readback` deliberately left fail-closed |

The readback and reconciler describe themselves as non-deploy-gating observers;
the keepalive, external wake, golden acceptance and nightly scan are read-only
observers that never mutate repository or production state. The model-maintenance
and continuous-intelligence `maintain`/`learn` jobs are isolated only where the
job itself is optional and its write is guarded by a typed receipt.

## Current unisolated inventory (`main` @ `80423e73`)

Gate workflows are marked; PR-only jobs never attach to `main`.

| Workflow | Unisolated jobs | Isolated | Unisolated job detail |
| --- | --- | --- | --- |
| `kalshi-weather-external-wake.yml` | 1 | 1 | verify 0/2 (PR-only) |
| `wow-codeql.yml` | 1 | 0 | analyze 0/4 |
| `wow-engineering-24x7.yml` | 4 | 0 | test 0/3 (PR-only); select 0/3; resume 0/2; dispatch 0/2 |
| `wow-ncaaf-44-prospective-postgame.yml` | 2 | 0 | verify-frozen-grade-contract 0/2; postgame-capture-oct25-to-oct28-only 0/4 |
| `wow-v17-24h-engineering-closure-loop.yml` | 1 | 0 | advance-one-stage 0/11 |
| `wow-v17-basketball-model-maintenance.yml` | 1 | 1 | verify 0/4 |
| `wow-v17-continuous-intelligence.yml` | 1 | 1 | verify 0/4 |
| `wow-v17-daily-snapshot.yml` | 3 | 0 | verify 0/4; daily-snapshot 0/2; nfl-full-slate-exact-once-acceptance 0/2 |
| `wow-v17-engineering-provider-dispatcher.yml` | 2 | 0 | provider-control 2/12; independent-provider 0/4 |
| `wow-v17-engineering-push-handoff.yml` | 1 | 0 | dispatch-worker 0/1 |
| `wow-v17-engineering-sirt-intake.yml` | 1 | 0 | independent-intake 0/2 |
| `wow-v17-engineering-specialist-support.yml` | 1 | 0 | support-active-closure 0/10 |
| `wow-v17-first-six-model-maintenance.yml` | 1 | 1 | verify 0/4 |
| `wow-v17-first-six-transport-rescue.yml` | 1 | 1 | pr-contract 0/2 (PR-only) |
| `wow-v17-frontier-intelligence-agent.yml` | 1 | 0 | frontier-intelligence 0/6 |
| `wow-v17-governance-dispatcher.yml` | 1 | 0 | route 0/4 |
| `wow-v17-independent-qa-check.yml` [GATE] | 1 | 0 | qa 0/24 |
| `wow-v17-llp-shadow-observer.yml` | 2 | 0 | verify 0/4; shadow-observer 0/2 |
| `wow-v17-mlb-prop-forward-evidence.yml` | 1 | 0 | seed-forward-evidence 0/2 |
| `wow-v17-morning-green-continuation.yml` | 2 | 0 | continue-repair 0/13; ordinary-sweep 0/1 |
| `wow-v17-ncaaf-model-maintenance.yml` | 3 | 0 | verify 0/4; maintain 0/3; exact-candidate-replay-once 0/2 |
| `wow-v17-ncaaf-sdv-player-history-research.yml` | 3 | 0 | contract 0/3; acquire 0/5; cross_source 0/8 |
| `wow-v17-nfl-forward-shadow.yml` | 2 | 0 | verify 0/4; forward-shadow 0/2 |
| `wow-v17-nfl-prop-forward-evidence.yml` | 1 | 0 | seed-forward-evidence 0/2 |
| `wow-v17-nfl-prospective-shadow.yml` | 2 | 0 | verify 0/4; capture-and-grade 0/8 (writer) |
| `wow-v17-nhl-model-maintenance.yml` | 2 | 1 | verify 0/4; maintain 0/2 |
| `wow-v17-nightly-multiscout.yml` | 3 | 0 | verify 0/8; market-evidence-acceptance 1/7; nightly-discovery 5/23 |
| `wow-v17-p0-rapid-dispatch.yml` | 1 | 0 | dispatch 0/5 |
| `wow-v17-post-deploy-verification-orchestrator.yml` | 5 | 1 | deployment-readback 0/5; spread-forward, spread-certification, priority-props, golden-full-slate (reusable-workflow calls, no steps) |
| `wow-v17-priority-prop-lifecycle.yml` | 1 | 0 | priority-lifecycle 0/2 |
| `wow-v17-release-authority-check.yml` [GATE] | 2 | 0 | admit-release 0/4; release 0/7 |
| `wow-v17-release-resume-agent.yml` | 1 | 0 | release-resume 2/8 |
| `wow-v17-render-production-deploy.yml` | 1 | 0 | deploy-production 0/7 (disabled controller) |
| `wow-v17-required-checks-deploy.yml` | 1 | 0 | deploy 0/4 (sanctioned Render writer; must stay fail-closed) |
| `wow-v17-restoration-progress-ticker.yml` | 1 | 0 | refresh 0/3 |
| `wow-v17-scout-board-refresh.yml` | 1 | 0 | refresh 0/6 |
| `wow-v17-scout-brain-persist.yml` | 1 | 0 | persist 0/12 |
| `wow-v17-scout-source-refresh.yml` | 1 | 0 | refresh 2/12 |
| `wow-v17-secret-sync.yml` | 1 | 0 | reconcile 0/7 |
| `wow-v17-team-event-recoverable-refresh.yml` | 2 | 0 | verify 0/4; refresh 0/2 |
| `wow-v17-terminal-closure-controller.yml` | 1 | 0 | terminal-closure 0/5 |
| `wow-v17-wnba-prop-candidate.yml` | 2 | 0 | verify 0/5; train-and-register 0/6 |

Totals: 42 workflows with at least one non-fully-isolated job (40 optional + 2
gate workflows); 67 non-fully-isolated jobs, of which 3 are PR-only and 4 are
reusable-workflow calls.

## Historical unisolated-optional-job candidate inventory (baseline audit; not refreshed post-fix)

```text
kalshi-weather-external-wake.yml 1
wow-codeql.yml 1
wow-engineering-24x7.yml 4
wow-ncaaf-44-prospective-postgame.yml 2
wow-v17-24h-engineering-closure-loop.yml 1
wow-v17-basketball-model-maintenance.yml 2
wow-v17-continuous-intelligence.yml 2
wow-v17-daily-snapshot.yml 3
wow-v17-engineering-provider-dispatcher.yml 2
wow-v17-engineering-push-handoff.yml 1
wow-v17-engineering-sirt-intake.yml 1
wow-v17-engineering-specialist-support.yml 1
wow-v17-first-six-model-maintenance.yml 2
wow-v17-first-six-transport-rescue.yml 2
wow-v17-frontier-intelligence-agent.yml 1
wow-v17-governance-dispatcher.yml 1
wow-v17-llp-shadow-observer.yml 2
wow-v17-log-incident-intake.yml 2
wow-v17-mlb-prop-forward-evidence.yml 1
wow-v17-morning-green-continuation.yml 1
wow-v17-ncaaf-model-maintenance.yml 3
wow-v17-ncaaf-sdv-player-history-research.yml 3
wow-v17-nfl-forward-shadow.yml 2
wow-v17-nfl-prop-forward-evidence.yml 1
wow-v17-nhl-model-maintenance.yml 2
wow-v17-nightly-multiscout.yml 3
wow-v17-p0-rapid-dispatch.yml 1
wow-v17-post-deploy-verification-orchestrator.yml 6
wow-v17-priority-prop-lifecycle.yml 1
wow-v17-release-resume-agent.yml 1
wow-v17-render-production-deploy.yml 1 (disabled)
wow-v17-restoration-progress-ticker.yml 1
wow-v17-scout-board-refresh.yml 1
wow-v17-scout-brain-persist.yml 1
wow-v17-scout-source-refresh.yml 1
wow-v17-secret-sync.yml 1
wow-v17-team-event-recoverable-refresh.yml 2
wow-v17-terminal-closure-controller.yml 1
wow-v17-wnba-prop-candidate.yml 2
```

This is a **baseline candidate list**, not a claim that all named jobs remain unisolated at the present PR head. It has been superseded by the measured inventory above: four entries shrank because their optional job was isolated, one workflow left the scope entirely, one grew a second job, and two main-scoped workflows postdate the audit. Each truly remaining optional workflow must be handled individually: either isolate every step and add guards proving no mutation follows a failed verification, or move the optional work off the main-attached path. New observers must be added to
`artifacts/wow-engine/tests/test_v17_optional_observer_checkspass_isolation.py`
before enablement.

## Verification

- Recount method: parse every `.github/workflows/*.yml` at the named revision,
  select `on: schedule` / `workflow_run`, and count a job as isolated only when
  every step sets `continue-on-error: true`.
- Both fixed workflows parse as YAML and each embedded `run` script passes `bash -n`.
- The isolation contract pins 15 workflows / 22 job pairs and passes
  (`pytest -q tests/test_v17_optional_observer_checkspass_isolation.py`;
  15 passed, 2026-11-26 — 14 pre-existing plus the new writer pin).
- The 22 pinned pairs cover 17 of the 49 schedule/`workflow_run` jobs plus the 5
  `workflow_call` canary jobs reached through the post-deploy orchestrator.
- `NEVER_ISOLATED_WRITER_STEPS` pins 2 workflows / 2 jobs / 4 writer steps as
  never isolable, and asserts neither workflow is listed as an optional observer.
- No authoritative gate, deploy writer, or acceptance workflow changed.
