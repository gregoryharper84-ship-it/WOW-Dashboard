"""Celery entrypoint that imports registered WOW Agent Runtime tasks."""
from .queue import celery_app
from . import runner as _runner  # noqa: F401
from . import durable_runner as _durable_runner  # noqa: F401

__all__ = ["celery_app"]
