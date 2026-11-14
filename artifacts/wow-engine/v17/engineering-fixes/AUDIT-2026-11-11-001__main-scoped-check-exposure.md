# AUDIT-2026-11-11-001 — Main-scoped workflow check exposure

- **Incident:** #2422 (follow-up to FIX-2026-11-11-001 / PR #2608, #2611)
- **Status:** AUDIT_COMPLETE_TARGETED_FIXES_ONLY
- **Authority:** `can_execute=false`; read-only source audit, no runtime proof.

## Scope and method

Every `.github/workflows/*.yml` whose `on:` includes `schedule` or
`workflow_run` runs against the protected `main` tip, so GitHub attaches its job
check runs to that SHA and Render's `checksPass` trigger waits on them. A job is
counted as **isolated** only when every one of its steps is
`continue-on-error: true` (job-level `continue-on-error` is insufficient: the
job check still concludes `failure`).

Post-fix worktree count, derived from the `e9b3faac` base plus observer-isolation patches (**not** a measurement of `e9b3faac` itself):

```text
MAIN_SCOPED_WORKFLOWS = 48
JOBS                  = 81
ISOLATED_JOBS         = 11   (Scout, 4 Engineering auto-repair, readback, reconciler, 4 read-only observers)
AUTHORITATIVE_GATE_JOBS = 3  (Independent QA, Release Authority, verification)
UNISOLATED_OPTIONAL_JOBS = 67 across 39 workflows
```

`wow-v17-render-production-deploy.yml` is `disabled_manually` under Lean
Deployment V4 and is listed for completeness, not as live exposure.

## Why this audit did not blanket-isolate all 73 jobs

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
| Read-only observers / recovery | `wow-v17-lean-production-readback`, `wow-v17-main-verification-reconciler` | Fixed here: all steps isolated, typed reasons preserved |
| Acceptance evidence | `wow-v17-post-deploy-verification-orchestrator` | Intentionally fail-closed acceptance; not isolated |
| Autonomous writers | model maintenance, snapshots, Scout persist/refresh, dispatchers, secret sync, issue tickers | Not isolated in this change: each needs action-graph review so a failed verification cannot be followed by a write |
| Disabled controller | `wow-v17-render-production-deploy` | Retired; must not be re-enabled |

## Fixed (cumulative)

| Workflow | Job | Change |
| --- | --- | --- |
| `wow-v17-lean-production-readback.yml` | `production-readback` | Checkout, invariant check, receipt upload and disclosure isolated; invariant mismatch emits `READBACK_EXECUTION_INVARIANT_MISMATCH` |
| `wow-v17-main-verification-reconciler.yml` | `reconcile` | Step isolated; `MAIN_VERIFICATION_RECONCILER_SHA_INVALID` typed; superseded and re-dispatch outcomes recorded in the step summary |
| `wow-v17-action-origin-keepalive.yml` | `keepalive` | GET-only heartbeat isolated; `ACTION_ORIGIN_KEEPALIVE_UNREACHABLE` typed, success receipt `ACTION_ORIGIN_KEEPALIVE_OK` |
| `kalshi-weather-external-wake.yml` | `heartbeat` | Read-only wake/DB-read probe isolated; `KALSHI_WEATHER_WAKE_PROBE_FAILED` typed, success receipt `KALSHI_WEATHER_WAKE_PROBE_OK` |
| `wow-v17-golden-product-acceptance.yml` | `product-acceptance` | Tests, liveness GET and receipt steps isolated; `GOLDEN_ACCEPTANCE_BACKEND_LIVENESS_UNAVAILABLE` typed; false-green boundary unchanged |
| `wow-v17-nightly-engineering-scan.yml` | `nightly-engineering-scan` | All 11 steps isolated; `/health` and `/governance` probes emit `NIGHTLY_SCAN_PRODUCTION_PROBE_UNAVAILABLE`; evidence artifact retained |

The readback and reconciler describe themselves as non-deploy-gating observers;
the keepalive, external wake, golden acceptance and nightly scan are read-only
observers that never mutate repository or production state.

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

This is a **baseline candidate list**, not a claim that all named jobs remain unisolated at the present PR head. The four recently isolated observers are intentionally still represented in the historical list; a fresh count is needed after merge. Each truly remaining optional workflow must be handled individually: either isolate every step
and add guards proving no mutation follows a failed verification, or move the
optional work off the main-attached path. New observers must be added to
`artifacts/wow-engine/tests/test_v17_optional_observer_checkspass_isolation.py`
before enablement.

## Verification

- Both fixed workflows parse as YAML and each embedded `run` script passes `bash -n`.
- Isolation test extended with the two jobs and re-run with the adoption/Scout suites.
- No authoritative gate, deploy writer, or acceptance workflow changed.
