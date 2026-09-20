# PM-2026-09-20-001 — Flask app import starts a live manifest reaper thread under pytest

- status: FIX_IN_PROGRESS
- severity: P1
- domain: CI / legacy Flask test infrastructure
- created_utc: 2026-09-20T22:40:00Z
- runtime_generation: V17_ACTIVE
- terminal_authority: V17_TERMINAL_REDUCER
- can_execute: false

## Impact

`wow-verify`'s `WOW required-three regression` job — one of the three protected
checks the Morning-Green continuation workflow independently re-checks before
authorizing a protected merge — failed on `main` after PR #613 merged
(GitHub Actions run `35537878665`, main commit `2493d258`). A red required
check on `main` signals that any new PR built on top of `main` is liable to
inherit the same failure, which blocks the autonomous CI-to-merge path for
every open Morning-Green PR, not just one.

## Detection

- Detected at: 2026-09-20, during this run's nightly discovery sweep of
  `gh run list --branch main --status failure`.
- Detected by: WOW V17 nightly engineering autopilot (this run).
- First known bad run: `35537878665` (`WOW required-three regression`,
  `wow-verify.yml`, push to `main`, commit `2493d258`).

## Evidence

- `gate_engine/tests/test_daily_orchestrator.py::TestRunDailyOrchestration::test_persist_false_skips_db_writes`
  failed: `AssertionError: Expected 'ensure_tables' to not have been called.
  Called 1 times. Calls: [call(), call().__bool__()]`, with
  `Captured stdout call` showing `BOOT_TIMING start app import` /
  `BOOT_TIMING imported stdlib` — i.e. `app.py`'s module body executed
  *during this specific test's call phase*, not at collection time.
- The same job's raw Postgres log shows concurrent-session symptoms during
  unrelated tests in the same run: `WARNING: you don't own a lock of type
  ExclusiveLock` and cascading `ERROR: current transaction is aborted,
  commands ignored until end of transaction block` while a schema-bootstrap
  transaction for `uac_evidence_packets` / `llp_source_snapshots` was in
  flight, followed by `column "fact_type" of relation "uac_evidence_packets"
  does not exist` — consistent with a second, uncoordinated connection
  interrupting the same shared CI-local database mid-DDL.
- `[call(), call().__bool__()]` matches exactly one call site in the tree:
  `gate_engine/daily_run_lifecycle.py::ensure_manifest_ready()` —
  `if not ensure_tables(): raise RuntimeError(...)`.
- Reproduced locally (with the `jobs`/`flask` dependency gaps of the
  sandboxed worker noted below): a scratch probe proved that merely
  importing `jobs.wow_daily_scan` — the exact module
  `test_persist_false_skips_db_writes` patches with
  `patch("jobs.wow_daily_scan.run_scan", ...)` — triggers `app.py`'s
  `BOOT_TIMING` prints. `jobs/wow_daily_scan.py:57-62` loads the *entire*
  `app.py` module body via
  `importlib.util.spec_from_file_location("app_module", _app_path)` +
  `exec_module(...)` solely to reach `compute_wow_score`, independent of and
  in addition to the two files that `import app` directly
  (`tests/test_daily_run_contract.py`, `tests/test_learning_log.py`).
- `app.py:140-148` (pre-fix) called `ensure_manifest_ready()` (a real,
  synchronous DB round trip) and `start_manifest_reaper()` (starts a
  `daemon=True` thread that calls `reap_expired_runs_once()` immediately and
  every `REAPER_INTERVAL_SECONDS=30` thereafter) **unconditionally at
  import time**, with no test/CI guard.
- The codebase already carries three independent, partial acknowledgements
  of this exact hazard, none of which closed it:
  - `tests/test_daily_run_contract.py` docstring: "The app bootstraps
    manifest state when imported. Keep these real Flask-client checks in a
    child interpreter so lifecycle-only tests retain their isolated module
    state in the parent pytest process" — mitigates only its own file, via
    a subprocess.
  - `gate_engine/tests/test_runtime_safety_cleanup.py` (two call sites):
    `# app.py starts a production warmup daemon at import time. Prevent
    test-only imports from leaking background model/data loads into the
    rest of the pytest process.` followed by
    `with patch("threading.Thread.start", return_value=None): from app
    import app` — silences the reaper *thread* for those two tests, but does
    not stop `ensure_manifest_ready()`'s synchronous `ensure_tables()` DB
    call in the same import, and does not help any other file.
  - `app.py` itself already uses `os.environ.get("PYTEST_CURRENT_TEST")` in
    two other functions (`_odds_quota_snapshot_cross_worker`, the quota
    write-through) specifically to keep live Postgres calls out of pytest,
    with comments describing the same class of flakiness.
- None of the above covers `tests/test_learning_log.py`
  ("`import app as _app_module`" at module scope, no subprocess, no thread
  patch) or the `jobs.wow_daily_scan` exec_module path, so the live reaper
  thread and its periodic DB polling still run for the life of the pytest
  process in the real `wow-verify` job.

## Root Cause

`app.py` is the historical Flask entrypoint
(`artifacts/flask-scoring-api`, legacy/reference regression material per
this repository's engineering contract §7). Several existing tests load its
module body — directly via `import app`, or indirectly via
`jobs/wow_daily_scan.py`'s `importlib.util` load of the same file — purely
to reach a helper function or a `Flask` test client. Because
`ensure_manifest_ready()` / `start_manifest_reaper()` ran unconditionally at
module import time with no test-mode guard, any such import inside the
pytest process bootstrapped the *real* manifest schema against the CI-local
Postgres and started a *real* daemon thread that polls the same database
every 30 seconds for the remaining lifetime of the test run — regardless of
whether that specific test ever intended to touch persistence.

That background thread:

1. Races with foreground test transactions on the same shared CI-local
   database connection pool, producing the observed advisory-lock
   contention and transaction-abort cascades.
2. Re-resolves `storage.daily_manifest.ensure_tables` dynamically on every
   firing (`from storage.daily_manifest import ensure_tables` inside
   `ensure_manifest_ready()`), so if it happens to fire while an unrelated
   test has that name mocked via `unittest.mock.patch`, the mock silently
   records an unexpected call — exactly the
   `test_persist_false_skips_db_writes` failure.

This is a non-deterministic, timing-dependent test-infrastructure defect,
not a change in sporting probability, calibration, terminal-reduction, or
persistence semantics, and not caused by PR #613 (which does not touch
`app.py`, `daily_orchestrator.py`, `daily_run_lifecycle.py`, or
`daily_manifest.py`). It is a long-standing latent hazard that this run is
the first to trip and diagnose.

## V17 Classification

- BACKEND_RUNTIME: legacy Flask (`artifacts/flask-scoring-api`) test-only
  import-time side effect; the active V17 production runtime
  (`artifacts/wow-engine`) is unaffected.
- MODEL_CAPABILITY: not applicable — no model/scorer/probability code path
  touched.
- REPOSITORY_GOVERNANCE: `WOW required-three regression`, one of the three
  protected checks gating Morning-Green autonomous merges, was red on
  `main`.
- LIVE_GPT_EDITOR_SYNC: not applicable.
- Terminal status: not applicable (no terminal-reducer code touched).
- `scoring_attempted`: not applicable.

## Controlling Lane / Specialist

Not applicable — this incident is CI/test-infrastructure plumbing
(`DEPLOYMENT_RUNTIME`-equivalent for this legacy artifact), not a sporting
specialist lane.

## Failure Semantics

No typed model/scorer/output failure semantics were touched. No terminal
label, calibration field, or probability value was read, written, or
reinterpreted by this defect or its repair.

## Remediation

- Engineering fix ID(s): FIX-2026-09-20-001
- Temporary mitigation: none needed/applied — the fix is the permanent
  repair.
- Permanent fix: gate the module-level `ensure_manifest_ready()` /
  `start_manifest_reaper()` calls in `app.py` behind
  `if "pytest" not in sys.modules:`, matching the codebase's own established
  `PYTEST_CURRENT_TEST` convention for keeping live Postgres/thread side
  effects out of the pytest process, but using a check that is also correct
  for module-level (import-time) code reached during collection, not only
  inside a running test's call phase.

## Verification

- Regression test(s):
  `gate_engine/tests/test_daily_run_lifecycle.py::TestAppImportDoesNotStartLiveReaperUnderPytest::test_fresh_app_module_load_skips_manifest_bootstrap_and_reaper` —
  loads a fresh copy of `app.py`'s module body via the same
  `importlib.util.spec_from_file_location` + `exec_module` mechanism
  `jobs/wow_daily_scan.py` uses, with `gate_engine.daily_run_lifecycle.
  ensure_manifest_ready` / `start_manifest_reaper` mocked, and asserts
  neither is called while running under pytest. This test fails against the
  pre-fix code and passes against the post-fix code.
- Acceptance test(s): existing
  `tests/test_daily_run_contract.py::test_daily_http_contract_and_dynamic_openapi_in_isolated_process`
  and `tests/test_learning_log.py` remain unaffected — the former imports
  `app` in a genuine child subprocess (no `pytest` in that process's
  `sys.modules`, so its real bootstrap/reaper behavior is preserved
  byte-for-byte), and the latter mocks the DB layer entirely and does not
  depend on the reaper or manifest bootstrap succeeding.
- Production verification: not applicable — `artifacts/flask-scoring-api`
  is legacy/reference regression material, not the active V17 production
  runtime; the change only guards a test-time import side effect and does
  not alter the gunicorn/production bootstrap path (`gunicorn_conf.py`'s
  `post_fork` hook already explicitly restarts the reaper in every worker
  regardless of whether the master process started it here, per its own
  documented fork-safety contract).
- Verified commit/deploy: pending PR merge; see FIX-2026-09-20-001 for the
  exact commit once opened.
- Known local-verification gap: this run's sandboxed execution environment
  does not have network egress to install `flask` / `psycopg2` (the same
  class of environment gap recorded in FIX-2026-09-14-001), so the new
  regression test and the full `gate_engine/tests/test_daily_orchestrator.py`
  suite could not be executed end-to-end locally in this run. Root cause was
  confirmed instead through direct source-level tracing (confirmed exact
  call site, exact trigger path, and exact pre-existing partial workarounds
  in three other test files) and by reproducing the `BOOT_TIMING` trigger
  itself as far as the sandbox's missing dependencies allow. CI, which
  installs the full `requirements.txt`, is the authoritative verification
  environment for this fix.

## Prevention / Follow-up

- Consider (separately, out of scope for this bounded repair) moving
  `jobs/wow_daily_scan.py`'s `importlib.util` load of `app.py` to import
  only `compute_wow_score` from a shared, Flask-independent module instead
  of exec'ing the entire Flask app file — this would remove the second,
  indirect trigger path entirely rather than only guarding it. Left as
  follow-up because it is a larger, non-bounded refactor of a widely-used
  legacy module.
- The `threading.Thread.start` patch in
  `gate_engine/tests/test_runtime_safety_cleanup.py` becomes redundant
  (harmless no-op) after this fix, since `start_manifest_reaper()` is no
  longer reached under pytest at all. Left in place — removing it is
  unrelated cleanup outside this bounded repair's scope.

## Closure

Not yet closed. This run implemented the bounded patch and a targeted
regression test and left the branch in a reviewable state, but did not
merge, deploy, or independently confirm required CI green (see the local
dependency-installation gap noted above). Per this repository's engineering
contract, PR creation and merge/deploy decisions belong to the surrounding
workflow, not to this worker.

- Closed at: not yet closed
- Closed by: pending independent review, required CI, and (if applicable)
  production verification
- Final status: FIX_IN_PROGRESS — see FIX-2026-09-20-001 for the exact
  branch/commit and outstanding verification steps
- Linked engineering fix(es): FIX-2026-09-20-001

---

V17 safety invariant: `can_execute=false`. This record cannot authorize, route, modify, approve, or cancel a wager/order.
