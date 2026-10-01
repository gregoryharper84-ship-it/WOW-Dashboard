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

from v17.failure_capsule import failure_fingerprint

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


def capture_wow_engineering_incident(
    *,
    route: str | None = None,
    sport: str | None = None,
    market_family: str | None = None,
    scorer_stage: str | None = None,
    terminal_code: str | None = None,
    exception_type: str | None = None,
    exception_message: str | None = None,
    specialist: str | None = None,
    model_version: str | None = None,
    artifact_id: str | None = None,
    run_id: str | None = None,
    deploy_id: str | None = None,
    source_provider: str | None = None,
) -> dict[str, Any]:
    """Capture one allow-listed engineering incident fingerprint, fail-open.

    Request bodies, authorization values, stack locals and probability payloads
    are deliberately absent. PostHog's native exception stack remains its normal
    error-grouping signal; this WOW fingerprint adds a stable engineering key
    that keeps sport/stage/typed-failure boundaries distinct.
    """

    fingerprint = failure_fingerprint(
        route=route,
        sport=sport,
        market_family=market_family,
        scorer_stage=scorer_stage,
        terminal_code=terminal_code,
        exception_type=exception_type,
        exception_message=exception_message,
    )
    if _POSTHOG_CLIENT is None:
        return {
            "status": "NOT_CAPTURED_TELEMETRY_DISABLED",
            "fingerprint": fingerprint,
            "can_execute": False,
        }

    properties = {
        "wow_failure_fingerprint": fingerprint,
        "runtime_generation": "V17_ACTIVE",
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "can_execute": False,
        "environment": os.getenv("WOW_ENVIRONMENT", "production"),
        "release": os.getenv("RENDER_GIT_COMMIT") or os.getenv("WOW_RELEASE_SHA"),
        "route": route,
        "sport": sport.upper() if sport else None,
        "market_family": market_family.upper() if market_family else None,
        "scorer_stage": scorer_stage,
        "terminal_code": terminal_code,
        "exception_type": exception_type,
        "specialist": specialist,
        "model_version": model_version,
        "artifact_id": artifact_id,
        "run_id": run_id,
        "deploy_id": deploy_id,
        "source_provider": source_provider,
    }
    properties = {key: value for key, value in properties.items() if value is not None}
    try:
        _POSTHOG_CLIENT.capture(
            "wow engineering incident",
            distinct_id="wow-governed-probability-engine",
            properties=properties,
        )
    except Exception as exc:  # telemetry must never affect scorer availability
        _LOGGER.warning(
            "PostHog engineering incident capture failed type=%s fingerprint=%s",
            type(exc).__name__,
            fingerprint,
        )
        return {
            "status": "CAPTURE_FAILED",
            "fingerprint": fingerprint,
            "error_type": type(exc).__name__,
            "can_execute": False,
        }
    return {
        "status": "CAPTURED",
        "fingerprint": fingerprint,
        "can_execute": False,
    }


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
