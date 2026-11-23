"""Opt-in attachment of the isolated Kalshi Weather dispatch consumer.

The shared WOW API keeps its collector disabled. The dedicated runtime must
explicitly claim ownership before installing either the durable queue consumer
or the legacy in-process loop. The preferred queue path is fenced by an atomic
service-role claim using FOR UPDATE SKIP LOCKED; both scheduler modes cannot be
enabled at once.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

OWNER_FLAG = "WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER"
STARTUP_FLAG = "WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_ENABLED"
DISPATCH_QUEUE_FLAG = "WOW_KALSHI_WEATHER_EMPIRICAL_DISPATCH_QUEUE_ENABLED"


def install_isolated_capture_if_owned(
    app: Any,
    *,
    db_client_fn: Callable[[], object],
    installer: Callable[..., None],
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Attach exactly one isolated collection scheduler when ownership is explicit."""
    env = os.environ if env is None else env
    owner = env.get(OWNER_FLAG, "0") == "1"
    startup_requested = env.get(STARTUP_FLAG, "0") == "1"
    queue_requested = env.get(DISPATCH_QUEUE_FLAG, "0") == "1"

    base = {
        "trigger_installed": False,
        "dispatch_queue_worker_installed": False,
        "dispatch_queue_requested": queue_requested,
        "dispatch_queue_enabled": False,
        "startup_requested": startup_requested,
        "startup_enabled": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    if not owner:
        return {
            **base,
            "status": "ISOLATED_COLLECTOR_NOT_OWNER",
        }
    if queue_requested and startup_requested:
        return {
            **base,
            "status": "MULTIPLE_SCHEDULERS_BLOCKED",
        }

    installer(app, db_client_fn=db_client_fn)
    return {
        **base,
        "status": "EXISTING_COLLECTOR_ATTACHED_NOT_PRODUCTION_VERIFIED",
        "dispatch_queue_worker_installed": queue_requested,
        "dispatch_queue_enabled": queue_requested,
        "startup_enabled": startup_requested,
    }
