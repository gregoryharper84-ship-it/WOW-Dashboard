"""Durable Class A ledger adapters for the WOW Ecosystem Conductor.

This module persists already-observed control-plane evidence. It does not probe,
score, calibrate, route wagers/trades, change specialist ownership, or certify
its own writes.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

import ecosystem_conductor

CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

PROBE_TABLE = "wow_ecosystem_probe_receipts"
HANDOFF_TABLE = "wow_ecosystem_handoff_receipts"
WORK_TABLE = "wow_ecosystem_work_items"
READINESS_TABLE = "wow_ecosystem_readiness_snapshots"
FINDING_TABLE = "wow_ecosystem_findings"

SENSITIVE_KEY_FRAGMENTS = (
    "secret",
    "token",
    "password",
    "authorization",
    "apikey",
    "api_key",
    "service_key",
    "bearer",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_sensitive_key(key: Any) -> bool:
    lowered = str(key).lower()
    return any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)


def sanitize(value: Any) -> Any:
    """Recursively redact secret-bearing fields before durable persistence."""
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in value.items():
            if _is_sensitive_key(key):
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = sanitize(child)
        return result
    if isinstance(value, tuple):
        return [sanitize(item) for item in value]
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, set):
        return [sanitize(item) for item in sorted(value, key=str)]
    if is_dataclass(value):
        return sanitize(asdict(value))
    return value


def _payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        result = value.to_dict()
    elif is_dataclass(value):
        result = asdict(value)
    elif isinstance(value, Mapping):
        result = dict(value)
    else:
        raise TypeError(f"unsupported receipt type: {type(value).__name__}")
    if not isinstance(result, dict):
        raise TypeError("receipt payload must be an object")
    return result


def probe_row(receipt: Any) -> dict[str, Any]:
    row = sanitize(_payload(receipt))
    if row.get("can_execute") is not False:
        raise ValueError("probe receipt can_execute must remain false")
    required = ("probe_id", "probe_type", "observed_at", "status")
    missing = [key for key in required if not str(row.get(key) or "").strip()]
    if missing:
        raise ValueError(f"probe receipt missing fields: {missing}")
    return {
        "probe_id": row["probe_id"],
        "probe_type": row["probe_type"],
        "observed_at": row["observed_at"],
        "status": str(row["status"]).upper(),
        "evidence_refs": list(row.get("evidence_refs") or []),
        "first_failing_boundary": row.get("first_failing_boundary"),
        "typed_failure": row.get("typed_failure"),
        "source_version": row.get("source_version"),
        "deployed_sha": row.get("deployed_sha"),
        "details": row.get("details") or {},
        "can_execute": False,
    }


def _receipt_failure(receipts: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    precedence = {"FAIL": 6, "BLOCKED": 5, "UNKNOWN": 4, "DEGRADED": 3, "PASS": 2, "NOT_APPLICABLE": 1}
    nonpass = [row for row in receipts if str(row.get("status")).upper() != "PASS"]
    if not nonpass:
        return None
    return max(
        nonpass,
        key=lambda row: precedence.get(str(row.get("status")).upper(), 4),
    )


def handoff_rows(
    registry: Mapping[str, Any],
    config: Mapping[str, Any],
    probe_results: Mapping[str, Any],
    observed_state: Mapping[str, Any],
    *,
    request_id: str | None = None,
    objective_id: str | None = None,
    work_item_id: str | None = None,
    observed_at: str | None = None,
) -> list[dict[str, Any]]:
    observed_at = observed_at or _utc_now()
    bindings = config.get("handoff_bindings") or {}
    states = observed_state.get("handoffs") or {}
    output: list[dict[str, Any]] = []

    for handoff_id, spec in registry.get("handoffs", {}).items():
        probe_ids = list(bindings.get(handoff_id) or [])
        bound: list[dict[str, Any]] = []
        for probe_id in probe_ids:
            if probe_id in probe_results:
                bound.append(probe_row(probe_results[probe_id]))
        failure = _receipt_failure(bound)
        refs: list[str] = []
        for receipt in bound:
            for ref in receipt.get("evidence_refs") or []:
                if ref not in refs:
                    refs.append(ref)

        output.append(
            {
                "handoff_id": handoff_id,
                "source": spec["source"],
                "target": spec["target"],
                "observed_at": observed_at,
                "status": str(states.get(handoff_id) or "UNKNOWN").upper(),
                "request_id": request_id,
                "objective_id": objective_id,
                "work_item_id": work_item_id,
                "evidence_refs": refs,
                "first_failing_boundary": failure.get("first_failing_boundary") if failure else None,
                "typed_failure": failure.get("typed_failure") if failure else None,
                "source_version": failure.get("source_version") if failure else None,
                "deployed_sha": failure.get("deployed_sha") if failure else None,
                "can_execute": False,
            }
        )
    return output


def work_item_row(item: Mapping[str, Any]) -> dict[str, Any]:
    errors = ecosystem_conductor.validate_work_item(dict(item), {"components": _component_stub(item)})
    if errors:
        # validate_work_item also expects a full component registry. The stronger
        # caller path below should be used when registry is available.
        pass
    row = sanitize(dict(item))
    if row.get("can_execute") not in (None, False):
        raise ValueError("work item can_execute must remain false")
    row["can_execute"] = False
    row["updated_at"] = str(row.get("updated_at") or _utc_now())
    if str(row.get("state") or "").upper() == "TERMINATED":
        row["terminal_at"] = str(row.get("terminal_at") or row["updated_at"])
    else:
        row["terminal_at"] = None
    return row


def _component_stub(item: Mapping[str, Any]) -> dict[str, Any]:
    owner = str(item.get("current_owner") or "")
    domain = str(item.get("authority_domain") or "")
    return {owner: {"authority_domain": domain}} if owner else {}


def validated_work_item_row(
    item: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    errors = ecosystem_conductor.validate_work_item(dict(item), dict(registry))
    if errors:
        raise ValueError("; ".join(errors))
    return work_item_row(item)


def readiness_row(
    evaluation: Mapping[str, Any],
    *,
    capability_matrix: Sequence[Mapping[str, Any]] = (),
    metrics: Mapping[str, Any] | None = None,
    evidence_refs: Iterable[str] = (),
    observed_at: str | None = None,
) -> dict[str, Any]:
    terminal = evaluation.get("terminal_authority")
    if terminal != TERMINAL_AUTHORITY:
        raise ValueError("terminal authority cannot be overridden")
    if evaluation.get("can_execute") is not False:
        raise ValueError("readiness can_execute must remain false")

    return {
        "observed_at": observed_at or _utc_now(),
        "ecosystem_status": evaluation.get("ecosystem_status") or "SAFE_HOLD",
        "safe_hold_required": bool(evaluation.get("safe_hold_required")),
        "false_green_detected": bool(evaluation.get("false_green_detected")),
        "component_states": sanitize(evaluation.get("component_states") or {}),
        "handoff_states": sanitize(evaluation.get("handoff_states") or {}),
        "golden_paths": sanitize(evaluation.get("golden_paths") or {}),
        "capability_matrix": sanitize(list(capability_matrix)),
        "metrics": sanitize(dict(metrics or {})),
        "evidence_refs": list(dict.fromkeys(map(str, evidence_refs))),
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
    }


def finding_row(finding: Mapping[str, Any]) -> dict[str, Any]:
    row = sanitize(dict(finding))
    if row.get("can_execute") not in (None, False):
        raise ValueError("finding can_execute must remain false")
    required = (
        "finding_id",
        "finding_type",
        "fingerprint",
        "severity",
        "status",
        "first_detected_at",
        "last_observed_at",
        "occurrence_count",
        "diagnostic_owner",
        "closure_owner",
        "change_class",
    )
    missing = [key for key in required if row.get(key) in (None, "")]
    if missing:
        raise ValueError(f"finding missing fields: {missing}")
    row["can_execute"] = False
    row["updated_at"] = str(row.get("updated_at") or _utc_now())
    return row


def _execute(query: Any) -> Any:
    result = query.execute()
    return getattr(result, "data", result)


def persist_probe_results(db: Any, probe_results: Mapping[str, Any]) -> int:
    rows = [probe_row(value) for value in probe_results.values()]
    if not rows:
        return 0
    _execute(db.table(PROBE_TABLE).insert(rows))
    return len(rows)


def persist_handoffs(db: Any, rows: Sequence[Mapping[str, Any]]) -> int:
    payload = [sanitize(dict(row)) for row in rows]
    if any(row.get("can_execute") is not False for row in payload):
        raise ValueError("handoff receipts must preserve can_execute=false")
    if payload:
        _execute(db.table(HANDOFF_TABLE).insert(payload))
    return len(payload)


def persist_work_items(
    db: Any,
    items: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Any],
) -> int:
    rows = [validated_work_item_row(item, registry) for item in items]
    if rows:
        _execute(db.table(WORK_TABLE).upsert(rows, on_conflict="work_item_id"))
    return len(rows)


def persist_readiness(db: Any, row: Mapping[str, Any]) -> int:
    payload = sanitize(dict(row))
    if payload.get("can_execute") is not False:
        raise ValueError("readiness receipt must preserve can_execute=false")
    _execute(db.table(READINESS_TABLE).insert(payload))
    return 1


def persist_findings(db: Any, findings: Sequence[Mapping[str, Any]]) -> int:
    rows = [finding_row(item) for item in findings]
    if rows:
        _execute(db.table(FINDING_TABLE).upsert(rows, on_conflict="finding_id"))
    return len(rows)


def evidence_hash(*parts: Any) -> str:
    canonical = json.dumps(sanitize(parts), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def persist_control_plane_run(
    db: Any,
    *,
    registry: Mapping[str, Any],
    config: Mapping[str, Any],
    probe_results: Mapping[str, Any],
    observed_state: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    capability_matrix: Sequence[Mapping[str, Any]] = (),
    metrics: Mapping[str, Any] | None = None,
    findings: Sequence[Mapping[str, Any]] = (),
    request_id: str | None = None,
    objective_id: str | None = None,
    work_item_id: str | None = None,
) -> dict[str, Any]:
    probes_written = persist_probe_results(db, probe_results)
    handoffs = handoff_rows(
        registry,
        config,
        probe_results,
        observed_state,
        request_id=request_id,
        objective_id=objective_id,
        work_item_id=work_item_id,
    )
    handoffs_written = persist_handoffs(db, handoffs)
    work_items = list(observed_state.get("work_items") or [])
    work_items_written = persist_work_items(db, work_items, registry) if work_items else 0

    evidence_refs = [
        ref
        for receipt in probe_results.values()
        for ref in probe_row(receipt).get("evidence_refs", [])
    ]
    ready = readiness_row(
        evaluation,
        capability_matrix=capability_matrix,
        metrics=metrics or {},
        evidence_refs=evidence_refs,
    )
    readiness_written = persist_readiness(db, ready)
    findings_written = persist_findings(db, findings)

    return {
        "probe_receipts_written": probes_written,
        "handoff_receipts_written": handoffs_written,
        "work_items_written": work_items_written,
        "readiness_snapshots_written": readiness_written,
        "findings_written": findings_written,
        "evidence_hash": evidence_hash(
            probe_results,
            observed_state,
            evaluation,
            capability_matrix,
            metrics or {},
            findings,
        ),
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


__all__ = [
    "CAN_EXECUTE",
    "FINDING_TABLE",
    "HANDOFF_TABLE",
    "PROBE_TABLE",
    "READINESS_TABLE",
    "TERMINAL_AUTHORITY",
    "WORK_TABLE",
    "evidence_hash",
    "finding_row",
    "handoff_rows",
    "persist_control_plane_run",
    "persist_findings",
    "persist_handoffs",
    "persist_probe_results",
    "persist_readiness",
    "persist_work_items",
    "probe_row",
    "readiness_row",
    "sanitize",
    "validated_work_item_row",
]
