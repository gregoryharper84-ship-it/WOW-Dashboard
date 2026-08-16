import os
import threading
import time


# ── Autoscale keep-alive ──────────────────────────────────────────────────────
# Autoscale kills the process after ~15 min of no incoming traffic.  A cold
# restart takes ~14 s, which is wide enough for all GPT retries to land inside
# the dead window.  Worker 1 runs a daemon thread that pings /wow/engine/health
# every 10 minutes so the idle timer never reaches 15 minutes.
#
# Only active when REPLIT_DEPLOYMENT=1 (production). Dev workers are unaffected.

_KEEPALIVE_INTERVAL_S = 600   # 10 minutes  (< 15-min autoscale threshold)
_KEEPALIVE_INITIAL_DELAY_S = 90  # let gunicorn fully stabilise before first ping


_KEEPALIVE_MAX_CONSECUTIVE_FAILURES = 5   # log WARNING after this many in a row


def _keepalive_loop(prod_url: str, worker_pid: int, log) -> None:
    """
    Ping /wow/engine/health every _KEEPALIVE_INTERVAL_S seconds.

    Logging policy (#66 fix):
      - SUCCESS: silent (no log).  Routine pings have no diagnostic value and
        clutter deployment logs.
      - FAILURE: log.warning on every failure.  After
        _KEEPALIVE_MAX_CONSECUTIVE_FAILURES consecutive failures, escalate to
        log.error so an unhealthy server is visible rather than masked (#65 fix).

    The keepalive loop NEVER stops — if the server recovers, pings resume.
    The consecutive-failure counter resets on the next success.
    """
    time.sleep(_KEEPALIVE_INITIAL_DELAY_S)
    consecutive_failures = 0
    while True:
        try:
            import urllib.request
            with urllib.request.urlopen(
                f"{prod_url}/wow/engine/health", timeout=10
            ) as resp:
                status = resp.status
            if status == 200:
                consecutive_failures = 0   # reset on success; no log (policy #66)
            else:
                consecutive_failures += 1
                _log_keepalive_failure(log, worker_pid, f"HTTP {status}", consecutive_failures)
        except Exception as exc:
            consecutive_failures += 1
            _log_keepalive_failure(log, worker_pid, str(exc), consecutive_failures)
        time.sleep(_KEEPALIVE_INTERVAL_S)


def _log_keepalive_failure(log, worker_pid: int, detail: str, consecutive: int) -> None:
    msg = (
        f"[keepalive] worker {worker_pid}: health check failed "
        f"(consecutive={consecutive}): {detail}"
    )
    if consecutive >= _KEEPALIVE_MAX_CONSECUTIVE_FAILURES:
        log.error(
            msg + f" — server appears persistently unhealthy "
            f"(≥{_KEEPALIVE_MAX_CONSECUTIVE_FAILURES} consecutive failures)"
        )
    else:
        log.warning(msg)


def post_fork(server, worker):
    """Re-initialize all threading.Lock instances after gunicorn forks a worker.

    With --preload, gunicorn imports the entire app in the master process and
    then forks each worker. Any threading.Lock that was *held* by a daemon
    thread at the moment of fork is inherited by the worker in a permanently-
    locked state — there is no thread in the worker to release it — causing
    every subsequent `with lock:` call to block forever and eventually trigger
    a WORKER TIMEOUT.

    The correct remedy (Python docs, PEP 9) is to re-create each lock in the
    child process via os.register_at_fork or a gunicorn post_fork hook.
    We also reset every _READY flag so each worker performs its own idempotent
    schema bootstrap (CREATE TABLE IF NOT EXISTS is safe to run concurrently).

    Stage 2 bootstrap note
    ─────────────────────
    ensure_all_tables() and start_settlement_worker() run in the master process
    (via the startup warmup thread and module-level code respectively). The
    daemon threads started there do NOT survive fork. Without explicit re-wiring
    here, every worker inherits:
      - llp_stage2_tables._TABLES_READY = False (if warmup thread hadn't finished)
        OR stale True with a permanently-locked _TABLES_LOCK
      - settlement_worker._WORKER_STARTED = True (inherited flag, no real thread)

    This hook corrects both by resetting the flags/locks and restarting each
    subsystem in the worker process itself.
    """
    try:
        import app as flask_app

        # ── app.py-level locks ───────────────────────────────────────────────
        flask_app._log_lock                  = threading.Lock()
        flask_app._ESPN_PLAYER_SEARCH_LOCK   = threading.Lock()
        flask_app._FIXTURES_SCHEMA_LOCK      = threading.Lock()
        flask_app._FIXTURES_REFRESH_LOCK     = threading.Lock()
        flask_app._TENNIS_CSV_LOCK           = threading.Lock()
        flask_app._UMPIRE_SCHEMA_LOCK        = threading.Lock()
        flask_app._UMPIRE_POPULATE_LOCK      = threading.Lock()
        flask_app._LINES_SCHEMA_LOCK         = threading.Lock()
        flask_app._CLV_SCHEMA_LOCK           = threading.Lock()
        flask_app._CM_SCHEMA_LOCK            = threading.Lock()
        flask_app._LLP_POSTMORTEM_SCHEMA_LOCK = threading.Lock()
        flask_app._LLP_PRO_SCHEMA_LOCK       = threading.Lock()
        flask_app._LLP_CRON_LOCK             = threading.Lock()
        flask_app._WNBA_CRON_LOCK            = threading.Lock()
        flask_app._ODDS_QUOTA_LOCK           = threading.Lock()

        flask_app._FIXTURES_SCHEMA_READY     = False
        flask_app._UMPIRE_SCHEMA_READY       = False
        flask_app._LINES_SCHEMA_READY        = False
        flask_app._CLV_SCHEMA_READY          = False
        flask_app._CM_SCHEMA_READY           = False
        flask_app._LLP_POSTMORTEM_SCHEMA_READY = False
        flask_app._LLP_PRO_SCHEMA_READY      = False

        server.log.info(f"[post_fork] worker {worker.pid}: all locks re-initialized")
    except Exception as exc:
        server.log.warning(f"[post_fork] lock re-init failed (non-fatal): {exc}")

    # ── Stage 2: re-initialize tables module state and run ensure_all_tables ─
    # Root cause: the master's warmup-thread sets _TABLES_READY=True and then
    # workers are forked. Either the flag is False (thread didn't finish in time)
    # or the _TABLES_LOCK is inherited in a potentially-held state. In both cases
    # each worker must bootstrap Stage 2 for itself.
    try:
        import gate_engine.llp_stage2_tables as _s2
        _s2._TABLES_READY = False
        _s2._TABLES_LOCK  = threading.Lock()
        # Run the DDL in a background thread so the fork hook returns quickly.
        # ensure_all_tables() is idempotent and uses CREATE TABLE IF NOT EXISTS.
        threading.Thread(
            target=_s2.ensure_all_tables,
            daemon=True,
            name=f"s2-ensure-tables-w{worker.pid}",
        ).start()
        server.log.info(f"[post_fork] worker {worker.pid}: Stage 2 table bootstrap scheduled")
    except Exception as exc:
        server.log.warning(f"[post_fork] Stage 2 table bootstrap failed (non-fatal): {exc}")

    # ── Odds API quota: ensure cross-worker table exists ─────────────────────
    # See gate_engine/pg_odds_quota.py. Fixes: quota state tracked as a
    # module-level dict in app.py is per-process under gunicorn, so the
    # /wow/odds/quota-status warning could be invisible to whichever worker
    # didn't make the last Odds API call. Idempotent DDL, daemon thread.
    try:
        import gate_engine.pg_odds_quota as _oq
        threading.Thread(
            target=_oq.ensure_table_exists,
            daemon=True,
            name=f"odds-quota-ensure-table-w{worker.pid}",
        ).start()
        server.log.info(f"[post_fork] worker {worker.pid}: odds quota table bootstrap scheduled")
    except Exception as exc:
        server.log.warning(f"[post_fork] odds quota table bootstrap failed (non-fatal): {exc}")

    # ── Stage 2: reset settlement worker and start a real thread in this worker ─
    # Root cause: start_settlement_worker() ran in the master → set
    # _WORKER_STARTED=True → workers inherit the flag with NO real thread running.
    # The health endpoint reported "started: true" while ticks stayed 0 forever.
    try:
        import gate_engine.settlement_worker as _sw
        # Re-create the lock first (may be inherited locked from parent)
        _sw._WORKER_LOCK    = threading.Lock()
        # Reset the flag so start_settlement_worker() will actually start a thread
        _sw._WORKER_STARTED = False
        # Reset tick counters so health stats reflect THIS worker's actual state
        _sw._WORKER_STATS.update({
            "ticks":             0,
            "props_graded":      0,
            "kalshi_graded":     0,
            "errors":            0,
            "last_tick":         None,
            "last_success_tick": None,
            "last_error":        None,
        })
        _sw.start_settlement_worker()
        server.log.info(f"[post_fork] worker {worker.pid}: settlement worker started")
    except Exception as exc:
        server.log.warning(f"[post_fork] settlement worker start failed (non-fatal): {exc}")

    # ── Autoscale keep-alive (worker 1 / production only) ────────────────────
    # Pings /wow/engine/health every 10 min so the 15-min idle SIGTERM never
    # fires while a GPT session might need the server.  Only one worker runs
    # the ping; worker.age==1 is the first worker forked by the master.
    try:
        prod_url = os.environ.get("REPLIT_APP_URL", "").rstrip("/")
        if prod_url and worker.age == 1:
            threading.Thread(
                target=_keepalive_loop,
                args=(prod_url, worker.pid, server.log),
                daemon=True,
                name="autoscale-keepalive",
            ).start()
            server.log.info(
                f"[post_fork] worker {worker.pid}: autoscale keep-alive started "
                f"(interval={_KEEPALIVE_INTERVAL_S}s, url={prod_url})"
            )
        elif not prod_url:
            server.log.info(
                f"[post_fork] worker {worker.pid}: keep-alive skipped "
                f"(REPLIT_APP_URL not set — dev environment)"
            )
    except Exception as exc:
        server.log.warning(f"[post_fork] keep-alive start failed (non-fatal): {exc}")

    # ── Pitcher prefetch executor reset ───────────────────────────────────────
    # Each worker must own its own ThreadPoolExecutor. With --preload the master
    # process imports pitcher_prefetch before forking, so _executor may already
    # be set. Resetting it here ensures each worker creates a fresh executor on
    # first use and does not share state with sibling workers.
    try:
        from gate_engine.mlb import pitcher_prefetch as _ppf
        _ppf._executor = None
        _ppf._executor_lock = threading.Lock()
        _ppf._inflight = {}
        _ppf._inflight_lock = threading.Lock()
        server.log.info(
            f"[post_fork] worker {worker.pid}: pitcher_prefetch executor reset"
        )
    except Exception as exc:
        server.log.warning(
            f"[post_fork] pitcher_prefetch reset failed (non-fatal): {exc}"
        )

    # ── Player identity cache schema-ready reset ──────────────────────────────
    # Force each worker to verify/create the DB schema on its first lookup.
    try:
        from gate_engine.mlb import player_identity_cache as _pic
        _pic.reset_schema_ready()
        server.log.info(
            f"[post_fork] worker {worker.pid}: player_identity_cache schema_ready reset"
        )
    except Exception as exc:
        server.log.warning(
            f"[post_fork] player_identity_cache reset failed (non-fatal): {exc}"
        )
