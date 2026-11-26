"""Celery entrypoint that imports registered WOW Agent Runtime tasks.
Ported from PR #33 (feature/wow-agent-runtime-v1) during the convergence
pass. Run as: python -m celery -A agent_runtime.celery_app:celery_app worker.
"""
from agent_runtime.queue import celery_app
from agent_runtime import runner as _runner  # noqa: F401
from agent_runtime import durable_runner as _durable_runner  # noqa: F401
from agent_runtime import worker_release as _worker_release
from v17.engineering_auditor_worker import install_celery_worker_hooks
from v17.engineering_langgraph_runtime import (
    install_celery_worker_hooks as install_engineering_langgraph_hooks,
)

from v17.engineering_resident_supervisor import (install_celery_worker_hooks as install_resident_engineering_hooks)

install_celery_worker_hooks()
install_engineering_langgraph_hooks()
install_resident_engineering_hooks()
# Pin this worker's RENDER_GIT_COMMIT into wow_agent_worker_registry so the web
# service's authenticated /wow/deploy-identity readback can prove SHA parity
# without a Render API key.
_worker_release.install_celery_worker_hooks()

__all__ = ["celery_app"]
