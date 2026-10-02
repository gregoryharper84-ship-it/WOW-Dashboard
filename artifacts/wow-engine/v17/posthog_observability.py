"""Optional PostHog error telemetry for the governed V17 runtime.

This module is observability-only. It must never alter model selection, sporting
probabilities, terminal reducer semantics, or execution authority. The client is
created only when POSTHOG_PROJECT_API_KEY is configured and all initialization
failures are contained so telemetry cannot take the scoring service down.
"""
from __future__ import annotations

import hashlib
import json
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
    except Exception as exc:
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


_ENGINEERING_FINGERPRINT_FIELDS = (
    "sport",
    "market_family",
    "specialist",
    "model_family",
    "model_version",
    "route",
    "terminal_code",
    "provider_code",
    "scorer_stage",
    "runtime_generation",
)

_ENGINEERING_INCIDENT_PROPERTY_FIELDS = (
    *_ENGINEERING_FINGERPRINT_FIELDS,
    "artifact_id",
    "artifact_checksum_prefix",
    "run_id",
    "deploy_id",
    "git_sha",
    "source_provider",
)


def engineering_failure_fingerprint(properties: dict[str, Any]) -> str:
    """Return a stable allowlisted fingerprint for incident grouping."""
    safe = {
        key: str(properties[key])
        for key in _ENGINEERING_FINGERPRINT_FIELDS
        if properties.get(key) not in (None, "")
    }
    safe["can_execute"] = False
    canonical = json.dumps(safe, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def engineering_incident_properties(properties: dict[str, Any]) -> dict[str, Any]:
    """Build the safe metadata envelope consumed by telemetry/Failure Capsules.

    Volatile run/deploy identifiers remain available for correlation but are not
    part of the stable failure fingerprint. Arbitrary request bodies, exception
    text, credentials, prompts, probability payloads, and unknown keys are
    dropped by construction.
    """
    safe = {
        key: str(properties[key])
        for key in _ENGINEERING_INCIDENT_PROPERTY_FIELDS
        if properties.get(key) not in (None, "")
    }
    safe["runtime_generation"] = str(
        properties.get("runtime_generation") or "V17_ACTIVE"
    )
    safe["terminal_authority"] = "V17_TERMINAL_REDUCER"
    safe["can_execute"] = False
    safe["wow_failure_fingerprint"] = engineering_failure_fingerprint(safe)
    return safe


def capture_engineering_incident(properties: dict[str, Any]) -> dict[str, Any]:
    """Fail-open capture of one allowlisted engineering incident envelope."""
    safe = engineering_incident_properties(properties)
    if _POSTHOG_CLIENT is None:
        return {
            "status": "DISABLED_NOT_CONFIGURED",
            "captured": False,
            "wow_failure_fingerprint": safe["wow_failure_fingerprint"],
            "can_execute": False,
        }
    try:
        _POSTHOG_CLIENT.capture(
            "wow engineering incident",
            distinct_id="wow-governed-probability-engine",
            properties=safe,
        )
        return {
            "status": "CAPTURED",
            "captured": True,
            "wow_failure_fingerprint": safe["wow_failure_fingerprint"],
            "can_execute": False,
        }
    except Exception as exc:
        _LOGGER.warning(
            "PostHog engineering incident capture failed type=%s",
            type(exc).__name__,
        )
        return {
            "status": "CAPTURE_FAILED",
            "captured": False,
            "error_type": type(exc).__name__,
            "wow_failure_fingerprint": safe["wow_failure_fingerprint"],
            "can_execute": False,
        }
