"""Celery entrypoint that imports registered WOW Agent Runtime tasks.
Ported from PR #33 (feature/wow-agent-runtime-v1) during the convergence
pass. Run as: celery -A agent_runtime.celery_app:celery_app worker.
"""
from agent_runtime.queue import celery_app
from agent_runtime import runner as _runner  # noqa: F401
from agent_runtime import durable_runner as _durable_runner  # noqa: F401

__all__ = ["celery_app"]
