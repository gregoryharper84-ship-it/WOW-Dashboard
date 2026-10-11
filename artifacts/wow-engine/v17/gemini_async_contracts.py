"""Deterministic Gemini Batch + explicit-cache contracts; NO network or spend.

Google Batch inline REST shape: /v1beta/models/{model}:batchGenerateContent.
Google cache REST shape: /v1beta/cachedContents.
This module deliberately cannot submit jobs, allocate a cache, or silently retry.
Provider outputs are evidence-only, never a model probability or QA release.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from v17.gemini_optional_worker import MAX_INPUT_CHARS, MODEL_PATTERN, TASKS
from v17.gemini_input_safety import (GeminiSensitiveMaterialError,
                                     ensure_no_obvious_sensitive_material)

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
CACHE_NAME_RE = re.compile(r"^cachedContents/[A-Za-z0-9_-]{1,128}$")
BATCH_NAME_RE = re.compile(r"^batches/[A-Za-z0-9_-]{1,128}$")
MAX_INLINE_ITEMS = 16
MAX_MANIFEST_BYTES = 256_000
MAX_CACHE_CHARS = 120_000
MAX_CACHE_TTL_SECONDS = 3600
BATCH_STATES = frozenset({
    "JOB_STATE_PENDING", "JOB_STATE_RUNNING", "JOB_STATE_SUCCEEDED",
    "JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED",
})
FINISHED_BAD = frozenset({"JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"})
DIRECTIVE = (
    "WOW evidence-only research challenger. JSON object with summary (string), "
    "findings (string array), evidence_refs (string array). No invented tests, "
    "probabilities, calibrated confidence, betting picks, QA or release approvals."
)


class GeminiContractError(Exception):
    """Typed fail-closed contract violation without user text or secrets."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _digest(payload: Any) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _model(value: Any) -> str:
    if not isinstance(value, str) or not MODEL_PATTERN.fullmatch(value):
        raise GeminiContractError("GEMINI_CONFIG_MISSING")
    return value


def _classification(value: Any) -> None:
    if value not in {"PUBLIC", "SANITIZED"}:
        raise GeminiContractError("GEMINI_INPUT_CLASS_BLOCKED")


def prepare_inline_batch(
    *, model: str, items: list[dict[str, Any]], urgency: str,
    data_classification: str,
) -> dict[str, Any]:
    """Prepare stable keyed Batch input; no network, retries, or billing.

    Every item must have key, task, prompt, evidence_refs (explicitly typed).
    Do not include protected source, credentials, personal data or live model data.
    """
    _model(model)
    _classification(data_classification)
    if urgency != "BACKGROUND":
        raise GeminiContractError("GEMINI_BATCH_LATENCY_INCOMPATIBLE")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_INLINE_ITEMS:
        raise GeminiContractError("GEMINI_BATCH_INPUT_INVALID")

    entries: list[dict[str, Any]] = []
    provenance: dict[str, list[str]] = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {"key", "task", "prompt", "evidence_refs"}:
            raise GeminiContractError("GEMINI_BATCH_INPUT_INVALID")
        key, task, prompt, refs = (item[x] for x in ("key", "task", "prompt", "evidence_refs"))
        if not isinstance(key, str) or not KEY_RE.fullmatch(key) or key in provenance:
            raise GeminiContractError("GEMINI_BATCH_IDENTITY_INVALID")
        if task not in TASKS or not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_INPUT_CHARS:
            raise GeminiContractError("GEMINI_BATCH_INPUT_INVALID")
        if (not isinstance(refs, list) or not refs or
                any(not isinstance(r, str) or not r.strip() or len(r) > 512 for r in refs)):
            raise GeminiContractError("GEMINI_BATCH_INPUT_INVALID")
        try:
            ensure_no_obvious_sensitive_material(prompt, *refs)
        except GeminiSensitiveMaterialError:
            raise GeminiContractError("GEMINI_SENSITIVE_MATERIAL_REJECTED") from None
        provenance[key] = list(refs)
        entries.append({
            "metadata": {"key": key},
            "request": {
                "contents": [{"role": "user", "parts": [{"text": DIRECTIVE + "\nTASK: " + task
                                                             + "\nMATERIAL:\n" + prompt}]}],
                "generationConfig": {"responseMimeType": "application/json",
                                     "maxOutputTokens": 1024, "temperature": 0},
            },
        })
    payload = {
        "batch": {
            "display_name": "wow-gemini-evidence",
            "input_config": {"requests": {"requests": entries}},
        }
    }
    if len(json.dumps(payload, sort_keys=True).encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise GeminiContractError("GEMINI_BATCH_INPUT_INVALID")
    manifest = {"model": model, "request_keys": sorted(provenance), "evidence_refs": provenance,
                "payload_sha256": _digest(payload), "data_classification": data_classification,
                "urgency": urgency}
    return {
        "provider": "gemini", "kind": "BATCH_INLINE_PREPARED",
        "api_route": "/v1beta/models/" + model + ":batchGenerateContent",
        "api_payload": payload, "manifest": manifest,
        "manifest_sha256": _digest(manifest),
        "status": "PREPARED_NOT_SUBMITTED",
        "can_execute": False, "terminal_authority": TERMINAL_AUTHORITY,
    }


def _normalized_evidence(output: Any) -> dict[str, Any]:
    if not isinstance(output, dict) or not isinstance(output.get("candidates"), list):
        raise GeminiContractError("GEMINI_BATCH_RESPONSE_INVALID")
    try:
        first = output["candidates"][0]
        if first.get("finishReason") != "STOP":
            raise ValueError("unfinished")
        parts = first["content"]["parts"]
        content = "".join(x["text"] for x in parts if isinstance(x.get("text"), str))
        obj = json.loads(content)
        if (not isinstance(obj, dict) or not isinstance(obj.get("summary"), str)
            or not isinstance(obj.get("findings"), list)
            or not all(isinstance(x, str) for x in obj["findings"])
            or not isinstance(obj.get("evidence_refs"), list)
            or not all(isinstance(x, str) for x in obj["evidence_refs"])):
            raise ValueError("invalid evidence")
    except (ValueError, TypeError, IndexError, KeyError):
        raise GeminiContractError("GEMINI_BATCH_RESPONSE_INVALID") from None
    return {"summary": obj["summary"], "findings": obj["findings"],
            "evidence_refs": obj["evidence_refs"],
            "verification_state": "UNVERIFIED_CHALLENGER_OUTPUT"}


def reconcile_inline_batch(
    response: dict[str, Any], *, expected_batch_name: str,
    expected_keys: list[str],
) -> dict[str, Any]:
    """Reconcile by explicit provider keys. NEVER assume output list order."""
    if not BATCH_NAME_RE.fullmatch(expected_batch_name) or not isinstance(response, dict):
        raise GeminiContractError("GEMINI_BATCH_IDENTITY_INVALID")
    if response.get("name") != expected_batch_name:
        raise GeminiContractError("GEMINI_BATCH_IDENTITY_INVALID")
    if (not isinstance(expected_keys, list) or not expected_keys or
        any(not isinstance(k, str) or not KEY_RE.fullmatch(k) for k in expected_keys)
            or len(set(expected_keys)) != len(expected_keys)):
        raise GeminiContractError("GEMINI_BATCH_IDENTITY_INVALID")
    metadata = response.get("metadata")
    meta_state = metadata.get("state") if isinstance(metadata, dict) else None
    state = meta_state or response.get("state")
    if state not in BATCH_STATES:
        raise GeminiContractError("GEMINI_BATCH_RESPONSE_INVALID")
    if state in FINISHED_BAD:
        return {"batch_name": expected_batch_name, "status": "TERMINAL_HOLD",
                "provider_state": state, "rows": [], "can_execute": False}
    if state != "JOB_STATE_SUCCEEDED":
        return {"batch_name": expected_batch_name, "status": "PENDING",
                "provider_state": state, "rows": [], "can_execute": False}
    destination = response.get("response") or response.get("dest") or {}
    rows = destination.get("inlinedResponses") if isinstance(destination, dict) else None
    if not isinstance(rows, list):
        # A file-based response is a distinct unimplemented reconciliation contract.
        raise GeminiContractError("GEMINI_BATCH_RESULTS_UNAVAILABLE")
    keyed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise GeminiContractError("GEMINI_BATCH_RESPONSE_INVALID")
        meta = row.get("metadata")
        key = meta.get("key") if isinstance(meta, dict) else row.get("key")
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            raise GeminiContractError("GEMINI_BATCH_RESULT_IDENTITY_MISSING")
        if key in keyed:
            raise GeminiContractError("GEMINI_BATCH_RESULT_IDENTITY_DUPLICATED")
        if "error" in row:
            raise GeminiContractError("GEMINI_BATCH_ROW_FAILED")
        keyed[key] = _normalized_evidence(row.get("response"))
    if set(keyed) != set(expected_keys):
        raise GeminiContractError("GEMINI_BATCH_RESULT_SET_MISMATCH")
    return {"batch_name": expected_batch_name, "status": "RECONCILED_UNVERIFIED",
            "provider_state": state,
            "rows": [{"key": key, **keyed[key]} for key in sorted(keyed)],
            "can_execute": False, "terminal_authority": TERMINAL_AUTHORITY}


def prepare_explicit_cache(
    *, model: str, content: str, ttl_seconds: int,
    data_classification: str, source_refs: list[str],
) -> dict[str, Any]:
    """Prepare an approved-excerpts-only cache payload. No CREATE/charge here."""
    _model(model)
    _classification(data_classification)
    if (not isinstance(content, str) or not content.strip() or
            len(content) > MAX_CACHE_CHARS or type(ttl_seconds) is not int or
            not 300 <= ttl_seconds <= MAX_CACHE_TTL_SECONDS):
        raise GeminiContractError("GEMINI_CACHE_INPUT_INVALID")
    if (not isinstance(source_refs, list) or not source_refs
        or any(not isinstance(r, str) or not r.strip() or len(r) > 512 for r in source_refs)):
        raise GeminiContractError("GEMINI_CACHE_INPUT_INVALID")
    try:
        ensure_no_obvious_sensitive_material(content, *source_refs)
    except GeminiSensitiveMaterialError:
        raise GeminiContractError("GEMINI_SENSITIVE_MATERIAL_REJECTED") from None
    # Cache contents remain external to WOW; caller still must perform an actual
    # data-classification/DLP review. A caller label is not a privacy guarantee.
    payload = {"model": "models/" + model,
               "contents": [{"role": "user", "parts": [{"text": content}]}],
               "ttl": str(ttl_seconds) + "s",
               "displayName": "wow-reviewed-excerpts"}
    source_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
    manifest = {
        "model": model, "payload_sha256": _digest(payload),
        "content_sha256": source_sha, "source_refs": list(source_refs),
        "data_classification": data_classification, "ttl_seconds": ttl_seconds,
    }
    return {
        "provider": "gemini", "kind": "CACHE_CREATE_PREPARED",
        "api_route": "/v1beta/cachedContents", "api_payload": payload,
        "manifest": manifest, "manifest_sha256": _digest(manifest),
        "content_sha256": source_sha, "source_refs": list(source_refs),
        "status": "PREPARED_NOT_CREATED", "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


def reconcile_cache_resource(
    response: dict[str, Any], *, expected_name: str,
    expected_model: str, evaluated_at: datetime,
) -> dict[str, Any]:
    """Normalize cache metadata only; never return/store cached source text."""
    if not isinstance(expected_name, str) or not CACHE_NAME_RE.fullmatch(expected_name):
        raise GeminiContractError("GEMINI_CACHE_IDENTITY_INVALID")
    _model(expected_model)
    if (not isinstance(response, dict) or response.get("name") != expected_name
        or response.get("model") not in {expected_model, "models/" + expected_model}):
        raise GeminiContractError("GEMINI_CACHE_IDENTITY_INVALID")
    if not isinstance(evaluated_at, datetime) or evaluated_at.tzinfo is None:
        raise GeminiContractError("GEMINI_CACHE_INPUT_INVALID")
    exp = response.get("expireTime")
    if not isinstance(exp, str):
        raise GeminiContractError("GEMINI_CACHE_EXPIRY_INVALID")
    try:
        parsed = datetime.fromisoformat(exp.replace("Z", "+00:00"))
    except ValueError:
        raise GeminiContractError("GEMINI_CACHE_EXPIRY_INVALID") from None
    if parsed.tzinfo is None:
        raise GeminiContractError("GEMINI_CACHE_EXPIRY_INVALID")
    valid = parsed > evaluated_at.astimezone(timezone.utc)
    return {"cache_name": expected_name, "model": expected_model,
            "status": "CACHE_AVAILABLE" if valid else "CACHE_EXPIRED",
            "expire_time": exp, "content": "REDACTED_NOT_EXPORTED",
            "can_execute": False, "terminal_authority": TERMINAL_AUTHORITY}
