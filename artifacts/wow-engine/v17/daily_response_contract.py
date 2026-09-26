"""Compact V17 Daily response projection and paged full-evidence retrieval.

A Daily run scores many rows, and each scored row carries the full governed
package: prediction, evidence ledger, acquisition packet, model artifact
metadata, numerical-engine output, objective lanes, and backend traversal.
Embedding every one of those in a single Daily response makes a normal client
flow fail with ``ResponseTooLargeError`` well before the requested row count is
reached, so the bounded run cannot complete at all.

The repair separates transport from evidence. Daily returns only compact
run/row terminal fields. The complete per-row evidence is persisted once during
the run and then read back through a paged retrieval route, so no evidence is
discarded — only relocated off the single monolithic response.

Compact mode also projects run-level reconciliation structures. Acquisition
receipts, cross-sport routed rows, and identity/audit lists can scale with the
discovered slate even when only a small number of rows is scored; those
collections are summarized/bounded in the Action response while their scalar
counts and reconciliation results remain visible. FULL mode is unchanged.

For team/event rows, compact transport also preserves the small numeric sporting-
probability package needed to distinguish a valid modeled hold from an unscored
blocker. Official publication and leaderboard eligibility remain separate fields.

This module never scores, never mutates a terminal, and never authorizes
execution.
"""
from __future__ import annotations

import json
from math import isfinite
from datetime import datetime, timezone
from typing import Any

CAN_EXECUTE = False

DAILY_ROW_DETAIL_TABLE = "wow_v17_daily_run_row_detail"
DAILY_ACQUISITION_DETAIL_TABLE = "wow_v17_daily_run_acquisition_detail"

DETAIL_PERSISTENCE_UNAVAILABLE = "DAILY_ROW_DETAIL_PERSISTENCE_UNAVAILABLE"
DETAIL_RETRIEVAL_UNAVAILABLE = "DAILY_ROW_DETAIL_RETRIEVAL_UNAVAILABLE"
ACQUISITION_DETAIL_PERSISTENCE_UNAVAILABLE = "CROSS_SPORT_ACQUISITION_DETAIL_PERSISTENCE_UNAVAILABLE"
ACQUISITION_DETAIL_RETRIEVAL_UNAVAILABLE = "CROSS_SPORT_ACQUISITION_DETAIL_RETRIEVAL_UNAVAILABLE"

# Compact-mode budgets. Complete row evidence remains in persisted detail and
# FULL mode remains available for internal audit; trimming applies to the normal
# Action transport only. Preserve ordinary/small reconciliation diagnostics in
# full and only truncate lists that have grown to slate scale.
MAX_COMPACT_BLOCKERS = 4
MAX_COMPACT_BLOCKER_CHARS = 100
MAX_COMPACT_IDENTITY_ITEMS = 50
MAX_COMPACT_AUDIT_ITEMS = 4

DETAIL_PAGE_DEFAULT_LIMIT = 5
DETAIL_PAGE_MAX_LIMIT = 25
ACQUISITION_DETAIL_PAGE_DEFAULT_LIMIT = 25
ACQUISITION_DETAIL_PAGE_MAX_LIMIT = 100

_COMPACT_DIRECTION_FIELDS = (
    "terminal_label",
    "verdict_class",
    "pick_rejected",
    "model_evaluated",
    "model_qualified",
    "confidence_tier",
    "value_qualification_status",
)

_COMPACT_PREDICTION_FIELDS = (
    "prediction_id",
    "calibrated_probability",
    "calibrated_probability_lower_bound",
    "calibrated_probability_upper_bound",
    "calibration_status",
)

_COMPACT_MONEYLINE_FIELDS = (
    "terminal_label",
    "code",
    "sporting_probability_completed",
    "sporting_probability_status",
    "probability_fields_withheld",
    "model_probability_available",
    "probability_visibility_status",
    "official_leaderboard_eligible",
    "raw_model_probability",
    "raw_home_probability",
    "raw_away_probability",
    "independent_probability",
    "independent_home_probability",
    "independent_away_probability",
    "unconditional_probability",
    "calibrated_probability",
    "calibrated_lower_bound",
    "calibrated_upper_bound",
    "selected_calibrated_probability",
    "selected_calibrated_lower_bound",
    "selected_calibrated_upper_bound",
    "calibrated_home_probability",
    "calibrated_home_lower_bound",
    "calibrated_home_upper_bound",
    "calibrated_away_probability",
    "calibrated_away_lower_bound",
    "calibrated_away_upper_bound",
    "model_timestamp",
    "model_version",
    "llp_probability_audit_result",
    "event_mutex_status",
)

_MONEYLINE_PROBABILITY_FIELDS = (
    "raw_model_probability",
    "raw_home_probability",
    "raw_away_probability",
    "independent_probability",
    "independent_home_probability",
    "independent_away_probability",
    "unconditional_probability",
    "calibrated_probability",
    "calibrated_lower_bound",
    "selected_calibrated_probability",
    "selected_calibrated_lower_bound",
    "calibrated_home_probability",
    "calibrated_home_lower_bound",
    "calibrated_away_probability",
    "calibrated_away_lower_bound",
)

_COMPACT_ACQUISITION_FIELDS = (
    "status",
    "attempted",
    "hydrated",
    "persisted",
    "held",
    "snapshot_write_succeeded",
    "snapshot_write_failed",
    "explicit_prewrite_exclusions",
    "candidate_source",
    "line_source",
)

_FATAL_MONEYLINE_MODEL_STATUSES = {
    "MODEL_UNAVAILABLE",
    "MODEL_SCORER_FAILED",
    "MODEL_OUTPUT_INVALID",
    "MODEL_INPUTS_INSUFFICIENT",
    "MODEL_ROUTE_UNSUPPORTED",
    "RUN_INVALID_ACQUISITION_INCOMPLETE",
}


def _compact_blocker(value: Any) -> str:
    text = str(value)
    if len(text) <= MAX_COMPACT_BLOCKER_CHARS:
        return text
    return text[:MAX_COMPACT_BLOCKER_CHARS]


def _compact_blockers(values: Any) -> tuple[list[str], int]:
    if not isinstance(values, (list, tuple)):
        return [], 0
    kept = [_compact_blocker(value) for value in values[:MAX_COMPACT_BLOCKERS]]
    return kept, max(len(values) - MAX_COMPACT_BLOCKERS, 0)


def _compact_list(values: Any, *, limit: int = MAX_COMPACT_AUDIT_ITEMS) -> tuple[list[Any], int, int]:
    if not isinstance(values, (list, tuple)):
        return [], 0, 0
    kept = list(values[:limit])
    total = len(values)
    return kept, total, max(total - len(kept), 0)


def _qualification(payload: dict[str, Any]) -> dict[str, Any]:
    qualification = payload.get("probability_qualification")
    return qualification if isinstance(qualification, dict) else {}


def _pick(source: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: source[field] for field in fields if field in source}


def _finite_probability(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return False
    return isfinite(parsed) and 0.0 <= parsed <= 1.0


def _moneyline_model_probability_available(result: dict[str, Any]) -> bool:
    if result.get("model_probability_available") is True:
        return True
    status = str(result.get("code") or result.get("terminal_status") or "").upper()
    if status in _FATAL_MONEYLINE_MODEL_STATUSES:
        return False
    if result.get("probability_fields_withheld") is True:
        return False
    return any(_finite_probability(result.get(field)) for field in _MONEYLINE_PROBABILITY_FIELDS)


def compact_acquisition(acquisition: dict[str, Any]) -> dict[str, Any]:
    """Summarize an acquisition packet without inlining per-candidate receipts."""
    compact = _pick(acquisition, _COMPACT_ACQUISITION_FIELDS)
    receipts = acquisition.get("receipts")
    compact["receipts_count"] = len(receipts) if isinstance(receipts, list) else 0
    compact["receipts_inlined"] = False
    blockers, truncated = _compact_blockers(acquisition.get("blockers"))
    compact["blockers"] = blockers
    if truncated:
        compact["blockers_truncated"] = truncated
    compact["can_execute"] = False
    return compact


def compact_handoff_reconciliation(handoff: dict[str, Any]) -> dict[str, Any]:
    """Preserve reconciliation math while bounding only slate-sized ID arrays."""
    compact = dict(handoff)
    values = handoff.get("missing_persisted_snapshot_ids")
    if isinstance(values, list):
        compact["missing_persisted_snapshot_ids"] = values[:MAX_COMPACT_IDENTITY_ITEMS]
        compact["missing_persisted_snapshot_ids_count"] = len(values)
        truncated = max(len(values) - MAX_COMPACT_IDENTITY_ITEMS, 0)
        if truncated:
            compact["missing_persisted_snapshot_ids_truncated"] = truncated
    compact["can_execute"] = False
    return compact


def compact_lane_reconciliation(lanes: dict[str, Any]) -> dict[str, Any]:
    """Project lane reconciliation without changing counts or terminal meaning."""
    compacted: dict[str, Any] = {}
    for lane_name, payload in lanes.items():
        if not isinstance(payload, dict):
            compacted[lane_name] = payload
            continue
        lane = dict(payload)
        if lane_name == "PROPS":
            acquisition = payload.get("acquisition")
            if isinstance(acquisition, dict):
                lane["acquisition"] = compact_acquisition(acquisition)
            handoff = payload.get("handoff_reconciliation")
            if isinstance(handoff, dict):
                lane["handoff_reconciliation"] = compact_handoff_reconciliation(handoff)
        compacted[lane_name] = lane
    return compacted


def _compact_discovery_inventory(discovery: dict[str, Any]) -> dict[str, Any]:
    """Bound discovery diagnostics while preserving slate/accounting metadata."""
    compact = dict(discovery)
    for field in ("sports_queried", "sports_with_events", "source_blockers", "acquisition_audit"):
        values = discovery.get(field)
        if not isinstance(values, (list, tuple)):
            continue
        kept, total, truncated = _compact_list(values)
        compact[field] = kept
        compact[f"{field}_count"] = total
        if truncated:
            compact[f"{field}_truncated"] = truncated
    compact["can_execute"] = False
    return compact


def _compact_cross_sport_reconciliation(reconciliation: dict[str, Any]) -> dict[str, Any]:
    compact = dict(reconciliation)
    for field in ("acquisition_audit", "families_without_configured_feed"):
        values = reconciliation.get(field)
        if not isinstance(values, (list, tuple)):
            continue
        kept, total, truncated = _compact_list(values)
        compact[field] = kept
        compact[f"{field}_count"] = total
        if truncated:
            compact[f"{field}_truncated"] = truncated
    source_blockers = reconciliation.get("source_blockers")
    if isinstance(source_blockers, (list, tuple)):
        kept, total, truncated = _compact_list(source_blockers)
        compact["source_blockers"] = kept
        compact["source_blockers_count"] = total
        if truncated:
            compact["source_blockers_truncated"] = truncated
    compact["can_execute"] = False
    return compact


def compact_cross_sport_discovery_audit(audit: dict[str, Any]) -> dict[str, Any]:
    """Remove slate-sized routed rows from the normal Daily Action response."""
    compact: dict[str, Any] = {"can_execute": False}
    discovery = audit.get("discovery")
    if isinstance(discovery, dict):
        compact["discovery"] = _compact_discovery_inventory(discovery)

    rows = audit.get("rows")
    compact["rows_count"] = len(rows) if isinstance(rows, list) else 0
    compact["rows_inlined"] = False

    reconciliation = audit.get("reconciliation")
    if isinstance(reconciliation, dict):
        compact["reconciliation"] = _compact_cross_sport_reconciliation(reconciliation)

    counters = audit.get("market_evidence_counters")
    if isinstance(counters, dict):
        compact["market_evidence_counters"] = dict(counters)
    persistence = audit.get("acquisition_detail_persistence")
    if isinstance(persistence, dict):
        compact["acquisition_detail_persistence"] = dict(persistence)
    reference = audit.get("acquisition_detail_ref")
    if isinstance(reference, dict):
        compact["acquisition_detail_ref"] = dict(reference)
    return compact


def compact_direction(outcome: dict[str, Any]) -> dict[str, Any]:
    """Project one scored direction down to its terminal/probability summary."""
    payload = outcome.get("payload") if isinstance(outcome.get("payload"), dict) else {}
    qualification = _qualification(payload)
    prediction = payload.get("prediction") if isinstance(payload.get("prediction"), dict) else {}

    compact: dict[str, Any] = {
        "direction": outcome.get("direction"),
        "stage_status": outcome.get("status"),
    }
    compact.update(_pick(payload, _COMPACT_DIRECTION_FIELDS))
    for field in _COMPACT_DIRECTION_FIELDS:
        if field not in compact and field in qualification:
            compact[field] = qualification[field]
    compact.update(_pick(prediction, _COMPACT_PREDICTION_FIELDS))

    if "code" in payload:
        compact["code"] = payload["code"]
    if "error_type" in payload:
        compact["error_type"] = payload["error_type"]

    rank_eligible = (
        payload.get("rank_eligible") is True
        or payload.get("probability_rank_eligible") is True
        or qualification.get("rank_eligible") is True
        or qualification.get("probability_rank_eligible") is True
    )
    compact["probability_publishable"] = payload.get("probability_publishable") is True
    compact["probability_rank_eligible"] = bool(rank_eligible)

    blockers = payload.get("blockers")
    if blockers is None:
        blockers = qualification.get("blockers")
    kept, truncated = _compact_blockers(blockers)
    if kept:
        compact["blockers"] = kept
    if truncated:
        compact["blockers_truncated"] = truncated

    compact["can_execute"] = False
    return compact


def compact_moneyline_result(result: dict[str, Any]) -> dict[str, Any]:
    """Project a team/event result without hiding valid held probabilities."""
    compact = _pick(result, _COMPACT_MONEYLINE_FIELDS)
    guard = result.get("official_publication_guard")
    if isinstance(guard, dict):
        kept, truncated = _compact_blockers(guard.get("blockers"))
        compact["official_publication_guard"] = {
            "status": guard.get("status"),
            "official_publication_allowed": guard.get("official_publication_allowed"),
            **({"blockers": kept} if kept else {}),
            **({"blockers_truncated": truncated} if truncated else {}),
        }
    prepublication = result.get("prepublication_claim")
    if isinstance(prepublication, dict):
        compact["prepublication_claim"] = prepublication

    publishable = result.get("probability_publishable") is True
    rank_eligible = result.get("rank_eligible") is True
    model_available = _moneyline_model_probability_available(result)
    official = bool(publishable and rank_eligible)

    compact["probability_publishable"] = publishable
    compact["rank_eligible"] = rank_eligible
    compact["model_probability_available"] = model_available
    compact["official_leaderboard_eligible"] = official
    compact["probability_visibility_status"] = str(
        result.get("probability_visibility_status")
        or ("OFFICIAL_QUALIFIED" if official else "MODELED_HELD" if model_available else "BLOCKED_UNSCORED")
    )
    compact["can_execute"] = False
    return compact


def compact_row(row: dict[str, Any], *, run_id: str, row_index: int, detail_available: bool) -> dict[str, Any]:
    """Project one terminal Daily row into the compact transport contract."""
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    compact: dict[str, Any] = {
        "lane": row.get("lane"),
        "identity": row.get("identity"),
        "terminal": True,
        "row_status": row.get("row_status"),
        "probability_publishable": row.get("probability_publishable") is True,
        "can_execute": False,
    }

    reduction = row.get("terminal_reduction")
    if isinstance(reduction, dict):
        compact["terminal_reduction"] = reduction

    if row.get("lane") == "PROPS":
        outcomes = result.get("outcomes")
        compact["directions"] = [
            compact_direction(outcome) for outcome in outcomes if isinstance(outcome, dict)
        ] if isinstance(outcomes, list) else []
    else:
        summary = compact_moneyline_result(result)
        compact["result_summary"] = summary
        compact["model_probability_available"] = summary["model_probability_available"]
        compact["probability_visibility_status"] = summary["probability_visibility_status"]
        compact["official_leaderboard_eligible"] = summary["official_leaderboard_eligible"]

    compact["detail_ref"] = {
        "run_id": run_id,
        "row_index": row_index,
        "detail_available": bool(detail_available),
        "retrieval_operation_id": "readWowV17DailySnapshotRowDetail",
    }
    return compact


def compact_response(response: dict[str, Any], *, detail_available: bool) -> dict[str, Any]:
    """Return the Daily response with compact row and run-level packages."""
    run_id = str(response.get("run_id") or "")
    rows = response.get("rows") if isinstance(response.get("rows"), list) else []
    compacted = dict(response)
    compacted["rows"] = [
        compact_row(row, run_id=run_id, row_index=index, detail_available=detail_available)
        for index, row in enumerate(rows)
    ]

    lanes = response.get("lane_reconciliation")
    if isinstance(lanes, dict):
        compacted["lane_reconciliation"] = compact_lane_reconciliation(lanes)

    acquisition = response.get("prop_acquisition")
    if isinstance(acquisition, dict):
        compacted["prop_acquisition"] = compact_acquisition(acquisition)

    cross_sport = response.get("cross_sport_discovery_audit")
    if isinstance(cross_sport, dict):
        compacted["cross_sport_discovery_audit"] = compact_cross_sport_discovery_audit(cross_sport)

    if "blockers" in compacted:
        blockers, truncated = _compact_blockers(compacted.get("blockers"))
        compacted["blockers"] = blockers
        if truncated:
            compacted["blockers_truncated"] = truncated

    compacted["response_mode"] = "COMPACT"
    compacted["row_detail_retrieval"] = {
        "mode": "PAGED",
        "detail_available": bool(detail_available),
        "operation_id": "readWowV17DailySnapshotRowDetail",
        "path": "/v17/daily-snapshot-run/{run_id}/rows",
        "default_limit": DETAIL_PAGE_DEFAULT_LIMIT,
        "max_limit": DETAIL_PAGE_MAX_LIMIT,
        "rows_available": len(rows),
        "can_execute": False,
    }
    compacted["can_execute"] = False
    return compacted


def persist_row_detail(db: Any, *, run_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Persist full per-row evidence once so compact transport loses nothing.

    Fails closed and explicitly: a persistence failure is reported as a typed
    blocker with ``detail_available=false`` rather than being swallowed.
    """
    if not rows:
        return {"status": "NO_ROWS", "detail_available": False, "rows_persisted": 0, "blockers": [], "can_execute": False}

    captured_at = datetime.now(timezone.utc).isoformat()
    payload = [
        {
            "run_id": run_id,
            "row_index": index,
            "lane": row.get("lane"),
            "row_status": row.get("row_status"),
            "identity": row.get("identity") or {},
            "detail": row,
            "captured_at": captured_at,
            "can_execute": False,
        }
        for index, row in enumerate(rows)
    ]
    try:
        db.table(DAILY_ROW_DETAIL_TABLE).upsert(payload).execute()
    except Exception as exc:
        return {
            "status": "PERSISTENCE_UNAVAILABLE",
            "detail_available": False,
            "rows_persisted": 0,
            "blockers": [f"{DETAIL_PERSISTENCE_UNAVAILABLE}:{type(exc).__name__}"],
            "can_execute": False,
        }
    return {
        "status": "PERSISTED",
        "detail_available": True,
        "rows_persisted": len(payload),
        "blockers": [],
        "can_execute": False,
    }


def read_row_detail_page(db: Any, *, run_id: str, offset: int = 0, limit: int = DETAIL_PAGE_DEFAULT_LIMIT) -> dict[str, Any]:
    """Read one bounded page of persisted full row evidence for a Daily run."""
    bounded_limit = max(1, min(int(limit), DETAIL_PAGE_MAX_LIMIT))
    bounded_offset = max(0, int(offset))
    try:
        query = (
            db.table(DAILY_ROW_DETAIL_TABLE)
            .select("run_id,row_index,lane,row_status,identity,detail,captured_at")
            .eq("run_id", run_id)
            .order("row_index")
        )
        ranged = getattr(query, "range", None)
        if callable(ranged):
            rows = ranged(bounded_offset, bounded_offset + bounded_limit - 1).execute().data or []
        else:
            rows = (query.limit(bounded_offset + bounded_limit).execute().data or [])[bounded_offset:bounded_offset + bounded_limit]
    except Exception as exc:
        return {
            "run_id": run_id,
            "terminal": True,
            "status": "DETAIL_UNAVAILABLE",
            "rows": [],
            "offset": bounded_offset,
            "limit": bounded_limit,
            "returned": 0,
            "blockers": [f"{DETAIL_RETRIEVAL_UNAVAILABLE}:{type(exc).__name__}"],
            "can_execute": False,
        }

    page = [dict(row) for row in rows]
    return {
        "run_id": run_id,
        "terminal": True,
        "status": "OK" if page else "NO_ROWS_FOR_PAGE",
        "rows": page,
        "offset": bounded_offset,
        "limit": bounded_limit,
        "returned": len(page),
        "next_offset": bounded_offset + len(page) if len(page) == bounded_limit else None,
        "blockers": [],
        "can_execute": False,
    }


def _bounded_text(value: Any, *, maximum: int = 128) -> str | None:
    """Bound persisted acquisition metadata without accepting arbitrary payloads."""
    if value is None:
        return None
    text = str(value).strip()
    return text[:maximum] if text else None


def _bounded_code(value: Any) -> str | None:
    """Persist a typed code, never an exception/provider message."""
    text = _bounded_text(value, maximum=128)
    if not text:
        return None
    token = text.split(":", 1)[0].strip().upper()
    sanitized = "".join(char if char.isalnum() or char in {"_", "-", "."} else "_" for char in token)
    return sanitized[:96] or None


def acquisition_detail_reference(
    *, run_id: str, detail_available: bool, details_count: int
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "detail_available": bool(detail_available),
        "details_count": max(0, int(details_count)),
        "details_inlined": False,
        "retrieval_operation_id": "readWowV17DailySnapshotAcquisitionDetail",
        "path": "/v17/daily-snapshot-run/{run_id}/acquisition-details",
        "default_limit": ACQUISITION_DETAIL_PAGE_DEFAULT_LIMIT,
        "max_limit": ACQUISITION_DETAIL_PAGE_MAX_LIMIT,
        "can_execute": False,
    }


def persist_acquisition_detail(
    db: Any,
    *,
    run_id: str,
    details: list[dict[str, Any]],
    expected_targets: list[dict[str, Any]],
) -> dict[str, Any]:
    """Persist sanitized target outcomes independently of event-row detail.

    Only this explicit allowlist crosses the persistence boundary. Provider raw
    payloads, prices, probabilities, participant identity, and error messages
    are never copied into the acquisition-detail store.
    """
    expected_keys = {
        (str(row.get("family") or "").strip().upper(), str(row.get("target_key") or "").strip())
        for row in expected_targets
        if isinstance(row, dict)
    }
    if not expected_targets or any(not all(identity) for identity in expected_keys):
        return {
            "status": "EXPECTED_TARGETS_INVALID",
            "detail_available": False,
            "details_expected": len(expected_targets),
            "details_persisted": 0,
            "board_completeness": False,
            "blockers": ["CROSS_SPORT_ACQUISITION_EXPECTED_TARGETS_INVALID"],
            "can_execute": False,
        }
    if len(expected_keys) != len(expected_targets):
        return {
            "status": "EXPECTED_TARGETS_INVALID",
            "detail_available": False,
            "details_expected": len(expected_targets),
            "details_persisted": 0,
            "board_completeness": False,
            "blockers": ["CROSS_SPORT_ACQUISITION_EXPECTED_TARGETS_DUPLICATE"],
            "can_execute": False,
        }
    if not details:
        return {
            "status": "NO_DETAILS",
            "detail_available": False,
            "details_expected": 0,
            "details_persisted": 0,
            "board_completeness": False,
            "blockers": ["CROSS_SPORT_ACQUISITION_DETAIL_MISSING"],
            "can_execute": False,
        }

    captured_at = datetime.now(timezone.utc).isoformat()
    payload: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    for detail in details:
        family = _bounded_text(detail.get("family"), maximum=32)
        target_key = _bounded_text(detail.get("target_key"), maximum=256)
        if not family or not target_key:
            return {
                "status": "INVALID_DETAIL",
                "detail_available": False,
                "details_expected": len(details),
                "details_persisted": 0,
                "board_completeness": False,
                "blockers": ["CROSS_SPORT_ACQUISITION_DETAIL_IDENTITY_INVALID"],
                "can_execute": False,
            }
        identity = (family.upper(), target_key)
        if identity in seen_keys:
            return {
                "status": "INVALID_DETAIL",
                "detail_available": False,
                "details_expected": len(details),
                "details_persisted": 0,
                "board_completeness": False,
                "blockers": ["CROSS_SPORT_ACQUISITION_DETAIL_DUPLICATE_IDENTITY"],
                "can_execute": False,
            }
        seen_keys.add(identity)
        provider_sport_id = detail.get("provider_sport_id")
        final_state = _bounded_text(detail.get("final_state"), maximum=96)
        provider_status = _bounded_text(detail.get("provider_status"), maximum=96)
        fallback_status = _bounded_text(detail.get("fallback_status"), maximum=96)
        exhaustion_status = _bounded_text(detail.get("exhaustion_status"), maximum=96)
        if not all((final_state, provider_status, fallback_status, exhaustion_status)):
            return {
                "status": "INVALID_DETAIL",
                "detail_available": False,
                "details_expected": len(details),
                "details_persisted": 0,
                "board_completeness": False,
                "blockers": ["CROSS_SPORT_ACQUISITION_DETAIL_STATE_INVALID"],
                "can_execute": False,
            }
        payload.append(
            {
                "run_id": run_id,
                "family": family.upper(),
                "target_key": target_key,
                "provider": _bounded_text(detail.get("provider"), maximum=64),
                "league": _bounded_text(detail.get("league"), maximum=64),
                "regime": _bounded_text(detail.get("regime"), maximum=64),
                "provider_sport_id": int(provider_sport_id) if isinstance(provider_sport_id, int) else None,
                "sport_key": _bounded_text(detail.get("sport_key"), maximum=128),
                "final_state": final_state,
                "provider_status": provider_status,
                "fallback_status": fallback_status,
                "exhaustion_status": exhaustion_status,
                "events_returned": max(0, int(detail.get("events_returned") or 0)),
                "duplicate_rows_suppressed": max(
                    0, int(detail.get("duplicate_rows_suppressed") or 0)
                ),
                "blocker_code": _bounded_code(detail.get("blocker_code")),
                "captured_at": captured_at,
                "can_execute": False,
            }
        )
    if seen_keys != expected_keys:
        return {
            "status": "TARGET_SET_MISMATCH",
            "detail_available": False,
            "details_expected": len(expected_keys),
            "details_persisted": 0,
            "board_completeness": False,
            "blockers": ["CROSS_SPORT_ACQUISITION_TARGET_SET_MISMATCH"],
            "missing_target_count": len(expected_keys - seen_keys),
            "extra_target_count": len(seen_keys - expected_keys),
            "can_execute": False,
        }
    try:
        db.table(DAILY_ACQUISITION_DETAIL_TABLE).upsert(
            payload, on_conflict="run_id,family,target_key"
        ).execute()
        query = (
            db.table(DAILY_ACQUISITION_DETAIL_TABLE)
            .select("family,target_key")
            .eq("run_id", run_id)
            .order("family")
            .order("target_key")
        )
        ranged = getattr(query, "range", None)
        stored_rows = (
            ranged(0, len(expected_keys)).execute().data
            if callable(ranged)
            else query.limit(len(expected_keys) + 1).execute().data
        ) or []
    except Exception as exc:
        return {
            "status": "PERSISTENCE_UNAVAILABLE",
            "detail_available": False,
            "details_expected": len(payload),
            "details_persisted": 0,
            "board_completeness": False,
            "blockers": [f"{ACQUISITION_DETAIL_PERSISTENCE_UNAVAILABLE}:{type(exc).__name__}"],
            "can_execute": False,
        }
    stored_keys = {
        (str(row.get("family") or "").strip().upper(), str(row.get("target_key") or "").strip())
        for row in stored_rows
        if isinstance(row, dict)
    }
    if stored_keys != expected_keys:
        return {
            "status": "PERSISTENCE_RECONCILIATION_FAILED",
            "detail_available": False,
            "details_expected": len(expected_keys),
            "details_persisted": len(stored_keys & expected_keys),
            "board_completeness": False,
            "blockers": ["CROSS_SPORT_ACQUISITION_PERSISTED_TARGET_SET_MISMATCH"],
            "missing_target_count": len(expected_keys - stored_keys),
            "extra_target_count": len(stored_keys - expected_keys),
            "can_execute": False,
        }
    return {
        "status": "PERSISTED",
        "detail_available": True,
        "details_expected": len(payload),
        "details_persisted": len(payload),
        "board_completeness": True,
        "blockers": [],
        "can_execute": False,
    }


def read_acquisition_detail_page(
    db: Any,
    *,
    run_id: str,
    offset: int = 0,
    limit: int = ACQUISITION_DETAIL_PAGE_DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Read a bounded page of sanitized per-target acquisition outcomes."""
    bounded_limit = max(1, min(int(limit), ACQUISITION_DETAIL_PAGE_MAX_LIMIT))
    bounded_offset = max(0, int(offset))
    fields = (
        "run_id,family,target_key,provider,league,regime,provider_sport_id,"
        "sport_key,final_state,provider_status,fallback_status,exhaustion_status,"
        "events_returned,duplicate_rows_suppressed,blocker_code,captured_at"
    )
    try:
        query = (
            db.table(DAILY_ACQUISITION_DETAIL_TABLE)
            .select(fields)
            .eq("run_id", run_id)
            .order("family")
            .order("target_key")
        )
        ranged = getattr(query, "range", None)
        if callable(ranged):
            rows = ranged(bounded_offset, bounded_offset + bounded_limit - 1).execute().data or []
        else:
            rows = (query.limit(bounded_offset + bounded_limit).execute().data or [
            ])[bounded_offset:bounded_offset + bounded_limit]
    except Exception as exc:
        return {
            "run_id": run_id,
            "terminal": True,
            "status": "ACQUISITION_DETAIL_UNAVAILABLE",
            "rows": [],
            "offset": bounded_offset,
            "limit": bounded_limit,
            "returned": 0,
            "blockers": [f"{ACQUISITION_DETAIL_RETRIEVAL_UNAVAILABLE}:{type(exc).__name__}"],
            "can_execute": False,
        }
    page = [dict(row) for row in rows]
    return {
        "run_id": run_id,
        "terminal": True,
        "status": "OK" if page else "NO_ROWS_FOR_PAGE",
        "rows": page,
        "offset": bounded_offset,
        "limit": bounded_limit,
        "returned": len(page),
        "next_offset": bounded_offset + len(page) if len(page) == bounded_limit else None,
        "blockers": [],
        "can_execute": False,
    }


def serialized_byte_size(payload: Any) -> int:
    """Transport size of a response, used by the response-size regression test."""
    return len(json.dumps(payload, default=str).encode("utf-8"))


__all__ = [
    "ACQUISITION_DETAIL_PAGE_DEFAULT_LIMIT",
    "ACQUISITION_DETAIL_PAGE_MAX_LIMIT",
    "ACQUISITION_DETAIL_PERSISTENCE_UNAVAILABLE",
    "ACQUISITION_DETAIL_RETRIEVAL_UNAVAILABLE",
    "CAN_EXECUTE",
    "DAILY_ACQUISITION_DETAIL_TABLE",
    "DAILY_ROW_DETAIL_TABLE",
    "DETAIL_PAGE_DEFAULT_LIMIT",
    "DETAIL_PAGE_MAX_LIMIT",
    "DETAIL_PERSISTENCE_UNAVAILABLE",
    "DETAIL_RETRIEVAL_UNAVAILABLE",
    "MAX_COMPACT_AUDIT_ITEMS",
    "MAX_COMPACT_BLOCKERS",
    "MAX_COMPACT_IDENTITY_ITEMS",
    "acquisition_detail_reference",
    "compact_acquisition",
    "compact_cross_sport_discovery_audit",
    "compact_direction",
    "compact_handoff_reconciliation",
    "compact_lane_reconciliation",
    "compact_moneyline_result",
    "compact_response",
    "compact_row",
    "persist_acquisition_detail",
    "persist_row_detail",
    "read_acquisition_detail_page",
    "read_row_detail_page",
    "serialized_byte_size",
]
