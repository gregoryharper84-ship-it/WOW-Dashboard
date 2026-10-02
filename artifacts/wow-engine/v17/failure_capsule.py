"""Sanitized engineering failure capsules for WOW V17.

This module is engineering reliability infrastructure only. It does not score
sporting events, alter probability packages, or grant execution authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

RUNTIME_GENERATION = "V17_ACTIVE"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CAPSULE_SCHEMA_VERSION = "WOW_FAILURE_CAPSULE_V1"
REDACTED = "<redacted>"

_SENSITIVE_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "password",
    "secret",
    "service_role",
    "session_token",
    "access_token",
    "refresh_token",
    "private_key",
)

_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_KEY_VALUE_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*([^\s,;]+)"
)
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
)
_WHITESPACE_RE = re.compile(r"\s+")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


def sanitize_text(value: str) -> str:
    """Redact common credential patterns from free text.

    The function is intentionally conservative: it does not try to preserve
    secret-shaped values for diagnostics. Stable engineering identities should
    be supplied through dedicated non-secret fields instead.
    """

    text = _BEARER_RE.sub("Bearer <redacted>", value)
    return _KEY_VALUE_RE.sub(lambda match: f"{match.group(1)}={REDACTED}", text)


def sanitize(value: Any) -> Any:
    """Recursively sanitize JSON-like engineering context."""

    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            cleaned[key_text] = REDACTED if _is_sensitive_key(key_text) else sanitize(item)
        return cleaned
    if isinstance(value, (list, tuple, set)):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize_text(repr(value))


def _stable_message(value: str | None) -> str | None:
    if not value:
        return None
    sanitized = sanitize_text(value)
    sanitized = _UUID_RE.sub("<uuid>", sanitized)
    sanitized = _WHITESPACE_RE.sub(" ", sanitized).strip().lower()
    return sanitized[:500]


def failure_fingerprint(*, route: str | None, sport: str | None, market_family: str | None,
                        scorer_stage: str | None, terminal_code: str | None,
                        exception_type: str | None, exception_message: str | None) -> str:
    """Return a stable dedup fingerprint for one engineering failure family."""

    stable = {
        "route": route or "UNKNOWN",
        "sport": (sport or "UNKNOWN").upper(),
        "market_family": (market_family or "UNKNOWN").upper(),
        "scorer_stage": (scorer_stage or "UNKNOWN").upper(),
        "terminal_code": terminal_code or "UNKNOWN",
        "exception_type": exception_type or "UNKNOWN",
        "exception_message": _stable_message(exception_message),
    }
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_failure_capsule(
    *,
    route: str | None = None,
    sport: str | None = None,
    market_family: str | None = None,
    scorer_stage: str | None = None,
    terminal_code: str | None = None,
    exception_type: str | None = None,
    exception_message: str | None = None,
    request_id: str | None = None,
    run_id: str | None = None,
    row_identity: Mapping[str, Any] | None = None,
    git_sha: str | None = None,
    deploy_id: str | None = None,
    specialist: str | None = None,
    model_family: str | None = None,
    model_version: str | None = None,
    artifact_id: str | None = None,
    calibrator_version: str | None = None,
    stage_timings_ms: Mapping[str, Any] | None = None,
    provider_states: Mapping[str, Any] | None = None,
    persistence_state: Mapping[str, Any] | None = None,
    feature_manifest: Mapping[str, Any] | None = None,
    stack_boundary: Sequence[str] | None = None,
    reproduction: Mapping[str, Any] | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Build a sanitized, non-executable engineering failure capsule."""

    fingerprint = failure_fingerprint(
        route=route,
        sport=sport,
        market_family=market_family,
        scorer_stage=scorer_stage,
        terminal_code=terminal_code,
        exception_type=exception_type,
        exception_message=exception_message,
    )
    observed = observed_at or utc_now_iso()
    capsule = {
        "schema_version": CAPSULE_SCHEMA_VERSION,
        "fingerprint": fingerprint,
        "occurrence_count": 1,
        "first_observed_at": observed,
        "last_observed_at": observed,
        "runtime_generation": RUNTIME_GENERATION,
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
        "route": route,
        "sport": sport,
        "market_family": market_family,
        "scorer_stage": scorer_stage,
        "terminal_code": terminal_code,
        "exception": {
            "type": exception_type,
            "message": _stable_message(exception_message),
        },
        "request_id": request_id,
        "run_id": run_id,
        "row_identity": row_identity or {},
        "git_sha": git_sha,
        "deploy_id": deploy_id,
        "specialist": specialist,
        "model_family": model_family,
        "model_version": model_version,
        "artifact_id": artifact_id,
        "calibrator_version": calibrator_version,
        "stage_timings_ms": stage_timings_ms or {},
        "provider_states": provider_states or {},
        "persistence_state": persistence_state or {},
        "feature_manifest": feature_manifest or {},
        "stack_boundary": list(stack_boundary or ()),
        "reproduction": reproduction or {},
    }
    return sanitize(capsule)


def merge_occurrence(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    """Merge a duplicate occurrence without changing the failure identity."""

    if existing.get("fingerprint") != incoming.get("fingerprint"):
        raise ValueError("failure fingerprints differ; refusing to merge distinct incidents")
    merged = deepcopy(dict(existing))
    merged["occurrence_count"] = int(existing.get("occurrence_count", 1)) + int(
        incoming.get("occurrence_count", 1)
    )
    merged["last_observed_at"] = incoming.get("last_observed_at") or existing.get("last_observed_at")
    return sanitize(merged)


def capsule_manifest_entry(capsule: Mapping[str, Any]) -> dict[str, Any]:
    """Return the Rapid Repair manifest entry carried by a capsule.

    A capsule may only request explicit pytest targets. Missing targets are a
    blocker, never implicit success and never a request to infer production data.
    """

    reproduction = capsule.get("reproduction")
    if not isinstance(reproduction, Mapping):
        reproduction = {}
    targets = reproduction.get("pytest")
    if not isinstance(targets, list) or not targets or not all(isinstance(item, str) for item in targets):
        return {
            "status": "BLOCKED_WITH_EXACT_REASON",
            "blocker": "FAILURE_CAPSULE_DETERMINISTIC_PYTEST_TARGETS_UNAVAILABLE",
            "fingerprint": capsule.get("fingerprint"),
            "pytest": [],
            "can_execute": False,
        }
    return {
        "status": "REPRODUCTION_READY",
        "fingerprint": capsule.get("fingerprint"),
        "description": reproduction.get("description") or capsule.get("terminal_code"),
        "pytest": list(targets),
        "can_execute": False,
    }


def write_capsule(path: Path, capsule: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sanitize(dict(capsule)), indent=2, sort_keys=True) + "\n", encoding="utf-8")
