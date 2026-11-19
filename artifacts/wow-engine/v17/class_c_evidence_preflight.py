"""Read-only, non-authorizing Class C candidate evidence packaging.

This checks structural completeness, never the truth of source rights, fitted
quality, or independent approval. Existing sport certification and
V17_TERMINAL_REDUCER remain authoritative.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

CAN_EXECUTE = False
REQUIRED_EVIDENCE = (
    "source_rights", "chronological_split", "leakage", "holdout",
    "prospective_forward", "calibration", "lower_bound", "failure_paths",
)
MODEL_IDENTITY = (
    "sport", "market_family", "model_family", "model_version",
    "feature_schema_version", "source_contract_version", "controlling_specialist",
)
HASH_IDENTITIES = (
    "artifact_sha256", "calibrator_sha256", "dataset_sha256", "policy_sha256",
)
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
SHA_RE = re.compile(r"^[a-f0-9]{40}$")


def _digest(obj: Mapping[str, Any]) -> str:
    data = json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 256


def _aware_timestamp(value: Any) -> bool:
    if not _text(value):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.utcoffset() is not None and parsed <= datetime.now(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return False


def build_class_c_evidence_preflight(
    candidate: Mapping[str, Any] | None,
    evidence: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Freeze a stable manifest of *claimed* evidence for independent replay.

    Caller-owned PASS or reviewer fields are not authenticated approval. No
    registry, model routing, deployment or publication mutations occur here.
    """
    reasons: list[str] = []
    if not isinstance(candidate, Mapping):
        return _result(None, None, ["CLASS_C_CANDIDATE_INVALID"])
    if not isinstance(evidence, Mapping):
        return _result(None, None, ["CLASS_C_EVIDENCE_INVALID"])

    subject = {}
    for key in MODEL_IDENTITY:
        value = candidate.get(key)
        if not _text(value):
            reasons.append("CLASS_C_IDENTITY_INVALID:" + key)
        else:
            subject[key] = value.strip()
    for key in HASH_IDENTITIES:
        value = candidate.get(key)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            reasons.append("CLASS_C_HASH_INVALID:" + key)
        else:
            subject[key] = value
    for key in ("training_code_sha", "head_sha"):
        value = candidate.get(key)
        if not isinstance(value, str) or not SHA_RE.fullmatch(value):
            reasons.append("CLASS_C_SHA_INVALID:" + key)
        else:
            subject[key] = value
    if candidate.get("lifecycle_state") != "CANDIDATE":
        reasons.append("CLASS_C_RESEARCH_LIFECYCLE_REQUIRED")
    for flag in ("active", "promoted", "probability_publishable", "can_execute",
                 "automatic_promotion", "automatic_certification"):
        if candidate.get(flag) is not False:
            reasons.append("CLASS_C_INERTNESS_REQUIRED:" + flag)

    required_receipts = {}
    for category in REQUIRED_EVIDENCE:
        receipt = evidence.get(category)
        if not isinstance(receipt, Mapping):
            reasons.append("CLASS_C_EVIDENCE_MISSING:" + category)
            continue
        mandatory = ("receipt_id", "sha256", "subject_artifact_sha256", "head_sha", "observed_at")
        if not _text(receipt.get("receipt_id")):
            reasons.append("CLASS_C_RECEIPT_ID_INVALID:" + category)
        if not isinstance(receipt.get("sha256"), str) or not SHA256_RE.fullmatch(receipt["sha256"]):
            reasons.append("CLASS_C_RECEIPT_HASH_INVALID:" + category)
        if receipt.get("subject_artifact_sha256") != candidate.get("artifact_sha256"):
            reasons.append("CLASS_C_RECEIPT_ARTIFACT_MISMATCH:" + category)
        if receipt.get("head_sha") != candidate.get("head_sha"):
            reasons.append("CLASS_C_RECEIPT_HEAD_MISMATCH:" + category)
        if not _aware_timestamp(receipt.get("observed_at")):
            reasons.append("CLASS_C_RECEIPT_TIME_INVALID:" + category)
        if receipt.get("status") not in ("PASS", "HOLD", "REJECT"):
            reasons.append("CLASS_C_RECEIPT_STATUS_INVALID:" + category)
        elif receipt.get("status") != "PASS":
            reasons.append("CLASS_C_EVIDENCE_NOT_PASS:" + category)
        # Do not copy untrusted provider payload, credentials or approval claims.
        if all(field in receipt for field in mandatory):
            required_receipts[category] = {field: receipt[field] for field in mandatory}
            required_receipts[category]["claimed_status"] = receipt.get("status")

    for category in evidence:
        if not isinstance(category, str) or category not in REQUIRED_EVIDENCE:
            reasons.append("CLASS_C_EVIDENCE_CATEGORY_UNEXPECTED")
            break
    manifest = {"schema_version": 1, "subject": subject, "receipts": required_receipts}
    return _result(manifest, _digest(manifest), reasons)


def _result(manifest: Mapping[str, Any] | None, digest: str | None, reasons: list[str]) -> dict[str, Any]:
    return {
        "status": "STRUCTURALLY_READY_FOR_INDEPENDENT_REPLAY" if not reasons else "HOLD",
        "blockers": sorted(set(reasons)),
        "bundle_sha256": digest,
        "manifest": manifest,
        "independent_verification_status": "REQUIRED",
        "certification_status": "NOT_CERTIFIED",
        "automatic_certification": False,
        "automatic_promotion": False,
        "model_release_authorized": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
    }
