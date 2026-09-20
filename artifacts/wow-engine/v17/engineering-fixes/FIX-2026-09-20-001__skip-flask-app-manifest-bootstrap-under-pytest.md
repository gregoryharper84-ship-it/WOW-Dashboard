# FIX-2026-09-20-001 — Skip Flask app manifest bootstrap/reaper under pytest

## Status

TESTING

## Linked Postmortem(s)

- PM-2026-09-20-001

## Problem Statement

`artifacts/flask-scoring-api/app.py` called
`gate_engine.daily_run_lifecycle.ensure_manifest_ready()` (a synchronous
live-DB call) and `start_manifest_reaper()` (starts a real `daemon=True`
background thread that polls the database every 30 seconds) unconditionally
at module import time. Multiple existing tests load `app.py`'s module body —
directly (`tests/test_learning_log.py`, and
`tests/test_daily_run_contract.py` inside an isolated subprocess) or
indirectly (`jobs/wow_daily_scan.py:57-62` loads the entire file via
`importlib.util` to reach `compute_wow_score`) — with no guard against this
side effect. The resulting live background thread races with foreground test
transactions on the shared CI-local Postgres instance and can register a
spurious call on any test that later mocks `storage.daily_manifest.*`,
causing intermittent `WOW required-three regression` failures on `main`
(observed in GitHub Actions run `35537878665`,
`test_persist_false_skips_db_writes`).

## Root Cause Addressed

Confirmed: `app.py`'s module-level `ensure_manifest_ready()` /
`start_manifest_reaper()` calls had no test-mode guard, unlike two other
live-DB call sites in the same file
(`_odds_quota_snapshot_cross_worker`, the quota write-through path) which
already skip under `os.environ.get("PYTEST_CURRENT_TEST")`. Because this
particular side effect fires once at *import* time — which, for several
callers, happens during pytest collection rather than inside any single
test's call phase — the existing `PYTEST_CURRENT_TEST` idiom is not
sufficient here (that env var is only set for the duration of an individual
test's call, not during collection). `"pytest" not in sys.modules` is used
instead, which is true from the moment the `pytest` process starts through
the entire collection and execution phases.

## Scope

- Components: legacy Flask scoring API entrypoint
  (`artifacts/flask-scoring-api`), Daily manifest lifecycle bootstrap.
- Files:
  - `artifacts/flask-scoring-api/app.py`
  - `artifacts/flask-scoring-api/gate_engine/tests/test_daily_run_lifecycle.py`
  - `artifacts/wow-engine/v17/incident-ledger.json`
  - `artifacts/wow-engine/v17/postmortems/PM-2026-09-20-001__flask-app-import-starts-live-reaper-under-pytest.md`
  - `artifacts/wow-engine/v17/engineering-fixes/FIX-2026-09-20-001__skip-flask-app-manifest-bootstrap-under-pytest.md`
- Routes/endpoints: none changed. No Flask route, request/response shape, or
  status code changes.
- Models/lanes: none. No probability, calibration, or terminal-reduction
  code touched.
- Persistence/schema impact: none. `ensure_tables()` remains exactly as
  defined; only the unconditional *caller* in `app.py` is now guarded. No
  migration, RLS, or grant change.
- GPT editor/action impact: none — `app.py` is legacy Flask, not the active
  V17 Action surface (`artifacts/wow-engine`).

## Change

In `app.py`:

- Added `import sys` to the existing stdlib import block (no other module
  in the file previously needed it).
- Wrapped the existing `ensure_manifest_ready()` / `start_manifest_reaper()`
  calls in `if "pytest" not in sys.modules:`, with a comment explaining the
  race this prevents and confirming production/gunicorn is unaffected
  (`gunicorn_conf.py`'s `post_fork` hook already explicitly restarts the
  reaper in every worker after fork, independent of whether the master
  process started it here).

In `gate_engine/tests/test_daily_run_lifecycle.py`:

- Added `TestAppImportDoesNotStartLiveReaperUnderPytest`, which loads a
  fresh copy of `app.py`'s module body via the same
  `importlib.util.spec_from_file_location` + `exec_module` mechanism
  `jobs/wow_daily_scan.py` uses in production, with
  `gate_engine.daily_run_lifecycle.ensure_manifest_ready` and
  `start_manifest_reaper` patched, and asserts neither mock is called.

No other file changed. No test was deleted, skipped, or weakened; the two
existing partial workarounds this postmortem documents
(`tests/test_daily_run_contract.py`'s subprocess isolation,
`gate_engine/tests/test_runtime_safety_cleanup.py`'s
`patch("threading.Thread.start", ...)`) are left in place — they remain
correct, if now redundant, defense in depth.

## Governance Invariants

- [x] `can_execute=false` remains unchanged.
- [x] No live wager/order execution path was introduced.
- [x] Controlling specialist ownership remains intact.
- [x] Typed model/scorer/completion failures remain preserved.
- [x] No sportsbook implied probability or external projection is relabeled as governed model probability.
- [x] Probability/calibration fields are not modified solely to satisfy card/portfolio concerns.
- [x] No secret or service-role credential is exposed.

No gate was weakened. Production/gunicorn behavior for `ensure_manifest_ready()` /
`start_manifest_reaper()` is byte-for-byte unchanged (a real gunicorn worker
process never has `"pytest"` in `sys.modules`); only the test-time import
side effect changes.

## Tests

### Unit

- `gate_engine/tests/test_daily_run_lifecycle.py::TestAppImportDoesNotStartLiveReaperUnderPytest::test_fresh_app_module_load_skips_manifest_bootstrap_and_reaper` — new; proves the guard.

### Contract

- Not applicable — no request/response contract changed.

### Regression

- Not independently re-run end-to-end in this session: the sandboxed worker
  environment used for this run has no network egress to install `flask` /
  `psycopg2` (same class of gap as FIX-2026-09-14-001's documented
  production-verification note), so the full
  `gate_engine/tests/test_daily_orchestrator.py`,
  `gate_engine/tests/test_daily_run_lifecycle.py`, `tests/test_learning_log.py`,
  and `tests/test_daily_run_contract.py` suites could not be executed
  end-to-end here. `python -m pytest ... --collect-only` was used to confirm
  the edited/added files parse and collect correctly, and the new test's
  control flow was traced up to the point where the local sandbox's own
  missing `flask` dependency stops execution (identical stopping point for
  pre-fix and post-fix code, confirming the change does not alter that
  unrelated failure). Required CI (`wow-verify`), which installs the full
  `requirements.txt`, is the authoritative environment for this
  verification and has not yet run against this branch.

### Acceptance

- Manually traced (not executed, per the gap above):
  `tests/test_daily_run_contract.py::test_daily_http_contract_and_dynamic_openapi_in_isolated_process`
  imports `app` inside a genuine `subprocess` child interpreter that never
  imports `pytest`, so `"pytest" not in sys.modules` is `True` there and its
  real bootstrap/reaper behavior is preserved exactly as before this fix.
  `tests/test_learning_log.py` mocks the DB layer entirely and never
  depended on `ensure_manifest_ready()` succeeding.

## Deployment

- Branch: (this worker's current branch — see PR once opened by the
  surrounding workflow)
- Commit SHA: pending
- PR: pending — this worker does not open PRs or merge/deploy; per this
  repository's engineering contract, that is handled by the surrounding
  workflow.
- Deploy ID: not applicable (`artifacts/flask-scoring-api` is legacy;
  Render production runtime is `artifacts/wow-engine`)
- Environment: CI only
- Deployed at: not applicable

## Production Verification

Not applicable. `artifacts/flask-scoring-api` is legacy/reference regression
material (this repository's engineering contract §7); the active V17
production runtime is `artifacts/wow-engine`, which this change does not
touch. The relevant verification is the `wow-verify` required-checks run
against this branch, which has not yet completed as of this record.

## Rollback

- Rollback trigger: any test that legitimately depends on `app.py`'s import
  eagerly bootstrapping the manifest schema or starting the reaper thread
  while running under pytest (none identified in this repository as of this
  fix — see Scope/Change above for the three files checked).
- Rollback procedure: git revert of the merge commit. No migration or
  persisted state is introduced or removed by this change.
- Last known good commit/deploy: current `main` tip at time of this fix
  (pre-existing flake; there is no single prior "good" commit to pin to
  since the defect is timing-dependent and latent across prior history).

## Result

PARTIALLY VERIFIED — root cause confirmed via direct source-level tracing
and partial local reproduction (confirmed the exact `BOOT_TIMING` trigger
path through `jobs/wow_daily_scan.py`'s `importlib.util` load of `app.py`);
bounded patch and targeted regression test implemented; full local
regression run blocked by a sandboxed-environment dependency-installation
gap (no `flask`/`psycopg2`, no network egress in this worker). Required CI
on this branch has not yet run. Not yet merged or deployed.

## Follow-up

- Confirm `wow-verify`'s three required checks pass on this branch's exact
  head SHA once CI runs.
- After merge, confirm a subsequent `main`-triggered `wow-verify` /
  nightly-scan run stays green (this defect is timing-dependent, so a single
  green run is good evidence but not absolute proof of full elimination;
  the regression test added here is the deterministic guarantee).
- Separately consider (not in this bounded repair) refactoring
  `jobs/wow_daily_scan.py` to stop loading the entire `app.py` file via
  `importlib.util` merely to reach `compute_wow_score`.

---

V17 terminal authority remains `V17_TERMINAL_REDUCER`; no engineering fix may override it.
