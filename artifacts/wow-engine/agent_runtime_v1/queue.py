from __future__ import annotations
import os
from celery import Celery
from kombu import Queue

REDIS_URL=os.getenv("REDIS_URL","redis://127.0.0.1:6379/0")
celery_app=Celery("wow_agent_runtime",broker=REDIS_URL,backend=REDIS_URL)
celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    task_default_queue="wow-agent",
    task_queues=(Queue("wow-agent"),),
    result_expires=3600,
)
