"""Optional PostHog error telemetry for the governed V17 runtime.

This module is observability-only. It must never alter model selection, sporting
probabilities, terminal reducer semantics, or execution authority. The client is
created only when POSTHOG_PROJECT_API_KEY is configured and all initialization
failures are contained so telemetry cannot take the scoring service down.
"""
from __future__ import annotations

import logging
import os
from typing import Any

_LOGGER = logging.getLogger("wow.v17.posthog")
_POSTHOG_CLIENT: Any | None = None
_POSTHOG_STATUS: dict[str, Any] = {
    "status": "DISABLED_NOT_CONFIGURED",
    "provider": "POSTHOG",
    "can_execute": False,
}


def posthog_observability_status() -> dict[str, Any]:
    """Return a copy of the current non-secret PostHog initialization status."""

    return dict(_POSTHOG_STATUS)


def initialize_posthog_observability() -> dict[str, Any]:
    """Initialize one fail-open PostHog client for the lifetime of this process."""

    global _POSTHOG_CLIENT, _POSTHOG_STATUS

    if _POSTHOG_CLIENT is not None:
        return posthog_observability_status()

    project_api_key = os.getenv("POSTHOG_PROJECT_API_KEY", "").strip()
    if not project_api_key:
        _POSTHOG_STATUS = {
            "status": "DISABLED_NOT_CONFIGURED",
            "provider": "POSTHOG",
            "can_execute": False,
        }
        return posthog_observability_status()

    host = os.getenv("POSTHOG_HOST", "https://us.i.posthog.com").strip()
    if not host:
        host = "https://us.i.posthog.com"

    try:
        from posthog import Posthog

        client = Posthog(
            project_api_key=project_api_key,
            host=host,
            enable_exception_autocapture=True,
            # Do not serialize local variables from model/scoring frames. This
            # keeps credentials, request bodies, and probability payloads out of
            # the telemetry surface by default.
            capture_exception_code_variables=False,
        )
        release = os.getenv("RENDER_GIT_COMMIT") or os.getenv("WOW_RELEASE_SHA")
        client.capture(
            "wow observability started",
            distinct_id="wow-governed-probability-engine",
            properties={
                "runtime_generation": "V17_ACTIVE",
                "terminal_authority": "V17_TERMINAL_REDUCER",
                "can_execute": False,
                "environment": os.getenv("WOW_ENVIRONMENT", "production"),
                "release": release,
            },
        )
        _POSTHOG_CLIENT = client
        _POSTHOG_STATUS = {
            "status": "ENABLED",
            "provider": "POSTHOG",
            "host": host,
            "exception_autocapture": True,
            "capture_exception_code_variables": False,
            "can_execute": False,
        }
    except Exception as exc:  # telemetry must never make production unavailable
        _POSTHOG_CLIENT = None
        _POSTHOG_STATUS = {
            "status": "INITIALIZATION_FAILED",
            "provider": "POSTHOG",
            "error_type": type(exc).__name__,
            "can_execute": False,
        }
        _LOGGER.warning(
            "PostHog observability initialization failed type=%s",
            type(exc).__name__,
        )

    return posthog_observability_status()
