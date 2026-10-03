"""Persistent worker-built serving cache for NCAAF spread-forward shadow.

This module does not fit or select a sporting model. It only serializes and
validates the exact existing MarginDistributionArtifact plus the settled sporting
history required by the forward feature builder. Heavy fitting remains owned by
v17.spread_margin_forward_fit and is executed on the governed agent worker.

The cache is research/shadow serving infrastructure:
- source fingerprint is immutable-ledger derived;
- model family/schema/ridge settings are validated on read;
- probability publication/certification/promotion stay false;
- can_execute is always false.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
import zlib
from typing import Any, Mapping, Sequence

from redis import Redis

from v17.spread_margin_challenger import (
    MODEL_PROGRAM,
    MarginDistributionArtifact,
    SpreadChallengerUnavailable,
)

CACHE_VERSION = "NCAAF_SPREAD_FORWARD_SERVING_CACHE_V1"
CACHE_PREFIX = "wow:v17:ncaaf:spread-forward"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
BUILD_LOCK_TTL_SECONDS = 15 * 60
EXPECTED_MODEL_FAMILY = "NCAAF_SPREAD_MARGIN_RIDGE_EMPIRICAL_V1"
EXPECTED_FEATURE_SCHEMA_VERSION = "NCAAF_SPREAD_MARGIN_TEAM_STATE_V1"
EXPECTED_RIDGE_ALPHA = 4.0


def _redis() -> Redis:
    url = str(os.getenv("REDIS_URL") or "").strip()
    if not url:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_SERVING_CACHE_REDIS_UNAVAILABLE",
            "REDIS_URL is unavailable for the spread serving cache",
        )
    return Redis.from_url(url, decode_responses=False, socket_connect_timeout=5, socket_timeout=5)


def source_fingerprint_key(source_fingerprint: Sequence[Any]) -> str:
    values = tuple(None if value is None else str(value) for value in source_fingerprint)
    raw = json.dumps(values, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def ready_key(source_fingerprint: Sequence[Any]) -> str:
    return f"{CACHE_PREFIX}:{CACHE_VERSION}:ready:{source_fingerprint_key(source_fingerprint)}"


def build_lock_key() -> str:
    return f"{CACHE_PREFIX}:{CACHE_VERSION}:build-lock"


def _artifact_payload(artifact: MarginDistributionArtifact) -> dict[str, Any]:
    payload = artifact.payload()
    payload["can_execute"] = False
    payload["probability_publishable"] = False
    payload["automatic_certification"] = False
    payload["automatic_promotion"] = False
    return payload


def artifact_from_payload(payload: Mapping[str, Any]) -> MarginDistributionArtifact:
    if str(payload.get("program") or "") != MODEL_PROGRAM:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_SERVING_CACHE_MODEL_PROGRAM_MISMATCH",
            "cached spread artifact program does not match the active challenger",
        )
    if str(payload.get("model_family") or "") != EXPECTED_MODEL_FAMILY:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_SERVING_CACHE_MODEL_FAMILY_MISMATCH",
            "cached spread artifact model family does not match the active challenger",
        )
    if str(payload.get("feature_schema_version") or "") != EXPECTED_FEATURE_SCHEMA_VERSION:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_SERVING_CACHE_FEATURE_SCHEMA_MISMATCH",
            "cached spread artifact feature schema does not match the active challenger",
        )
    try:
        ridge_alpha = float(payload.get("ridge_alpha"))
    except (TypeError, ValueError) as exc:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_SERVING_CACHE_RIDGE_INVALID",
            "cached spread artifact ridge alpha is invalid",
        ) from exc
    if abs(ridge_alpha - EXPECTED_RIDGE_ALPHA) > 1e-12:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_SERVING_CACHE_RIDGE_MISMATCH",
            "cached spread artifact ridge alpha does not match the active challenger",
        )
    if payload.get("can_execute") is not False or payload.get("probability_publishable") is not False:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_SERVING_CACHE_GOVERNANCE_INVALID",
            "cached spread artifact violates shadow-only governance",
        )

    required_sequences = (
        "feature_names",
        "scaler_mean",
        "scaler_scale",
        "coefficients",
        "calibration_residuals",
    )
    for name in required_sequences:
        if not isinstance(payload.get(name), (list, tuple)) or not payload.get(name):
            raise SpreadChallengerUnavailable(
                "SPREAD_FORWARD_SERVING_CACHE_ARTIFACT_INVALID",
                f"cached spread artifact field {name} is missing or invalid",
            )

    return MarginDistributionArtifact(
        sport="NCAAF",
        model_family=EXPECTED_MODEL_FAMILY,
        feature_schema_version=EXPECTED_FEATURE_SCHEMA_VERSION,
        feature_names=tuple(str(v) for v in payload["feature_names"]),
        scaler_mean=tuple(float(v) for v in payload["scaler_mean"]),
        scaler_scale=tuple(float(v) for v in payload["scaler_scale"]),
        coefficients=tuple(float(v) for v in payload["coefficients"]),
        intercept=float(payload["intercept"]),
        calibration_residuals=tuple(float(v) for v in payload["calibration_residuals"]),
        train_rows=int(payload["train_rows"]),
        calibration_rows=int(payload["calibration_rows"]),
        test_rows=int(payload["test_rows"]),
        training_dataset_hash=str(payload["training_dataset_hash"]),
        ridge_alpha=ridge_alpha,
    )


def encode_context(
    *,
    artifact: MarginDistributionArtifact,
    settled_events: Sequence[Mapping[str, Any]],
    source_fingerprint: Sequence[Any],
    latest_training_event: str,
) -> bytes:
    fingerprint = [None if value is None else str(value) for value in source_fingerprint]
    envelope = {
        "cache_version": CACHE_VERSION,
        "source_fingerprint": fingerprint,
        "artifact": _artifact_payload(artifact),
        "settled_events": [dict(row) for row in settled_events],
        "latest_training_event": str(latest_training_event),
        "training_dataset_hash": artifact.training_dataset_hash,
        "model_program": MODEL_PROGRAM,
        "probability_publishable": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }
    raw = json.dumps(envelope, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return zlib.compress(raw, level=6)


def decode_context(raw: bytes, *, expected_source_fingerprint: Sequence[Any]) -> dict[str, Any]:
    try:
        envelope = json.loads(zlib.decompress(raw).decode("utf-8"))
    except Exception as exc:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_SERVING_CACHE_DECODE_FAILED",
            "spread serving cache payload could not be decoded",
        ) from exc
    if not isinstance(envelope, dict) or envelope.get("cache_version") != CACHE_VERSION:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_SERVING_CACHE_VERSION_MISMATCH",
            "spread serving cache version is not accepted",
        )
    expected = [None if value is None else str(value) for value in expected_source_fingerprint]
    if list(envelope.get("source_fingerprint") or []) != expected:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_SERVING_CACHE_SOURCE_MISMATCH",
            "spread serving cache source fingerprint is stale",
        )
    if (
        envelope.get("can_execute") is not False
        or envelope.get("probability_publishable") is not False
        or envelope.get("automatic_certification") is not False
        or envelope.get("automatic_promotion") is not False
    ):
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_SERVING_CACHE_GOVERNANCE_INVALID",
            "spread serving cache governance flags are invalid",
        )
    artifact = artifact_from_payload(dict(envelope.get("artifact") or {}))
    settled = envelope.get("settled_events")
    if not isinstance(settled, list) or not settled:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_SERVING_CACHE_HISTORY_INVALID",
            "spread serving cache has no settled sporting history",
        )
    if str(envelope.get("training_dataset_hash") or "") != artifact.training_dataset_hash:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_SERVING_CACHE_HASH_MISMATCH",
            "spread serving cache artifact hash metadata is inconsistent",
        )
    return {
        "artifact": artifact,
        "settled_events": tuple(dict(row) for row in settled if isinstance(row, dict)),
        "latest_training_event": str(envelope.get("latest_training_event") or ""),
        "source_fingerprint": tuple(expected_source_fingerprint),
        "cache_version": CACHE_VERSION,
        "can_execute": False,
    }


def load_ready_context(source_fingerprint: Sequence[Any]) -> dict[str, Any]:
    try:
        raw = _redis().get(ready_key(source_fingerprint))
    except SpreadChallengerUnavailable:
        raise
    except Exception as exc:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_SERVING_CACHE_REDIS_UNAVAILABLE",
            "spread serving cache read failed",
        ) from exc
    if not raw:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_SERVING_CACHE_BUILD_PENDING",
            "worker-built spread serving cache is not ready for the current source fingerprint",
        )
    return decode_context(bytes(raw), expected_source_fingerprint=source_fingerprint)


def persist_ready_context(
    *,
    artifact: MarginDistributionArtifact,
    settled_events: Sequence[Mapping[str, Any]],
    source_fingerprint: Sequence[Any],
    latest_training_event: str,
) -> dict[str, Any]:
    payload = encode_context(
        artifact=artifact,
        settled_events=settled_events,
        source_fingerprint=source_fingerprint,
        latest_training_event=latest_training_event,
    )
    key = ready_key(source_fingerprint)
    redis = _redis()
    redis.set(key, payload, ex=CACHE_TTL_SECONDS)
    return {
        "status": "READY",
        "code": "SPREAD_FORWARD_SERVING_CACHE_READY",
        "cache_version": CACHE_VERSION,
        "cache_key_sha256": source_fingerprint_key(source_fingerprint),
        "compressed_bytes": len(payload),
        "training_dataset_hash": artifact.training_dataset_hash,
        "probability_publishable": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }


def acquire_build_lock() -> tuple[Redis, str] | tuple[None, None]:
    redis = _redis()
    token = uuid.uuid4().hex
    acquired = redis.set(build_lock_key(), token.encode("ascii"), nx=True, ex=BUILD_LOCK_TTL_SECONDS)
    if not acquired:
        return None, None
    return redis, token


def release_build_lock(redis: Redis | None, token: str | None) -> None:
    if redis is None or token is None:
        return
    try:
        current = redis.get(build_lock_key())
        if current == token.encode("ascii"):
            redis.delete(build_lock_key())
    except Exception:
        return


def dispatch_build() -> str:
    try:
        from agent_runtime.queue import celery_app
        result = celery_app.send_task(
            "wow.v17.build_ncaaf_spread_serving_cache",
            queue="wow-agent",
        )
    except Exception as exc:
        raise SpreadChallengerUnavailable(
            "SPREAD_FORWARD_SERVING_CACHE_DISPATCH_FAILED",
            "spread serving cache worker dispatch failed",
        ) from exc
    return str(result.id or "")


__all__ = [
    "BUILD_LOCK_TTL_SECONDS",
    "CACHE_TTL_SECONDS",
    "CACHE_VERSION",
    "acquire_build_lock",
    "artifact_from_payload",
    "decode_context",
    "dispatch_build",
    "encode_context",
    "load_ready_context",
    "persist_ready_context",
    "ready_key",
    "release_build_lock",
    "source_fingerprint_key",
]
