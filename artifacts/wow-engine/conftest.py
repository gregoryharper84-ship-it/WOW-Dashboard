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



def pytest_configure(config):
    """Register a narrowly scoped marker for incident-learning proof."""
    config.addinivalue_line(
        "markers",
        "wow_incident_regression: Emit one authenticated named PASS line for incident closure.",
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Quiet-mode CI prints a named result only for marked passing tests.

    This is evidence output, not a CI decision or additional approval gate.
    """
    outcome = yield
    report = outcome.get_result()
    if (report.when == "call" and report.passed
            and item.get_closest_marker("wow_incident_regression") is not None):
        terminal = item.config.pluginmanager.get_plugin("terminalreporter")
        if terminal is not None:
            terminal.write_line(f"WOW_INCIDENT_REGRESSION_PASSED {report.nodeid} PASSED")
