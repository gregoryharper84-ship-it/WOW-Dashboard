"""Opt-in attachment of the existing bounded Kalshi Weather cohort controller.

This is NOT a second collector, writer lease, or evidence authority. The shared
WOW API keeps its own collector disabled. The isolated runtime must explicitly
claim ownership before this code installs an inbound trigger or startup task.
The deploy controller must verify singleton ownership before setting the flag.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

OWNER_FLAG = "WOW_KALSHI_WEATHER_ISOLATED_CAPTURE_OWNER"
STARTUP_FLAG = "WOW_KALSHI_WEATHER_EMPIRICAL_COHORT_ENABLED"


def install_isolated_capture_if_owned(
    app: Any,
    *,
    db_client_fn: Callable[[], object],
    installer: Callable[..., None],
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Attach the *existing* controller, only with explicit isolated ownership.

    This guard does not claim distributed fencing. Owner configuration is a
    deployment prerequisite; production activation needs a separate readback
    establishing that no other live service has an active collector.
    """
    env = os.environ if env is None else env
    owner = env.get(OWNER_FLAG, "0") == "1"
    startup_requested = env.get(STARTUP_FLAG, "0") == "1"
    if not owner:
        # A global capture-on flag accidentally copied from the shared service
        # must not start a second writer on this dedicated deployment.
        return {
            "status": "ISOLATED_COLLECTOR_NOT_OWNER",
            "trigger_installed": False,
            "startup_requested": startup_requested,
            "startup_enabled": False,
            "probability_publishable": False,
            "can_execute": False,
        }
    installer(app, db_client_fn=db_client_fn)
    return {
        "status": "EXISTING_COLLECTOR_ATTACHED_NOT_PRODUCTION_VERIFIED",
        "trigger_installed": True,
        "startup_requested": startup_requested,
        "startup_enabled": startup_requested,
        "probability_publishable": False,
        "can_execute": False,
    }
