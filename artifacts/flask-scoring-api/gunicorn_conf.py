import threading


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
    """
    try:
        import app as flask_app

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
