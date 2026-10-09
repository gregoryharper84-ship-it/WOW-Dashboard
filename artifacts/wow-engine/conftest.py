"""wow-engine test configuration.

Celery's task_always_eager makes apply_async() execute the task body
synchronously in-process instead of publishing to a broker — set globally
here so no test anywhere in this suite can hang or error trying to reach a
real Redis broker that doesn't exist in this sandbox/CI environment. Tests
that specifically want to exercise durable queue/broker mechanics (late ACK,
worker restart, real retry timing) belong in
test_agent_runtime_postgres_integration.py, gated on
WOW_AGENT_RUNTIME_INTEGRATION=1 against a real ephemeral Postgres+Redis+
Celery worker (see that file and .github/workflows/wow-engine-verify.yml),
not this default in-process test run.
"""
import pytest


@pytest.fixture(autouse=True, scope="session")
def _agent_runtime_celery_eager():
    from agent_runtime.queue import celery_app

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield


@pytest.fixture(autouse=True)
def _rundown_snapshot_cache_isolation():
    """Keep the process-global TheRundown snapshot cache out of test coupling.

    The cache and its counters are deliberately process-scoped in production so
    one research run issues one provider request per sport/date. In a test run
    that same scope would let one test's stubbed payload satisfy the next test's
    fetch, so both are reset around every test.
    """
    from v17 import market_evidence_observability, rundown_snapshot_cache

    rundown_snapshot_cache.reset()
    market_evidence_observability.reset()
    yield
    rundown_snapshot_cache.reset()
    market_evidence_observability.reset()


@pytest.fixture(autouse=True)
def _discovery_source_env_isolation():
    """Stop one test's minted proxy token from becoming another test's live feed.

    The nightly Multi-Scout deliberately writes a freshly minted OIDC token into
    the process environment so its proxy calls can use it. Under pytest that
    write outlives the test, and the cross-sport discovery lane would then treat
    a stubbed token as a real credential and attempt outbound calls from
    unrelated tests. Snapshot and restore the credentials discovery reads.
    """
    import os

    names = ("WOW_GITHUB_OIDC_TOKEN", "WOW_ODDS_PROXY_ACTION_KEY")
    before = {name: os.environ.get(name) for name in names}
    yield
    for name, value in before.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture(autouse=True)
def _rundown_datapoint_budget_isolation():
    """Reset the process-global daily Rundown budget per test.

    Limits stay active (tests exercise the real guard); only accumulated usage
    and the registered DB client are cleared so suites cannot couple through
    shared counters.
    """
    from v17 import rundown_datapoint_budget

    rundown_datapoint_budget._reset_for_tests()
    # Test-only service-role RPC stand-in. Production NEVER substitutes this:
    # when the real DB/migration is absent, reserve_call() fails closed.
    from datetime import datetime, timezone
    from threading import Lock
    from types import SimpleNamespace

    lock = Lock()
    days = {}
    requests = {}

    class _RPC:
        def __init__(self, name, params):
            self.name, self.params = name, params

        def execute(self):
            with lock:
                if self.name == "wow_rundown_reserve_call":
                    p = self.params
                    day, rid = p["p_utc_day"], p["p_request_id"]
                    state = days.setdefault(day, dict(calls=0, points=0, pending=0, unknown=0))
                    if rid in requests:
                        result = {"allowed": False, "code": "PAID_PROVIDER_REQUEST_ID_REUSED"}
                    elif state["pending"] or state["unknown"]:
                        result = {"allowed": False, "code": "PAID_PROVIDER_USAGE_UNRECONCILED"}
                    elif ((p["p_call_limit"] >= 0 and state["calls"] >= p["p_call_limit"])
                          or (p["p_point_limit"] >= 0 and state["points"] >= p["p_point_limit"])):
                        result = {"allowed": False, "code": "PAID_PROVIDER_BUDGET_EXHAUSTED"}
                    else:
                        requests[rid] = {"day": day, "state": "PENDING"}
                        state["calls"] += 1
                        state["pending"] += 1
                        result = {"allowed": True, "code": "PAID_PROVIDER_CALL_RESERVED"}
                elif self.name == "wow_rundown_finish_call":
                    rid = self.params["p_request_id"]
                    rec = requests.get(rid)
                    if rec is None:
                        result = {"ok": False, "code": "PAID_PROVIDER_RESERVATION_NOT_FOUND"}
                    elif rec["state"] != "PENDING":
                        result = {"ok": False, "code": "PAID_PROVIDER_RESERVATION_ALREADY_FINAL"}
                    else:
                        state = days[rec["day"]]
                        value = self.params["p_datapoints"]
                        state["pending"] -= 1
                        if value is None:
                            state["unknown"] += 1
                        else:
                            state["points"] += value
                        rec["state"] = "UNKNOWN" if value is None else "SETTLED"
                        result = {"ok": True, "code": "PAID_PROVIDER_CALL_SETTLED"}
                else:
                    raise RuntimeError("UNKNOWN_BUDGET_RPC")
                return SimpleNamespace(data=result)

    class _Client:
        def rpc(self, name, params):
            return _RPC(name, params)

    rundown_datapoint_budget.register_client(_Client)
    yield
    rundown_datapoint_budget._reset_for_tests()
