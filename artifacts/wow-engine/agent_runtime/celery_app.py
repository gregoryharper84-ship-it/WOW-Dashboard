"""Celery entrypoint that imports registered WOW Agent Runtime tasks.
Ported from PR #33 (feature/wow-agent-runtime-v1) during the convergence
pass. Run as: celery -A agent_runtime.celery_app:celery_app worker.
"""
from agent_runtime.queue import celery_app
from agent_runtime import runner as _runner  # noqa: F401
from agent_runtime import durable_runner as _durable_runner  # noqa: F401
from v17.engineering_auditor_worker import install_celery_worker_hooks
from v17.engineering_langgraph_runtime import (
    install_celery_worker_hooks as install_engineering_langgraph_hooks,
)

from v17.engineering_resident_supervisor import (install_celery_worker_hooks as install_resident_engineering_hooks)

install_celery_worker_hooks()
install_engineering_langgraph_hooks()
install_resident_engineering_hooks()

__all__ = ["celery_app"]
