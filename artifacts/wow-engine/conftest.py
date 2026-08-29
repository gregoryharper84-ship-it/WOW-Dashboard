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
