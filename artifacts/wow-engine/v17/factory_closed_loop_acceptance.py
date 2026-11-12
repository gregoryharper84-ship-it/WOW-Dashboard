"""Nonblocking evidence checker for WOW's *existing* native repair factory.

This module deliberately has no credentials, HTTP, scheduling, queue, Git writes,
lease mutation, deployment, or probability authority. It checks externally
collected, authenticated receipts; it never creates those receipts. A green CI
run alone cannot satisfy production acceptance.

Issue: WOW-Dashboard #2219. V17_TERMINAL_REDUCER remains controlling.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
import re
import sys
from typing import Any, Mapping, Sequence

QA_APP_ID = 5245832
RELEASE_APP_ID = 5245853
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ALLOWED_TRIGGERS = frozenset({"SCHEDULED", "EVENT_DRIVEN"})
REQUIRED_STAGE_TYPES = (
    "DETECTED", "TRIAGED", "LEASE_ACQUIRED", "PATCH_PRODUCED",
    "TESTS_PASSED", "QA_PASSED", "RELEASE_PASSED", "MERGED",
    "STAGING_VERIFIED", "PRODUCTION_VERIFIED", "NEGATIVE_CANARY_PASSED",
    "LEASE_RELEASED",
)
PRIVILEGED_EVENTS = frozenset({"LEASE_ACQUIRED", "PATCH_PRODUCED", "LEASE_RELEASED"})


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo is not None else None
    except ValueError:
        return None


def _sha(value: Any) -> bool:
    return isinstance(value, str) and SHA_RE.fullmatch(value) is not None


def _verified(row: Mapping[str, Any]) -> bool:
    # Never infer authenticity from an event type or text alone.
    return row.get("authenticated") is True and bool(row.get("receipt_id"))


def _reason(reasons: list[str], code: str) -> None:
    if code not in reasons:
        reasons.append(code)


def assess_incident(events: Sequence[Mapping[str, Any]], *, class_name: str = "B",
                    source_attested: bool = False) -> dict[str, Any]:
    """Check one complete, chronological incident trail without mutating anything.

    All passed-in data is evidence *claims*. "authenticated" is a signal to be
    supplied by an independent receipt adapter; this checker cannot prove a
    caller did not forge JSON. Production closure requires a separately verified
    source adapter and an authentic external readback.
    """
    reasons: list[str] = []
    if not source_attested:
        _reason(reasons, "EXTERNAL_SOURCE_ATTESTATION_REQUIRED")
    if not events:
        return {"status": "UNVERIFIED", "reason_codes": ["NO_RECEIPTS"], "can_execute": False}
    ids = {str(x.get("incident_id") or "") for x in events}
    if len(ids) != 1 or "" in ids:
        _reason(reasons, "INCIDENT_IDENTITY_MISMATCH")
    incident_id = next(iter(ids)) if len(ids) == 1 else "UNRESOLVED"
    ordered = sorted(events, key=lambda r: str(r.get("observed_at") or ""))
    if list(events) != ordered or any(_utc(x.get("observed_at")) is None for x in events):
        _reason(reasons, "RECEIPT_CHRONOLOGY_UNVERIFIED")
    if any(x.get("can_execute") is not False for x in events):
        _reason(reasons, "EXECUTION_SAFETY_INVARIANT_FAILED")
    if any(not _verified(x) for x in events):
        _reason(reasons, "UNAUTHENTICATED_RECEIPT")
    stages: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in events:
        stages[str(row.get("event_type") or "")].append(row)
    for name in REQUIRED_STAGE_TYPES:
        if not stages[name]:
            _reason(reasons, f"MISSING_{name}")
    detected = stages["DETECTED"]
    fingerprints = {str(x.get("fingerprint") or "") for x in detected}
    if not detected or "" in fingerprints or len(fingerprints) != 1:
        _reason(reasons, "FINGERPRINT_UNRESOLVED")
    # Duplicate detection is allowed (e.g. Sentry + Actions) but never two tickets.
    if any(x.get("canonical_incident_id") != incident_id for x in detected):
        _reason(reasons, "DEDUPE_CANONICAL_ID_MISMATCH")

    cycles = stages["SUPERVISOR_CYCLE"]
    valid_cycles = [x for x in cycles if _verified(x) and
                    x.get("trigger") in ALLOWED_TRIGGERS and
                    x.get("unattended") is True and
                    x.get("advanced_work") is True and x.get("cycle_id")]
    distinct_cycles = {str(x["cycle_id"]) for x in valid_cycles}
    sequences = {x.get("cycle_sequence") for x in valid_cycles if type(x.get("cycle_sequence")) is int}
    consecutive = any((seq + 1) in sequences for seq in sequences)
    if len(distinct_cycles) < 2 or not consecutive:
        _reason(reasons, "TWO_UNATTENDED_PROGRESS_CYCLES_MISSING")
    # Multiple entries with the same cycle ID are not independent observations.
    if len(stages["LEASE_ACQUIRED"]) != 1:
        _reason(reasons, "SINGLE_WRITER_LEASE_UNVERIFIED")
    lease = stages["LEASE_ACQUIRED"][0] if stages["LEASE_ACQUIRED"] else {}
    worker = lease.get("worker_id")
    epoch = lease.get("epoch")
    if not isinstance(worker, str) or not worker or type(epoch) is not int or epoch < 0:
        _reason(reasons, "LEASE_IDENTITY_INVALID")
    for row in stages["PATCH_PRODUCED"]:
        if row.get("worker_id") != worker or row.get("epoch") != epoch or \
                row.get("write_fence_confirmed") is not True:
            _reason(reasons, "PATCH_WRITER_NOT_FENCED")

    patch = stages["PATCH_PRODUCED"][-1] if stages["PATCH_PRODUCED"] else {}
    head_sha = patch.get("head_sha")
    if not _sha(head_sha):
        _reason(reasons, "PATCH_HEAD_SHA_INVALID")
    for row in stages["TESTS_PASSED"]:
        if row.get("head_sha") != head_sha or row.get("passed") is not True or \
                type(row.get("test_count")) is not int or row.get("test_count", 0) < 1:
            _reason(reasons, "EXACT_HEAD_TESTS_UNVERIFIED")
    # Class A may use policy-authorized deterministic checks, not synthetic semantic review.
    if class_name not in {"A", "B", "C"}:
        _reason(reasons, "CHANGE_CLASS_INVALID")
    if class_name in {"B", "C"}:
        reviews = stages["INDEPENDENT_REVIEW_PASSED"]
        if not reviews or not any(x.get("head_sha") == head_sha and
                                 x.get("reviewer_principal") and
                                 x.get("reviewer_principal") != x.get("source_author") and
                                 x.get("semantic_review") is True for x in reviews):
            _reason(reasons, "INDEPENDENT_REVIEW_EVIDENCE_MISSING")
    for event_name, expected_id in (("QA_PASSED", QA_APP_ID),
                                    ("RELEASE_PASSED", RELEASE_APP_ID)):
        if not any(x.get("head_sha") == head_sha and x.get("app_id") == expected_id and
                   x.get("conclusion") == "success" for x in stages[event_name]):
            _reason(reasons, f"{event_name}_EXACT_HEAD_PROVENANCE_INVALID")
    merged = stages["MERGED"][-1] if stages["MERGED"] else {}
    merge_sha = merged.get("merge_sha")
    if merged.get("source_head_sha") != head_sha or not _sha(merge_sha) or \
            merged.get("protected_branch") != "main" or merged.get("merged") is not True:
        _reason(reasons, "PROTECTED_MERGE_UNVERIFIED")
    for event_name in ("STAGING_VERIFIED", "PRODUCTION_VERIFIED"):
        for row in stages[event_name]:
            if row.get("merge_sha") != merge_sha or row.get("web_sha") != merge_sha or \
                    row.get("worker_sha") != merge_sha or row.get("web_status") != "LIVE" or \
                    row.get("worker_status") != "LIVE" or row.get("smoke_passed") is not True:
                _reason(reasons, f"{event_name}_SHA_PARITY_OR_HEALTH_FAILED")
    for row in stages["NEGATIVE_CANARY_PASSED"]:
        if row.get("merge_sha") != merge_sha or row.get("stale_writer_rejected") is not True or \
                row.get("false_green_rejected") is not True:
            _reason(reasons, "NEGATIVE_CANARY_INVALID")
    for row in stages["LEASE_RELEASED"]:
        if row.get("worker_id") != worker or row.get("epoch") != epoch or \
                row.get("cas_verified") is not True or row.get("remote_terminal_verified") is not True or \
                row.get("no_inflight_writes") is not True:
            _reason(reasons, "LEASE_RELEASE_NOT_SAFE")

    # A stage's existence is insufficient: preserve the causal order.
    first = {}
    for event_name, rows in stages.items():
        if rows:
            first[event_name] = _utc(rows[0].get("observed_at"))
    chronological_stages = tuple(x for x in REQUIRED_STAGE_TYPES if x != "LEASE_RELEASED")
    for left, right in zip(chronological_stages, chronological_stages[1:]):
        if first.get(left) and first.get(right) and first[left] > first[right]:
            _reason(reasons, "STAGE_ORDER_INVALID")
    if first.get("LEASE_RELEASED") and first.get("PATCH_PRODUCED") and first["LEASE_RELEASED"] < first["PATCH_PRODUCED"]:
        _reason(reasons, "LEASE_RELEASE_BEFORE_WRITER_COMPLETION")
    if stages["INDEPENDENT_REVIEW_PASSED"] and first.get("PATCH_PRODUCED") and first.get("QA_PASSED"):
        t = first["INDEPENDENT_REVIEW_PASSED"]
        if t and not (first["PATCH_PRODUCED"] <= t <= first["QA_PASSED"]):
            _reason(reasons, "REVIEW_ORDER_INVALID")
    status = "VERIFIED_CLOSED_LOOP" if not reasons else "UNVERIFIED"
    return {
        "incident_id": incident_id,
        "status": status,
        "reason_codes": reasons,
        "distinct_unattended_progress_cycles": len(distinct_cycles),
        "observed_receipts": len(events),
        "deduplicated_detection_count": max(len(detected) - 1, 0),
        "can_execute": False,
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "read_only": True,
        "note": "Evidence authenticity must be confirmed by the external source adapter.",
    }


def assess_batch(document: Mapping[str, Any], *, source_attested: bool = False) -> dict[str, Any]:
    items = document.get("incidents")
    if not isinstance(items, list):
        raise ValueError("incidents must be a list")
    assessments = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("receipts"), list):
            raise ValueError("each incident requires a receipts list")
        assessments.append(assess_incident(item["receipts"], class_name=item.get("class", "B"),
                                           source_attested=source_attested))
    counts = Counter(row["status"] for row in assessments)
    return {"incidents": assessments, "summary": dict(sorted(counts.items())),
            "read_only": True, "can_execute": False,
            "terminal_authority": "V17_TERMINAL_REDUCER"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline, read-only WOW V17 factory acceptance audit")
    parser.add_argument("receipts_json", help="JSON document of preauthenticated receipt claims")
    args = parser.parse_args(argv)
    try:
        with open(args.receipts_json, encoding="utf-8") as file:
            data = json.load(file)
        output = assess_batch(data)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "INPUT_INVALID", "error_type": type(exc).__name__,
                          "can_execute": False}), file=sys.stdout)
        return 2
    print(json.dumps(output, indent=2, sort_keys=True))
    # Raw JSON is untrusted, so CLI never claims verified production closure.
    # Never blocks a native release or auto-repair process via an UNVERIFIED finding.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())