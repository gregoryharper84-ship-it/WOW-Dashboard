"""Read-only production readback for governed prop-evidence failures.

The readback answers one bounded question: for an exact ``/score-prop``
request identity, does ``wow_prop_evidence_snapshots`` contain the requested
row, a different row with the same identity, a row with a different identity,
or no row at all? It also audits the invocation receipt so an expected
``SELF_ACCEPTANCE`` probe is never confused with a live Action failure.

This module is deliberately read-only. It never calls the repair loop, writes
a snapshot, invokes a specialist, or returns game logs, box scores, player
names, or probabilities. ``can_execute`` is always false.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import logging
import math
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

LOGGER = logging.getLogger("wow.v17.prop_evidence.readback")

READBACK_OPERATION = "PROP_EVIDENCE_READBACK"
EVIDENCE_READY = "PROP_EVIDENCE_READY"
EVIDENCE_NOT_FOUND = "PROP_EVIDENCE_SNAPSHOT_NOT_FOUND"
EVIDENCE_IDENTITY_MISMATCH = "PROP_EVIDENCE_IDENTITY_MISMATCH"
EVIDENCE_INCOMPLETE = "RUN_INVALID_ACQUISITION_INCOMPLETE"
EVIDENCE_FAILURE_CODES = frozenset(
    {
        EVIDENCE_NOT_FOUND,
        EVIDENCE_IDENTITY_MISMATCH,
        EVIDENCE_INCOMPLETE,
        "PROP_EVIDENCE_INCOMPLETE",
        "PROP_EVIDENCE_STALE",
    }
)

SELF_ACCEPTANCE = "SELF_ACCEPTANCE"
LIVE_ACTION_CALLERS = frozenset({"CHATGPT_ACTION", "ACTION_API_KEY"})

_ROW_COLUMNS = (
    "source_snapshot_id,captured_at,event_id,sport,player,stat_type,line,"
    "hydration_status,blockers,game_log,box_score_log,role_status,role_timestamp"
)
_CANDIDATE_COLUMNS = (
    "source_snapshot_id,captured_at,event_id,sport,player,stat_type,line,"
    "hydration_status,blockers,game_log,box_score_log,role_status,role_timestamp"
)
_RECEIPT_COLUMNS = (
    "invocation_id,occurred_at,route,http_status,validation_code,"
    "caller_class,auth_scheme"
)
_IDENTITY_FIELDS = (
    "source_snapshot_id",
    "event_id",
    "sport",
    "player",
    "stat_type",
    "line",
)


class PropEvidenceReadbackRequest(BaseModel):
    """Exact identity, or a request-id-only receipt audit.

    The full ``/score-prop`` body may be posted; unrelated fields are ignored.
    Partial identities are rejected rather than silently diagnosing a
    different prop.
    """

    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    source_snapshot_id: Optional[UUID] = None
    event_id: Optional[str] = Field(default=None, min_length=1)
    sport: Optional[str] = Field(default=None, min_length=1)
    player: Optional[str] = Field(default=None, min_length=1)
    stat_type: Optional[str] = Field(default=None, min_length=1)
    line: Optional[float] = None
    request_id: Optional[str] = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _require_request_or_complete_identity(self) -> "PropEvidenceReadbackRequest":
        provided = [
            name for name in _IDENTITY_FIELDS if getattr(self, name) is not None
        ]
        if not self.request_id and not provided:
            raise ValueError("request_id or a complete snapshot identity is required")
        if provided and len(provided) != len(_IDENTITY_FIELDS):
            raise ValueError(
                "source_snapshot_id, event_id, sport, player, stat_type and line "
                "must be provided together"
            )
        return self


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _canonical_stat(sport: Any, value: Any) -> str:
    """Resolve stat identity through the governed writer's alias map.

    Imported lazily because the governed runtime imports this readback layer
    indirectly through api_prod_market. Runtime-registered aliases mutate the
    same map, so this stays aligned with what the snapshot writer persisted.
    """
    from pick_request_runtime_core import _canonical_stat as writer_canonical_stat

    return writer_canonical_stat(str(sport or "").strip().upper(), str(value or ""))


def _canonical_player(value: Any) -> str:
    return " ".join(str(value or "").split())


def normalize_identity(value: Any) -> dict[str, Any]:
    """Normalize identity exactly as the governed snapshot writer stores it."""
    raw_snapshot_id = _get(value, "source_snapshot_id")
    try:
        source_snapshot_id = str(UUID(str(raw_snapshot_id)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("source_snapshot_id must be a UUID") from exc

    event_id = str(_get(value, "event_id") or "").strip()
    sport = str(_get(value, "sport") or "").strip().upper()
    player = _canonical_player(_get(value, "player"))
    stat_type = _canonical_stat(sport, _get(value, "stat_type"))
    if not event_id or not sport or not player or not stat_type:
        raise ValueError("event_id, sport, player and stat_type are required")

    raw_line = _get(value, "line")
    if raw_line is None or isinstance(raw_line, bool):
        raise ValueError("line must be a finite number")
    try:
        line = float(raw_line)
    except (TypeError, ValueError) as exc:
        raise ValueError("line must be a finite number") from exc
    if not math.isfinite(line):
        raise ValueError("line must be a finite number")

    return {
        "source_snapshot_id": source_snapshot_id,
        "event_id": event_id,
        "sport": sport,
        "player": player,
        "stat_type": stat_type,
        "line": line,
    }


def identity_fingerprint(value: Any) -> str:
    """Return a stable non-PII join key for logs and readback requests."""
    identity = normalize_identity(value)
    payload = json.dumps(
        {
            "event_id": identity["event_id"],
            "sport": identity["sport"],
            "player": identity["player"],
            "stat_type": identity["stat_type"],
            "line": identity["line"],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    if not isinstance(data, list):
        return []
    return [dict(row) for row in data if isinstance(row, dict)]


def _count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _mismatch_fields(row: Mapping[str, Any], identity: Mapping[str, Any]) -> list[str]:
    mismatches: list[str] = []
    if str(row.get("event_id") or "").strip() != identity["event_id"]:
        mismatches.append("event_id")
    if str(row.get("sport") or "").strip().upper() != identity["sport"]:
        mismatches.append("sport")
    if _canonical_player(row.get("player")) != identity["player"]:
        mismatches.append("player")
    row_sport = str(row.get("sport") or identity["sport"] or "").strip().upper()
    if _canonical_stat(row_sport, row.get("stat_type")) != identity["stat_type"]:
        mismatches.append("stat_type")
    try:
        row_line: Optional[float] = float(row.get("line"))
    except (TypeError, ValueError):
        row_line = None
    if row_line is None or abs(row_line - float(identity["line"])) > 1e-9:
        mismatches.append("line")
    return mismatches


def _row_summary(row: Mapping[str, Any], identity: Mapping[str, Any]) -> dict[str, Any]:
    mismatches = _mismatch_fields(row, identity)
    raw_blockers = row.get("blockers")
    blockers = [str(item) for item in raw_blockers] if isinstance(raw_blockers, list) else []
    return {
        "present": True,
        "source_snapshot_id": str(row.get("source_snapshot_id") or ""),
        "captured_at": _iso(row.get("captured_at")),
        "hydration_status": row.get("hydration_status"),
        "blockers": blockers[:10],
        "game_log_count": _count(row.get("game_log")),
        "box_score_log_count": _count(row.get("box_score_log")),
        "role_timestamp_present": row.get("role_timestamp") is not None,
        "identity_matches": not mismatches,
        "mismatch_fields": mismatches,
    }


def _lookup_row(client: Any, identity: Mapping[str, Any]) -> dict[str, Any]:
    try:
        response = (
            client.table("wow_prop_evidence_snapshots")
            .select(_ROW_COLUMNS)
            .eq("source_snapshot_id", identity["source_snapshot_id"])
            .limit(1)
            .execute()
        )
    except Exception as exc:
        return {"status": "UNAVAILABLE", "present": None, "error_type": type(exc).__name__}
    rows = _rows(response)
    if not rows:
        return {"status": "OK", "present": False}
    return {"status": "OK", "present": True, "summary": _row_summary(rows[0], identity)}


def _lookup_candidates(client: Any, identity: Mapping[str, Any]) -> dict[str, Any]:
    """Find exact-identity rows regardless of snapshot id, excluding the request id."""
    try:
        response = (
            client.table("wow_prop_evidence_snapshots")
            .select(_CANDIDATE_COLUMNS)
            .eq("event_id", identity["event_id"])
            .eq("line", identity["line"])
            # The writer persists these canonical values. Filtering in the
            # database before the bounded limit prevents >25 distractor rows
            # from hiding an exact identity and creating a false absence.
            .eq("sport", identity["sport"])
            .eq("player", identity["player"])
            .eq("stat_type", identity["stat_type"])
            .order("captured_at", desc=True)
            .limit(25)
            .execute()
        )
    except Exception as exc:
        return {"status": "UNAVAILABLE", "error_type": type(exc).__name__, "other_snapshot_ids": []}

    rows = _rows(response)
    other_ids: list[str] = []
    for row in rows:
        if _mismatch_fields(row, identity):
            continue
        source_snapshot_id = str(row.get("source_snapshot_id") or "")
        if source_snapshot_id and source_snapshot_id != identity["source_snapshot_id"]:
            other_ids.append(source_snapshot_id)
    # Reaching the bound with no different id cannot prove that no other
    # snapshot exists. Treat it as partial rather than absent evidence.
    status = "PARTIAL" if len(rows) >= 25 and not other_ids else "OK"
    return {"status": status, "other_snapshot_ids": other_ids[:10]}


def _receipt_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "invocation_id": str(row.get("invocation_id") or ""),
        "occurred_at": _iso(row.get("occurred_at")),
        "route": row.get("route"),
        "http_status": row.get("http_status"),
        "validation_code": row.get("validation_code"),
        "caller_class": row.get("caller_class"),
        "auth_scheme": row.get("auth_scheme"),
    }


def _lookup_receipt(client: Any, request_id: Optional[str]) -> dict[str, Any]:
    if not request_id:
        return {
            "status": "NOT_PROVIDED",
            "matches": [],
            "latest": None,
            "attribution": "UNATTRIBUTED_NO_REQUEST_ID",
        }
    try:
        response = (
            client.table("wow_action_invocation_receipts")
            .select(_RECEIPT_COLUMNS)
            .eq("request_id", str(request_id))
            .order("occurred_at", desc=True)
            .limit(10)
            .execute()
        )
    except Exception as exc:
        return {
            "status": "UNAVAILABLE",
            "matches": [],
            "latest": None,
            "error_type": type(exc).__name__,
            "attribution": "UNATTRIBUTED_RECEIPT_UNAVAILABLE",
        }
    matches = [_receipt_summary(row) for row in _rows(response)]
    latest = matches[0] if matches else None
    return {
        "status": "OK",
        "matches": matches,
        "latest": latest,
        "attribution": _receipt_attribution(latest),
    }


def _receipt_attribution(latest: Optional[Mapping[str, Any]]) -> str:
    if latest is None:
        return "UNATTRIBUTED_NO_RECEIPT"
    if str(latest.get("route") or "") != "/score-prop":
        return "RECEIPT_ROUTE_MISMATCH"
    caller = str(latest.get("caller_class") or "").strip().upper()
    code = str(latest.get("validation_code") or "").strip()
    try:
        http_status = int(latest.get("http_status"))
    except (TypeError, ValueError):
        http_status = None
    if caller == SELF_ACCEPTANCE and http_status == 422 and code == EVIDENCE_NOT_FOUND:
        return "EXPECTED_SELF_ACCEPTANCE_PROBE"
    if caller in LIVE_ACTION_CALLERS and http_status == 422:
        if code in EVIDENCE_FAILURE_CODES:
            return "LIVE_ACTION_EVIDENCE_FAILURE"
        return "LIVE_ACTION_OTHER_422"
    if caller == SELF_ACCEPTANCE:
        return "SELF_ACCEPTANCE_PROBE_OTHER_TERMINAL"
    return "OTHER_CALLER"


def _rpc_lookup(
    client: Any, identity: Mapping[str, Any], request: Any
) -> dict[str, Any]:
    try:
        response = client.rpc(
            "wow_prop_evidence_snapshot",
            {
                "p_source_snapshot_id": identity["source_snapshot_id"],
                "p_event_id": identity["event_id"],
                "p_sport": identity["sport"],
                "p_player": identity["player"],
                # The primary RPC compares the request label as supplied; only
                # row/candidate matching and the fingerprint alias-resolve.
                "p_stat_type": _get(request, "stat_type"),
                "p_line": identity["line"],
            },
        ).execute()
    except Exception as exc:
        return {"status": "INFRA_UNAVAILABLE", "error_type": type(exc).__name__}

    payload = getattr(response, "data", None)
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    if not isinstance(payload, dict):
        return {"status": "INVALID_RESPONSE"}

    raw_blockers = payload.get("blockers")
    blockers = [str(item) for item in raw_blockers] if isinstance(raw_blockers, list) else []
    return {
        "status": "OK",
        "ok": payload.get("ok") is True,
        "code": str(payload.get("code") or ""),
        "hydration_status": payload.get("hydration_status"),
        "game_log_count": payload.get("game_log_count"),
        "box_score_log_count": payload.get("box_score_log_count"),
        "blockers": blockers[:10],
    }


def classify_evidence_state(
    *,
    rpc: Mapping[str, Any],
    row: Mapping[str, Any],
    candidate_status: str,
    other_snapshot_ids: list[str],
) -> tuple[str, str]:
    """Classify the readback without converting infrastructure into absence."""
    if rpc.get("status") != "OK":
        if rpc.get("status") == "INVALID_RESPONSE":
            return (
                "PRIMARY_LOOKUP_INVALID_RESPONSE",
                "The primary RPC returned a non-object payload. Absence is unproven; "
                "treat this as an invalid readback, not as missing evidence.",
            )
        return (
            "PRIMARY_LOOKUP_INFRA_UNAVAILABLE",
            "The primary RPC could not be executed. Absence is unproven; treat this as "
            "an infrastructure failure, never as model or evidence absence.",
        )

    code = str(rpc.get("code") or "")
    if code == EVIDENCE_READY:
        return (
            "EVIDENCE_READY",
            "The primary RPC returned PROP_EVIDENCE_READY. The evidence exists and is "
            "acquisition-complete; a 422 here would be downstream of evidence.",
        )
    if code == EVIDENCE_INCOMPLETE:
        return (
            "EVIDENCE_NOT_READY",
            "The requested row exists but fails the governed acquisition contract. "
            "The 422 is incomplete evidence, not a missing row.",
        )
    if code == EVIDENCE_IDENTITY_MISMATCH:
        if row.get("present") is True:
            summary = row.get("summary")
            if isinstance(summary, Mapping) and summary.get("identity_matches") is True:
                return (
                    "PRIMARY_LOOKUP_STAT_ALIAS_MISMATCH",
                    "The snapshot id resolves to the requested row under the writer's "
                    "canonical stat alias, but the primary RPC compared the raw request "
                    "label and returned an identity mismatch. This is a stat-label "
                    "normalization boundary, not a different identity.",
                )
            return (
                "SNAPSHOT_ID_POINTS_TO_OTHER_IDENTITY",
                "The snapshot id resolves to a row with a different identity. The 422 "
                "is an identity mismatch, not absent evidence.",
            )
        if row.get("present") is None:
            return (
                "SNAPSHOT_IDENTITY_MISMATCH_ROW_UNREADABLE",
                "The RPC reported an identity mismatch but the row could not be read. "
                "Absence is unproven.",
            )
        return (
            "SNAPSHOT_IDENTITY_MISMATCH_ROW_MISSING",
            "The RPC reported an identity mismatch but no row was found for the id. "
            "This readback is internally inconsistent and needs operator review.",
        )
    if code == EVIDENCE_NOT_FOUND:
        if row.get("present") is None:
            return (
                "SNAPSHOT_ROW_LOOKUP_UNAVAILABLE",
                "The RPC reported NOT_FOUND but the direct row read failed. Absence is "
                "unproven; do not report a missing row from this readback.",
            )
        if row.get("present") is True:
            return (
                "PRIMARY_LOOKUP_INCONSISTENT",
                "The RPC reported NOT_FOUND while the exact primary-key row is present. "
                "The lookup is inconsistent and needs operator review.",
            )
        if other_snapshot_ids:
            return (
                "SNAPSHOT_EXISTS_UNDER_OTHER_ID",
                "No row exists for the requested snapshot id, but an exact-identity row "
                "exists under a different id. The 422 is an id/selection defect, not "
                "absent evidence.",
            )
        if candidate_status != "OK":
            return (
                "SNAPSHOT_ROW_MISSING_LOOKUP_PARTIAL",
                "The requested row is absent, but the exact-identity fallback lookup "
                "also failed. Absence is unproven.",
            )
        return (
            "SNAPSHOT_ROW_MISSING",
            "No row exists for the requested snapshot id and no exact-identity row "
            "exists under any other id. The 422 is genuinely absent governed evidence.",
        )
    return (
        "PRIMARY_LOOKUP_TYPED_FAILURE",
        "The primary RPC returned an unexpected typed code. Inspect the code; do not "
        "infer missing evidence from this readback.",
    )


def _cause_assessment(attribution: str, diagnosis: str) -> tuple[Optional[bool], str]:
    if attribution == "EXPECTED_SELF_ACCEPTANCE_PROBE":
        return False, "SELF_ACCEPTANCE_PROBE_EXPECTS_422"
    if attribution == "SELF_ACCEPTANCE_PROBE_OTHER_TERMINAL":
        return False, "SELF_ACCEPTANCE_PROBE_OTHER_TERMINAL"
    if attribution != "LIVE_ACTION_EVIDENCE_FAILURE":
        return None, "UNATTRIBUTED"
    if diagnosis in {"SNAPSHOT_ROW_MISSING", "EVIDENCE_NOT_READY"}:
        return True, "LIVE_ACTION_422_MATCHES_EVIDENCE_STATE"
    if diagnosis == "PRIMARY_LOOKUP_STAT_ALIAS_MISMATCH":
        return False, "GOVERNED_EVIDENCE_EXISTS_BUT_PRIMARY_STAT_ALIAS_MISMATCH"
    if diagnosis in {
        "SNAPSHOT_EXISTS_UNDER_OTHER_ID",
        "SNAPSHOT_ID_POINTS_TO_OTHER_IDENTITY",
    }:
        return False, "GOVERNED_EVIDENCE_EXISTS_BUT_LOOKUP_IDENTITY_DIFFERS"
    if diagnosis == "EVIDENCE_READY":
        return False, "EVIDENCE_READY_FAILURE_IS_DOWNSTREAM"
    return None, "LIVE_ACTION_422_UNRESOLVED_BY_READBACK"


def _identity_provided(request: Any) -> bool:
    return any(_get(request, name) is not None for name in _IDENTITY_FIELDS)


def _base_result(*, request_id: Optional[str], receipt: Mapping[str, Any]) -> dict[str, Any]:
    attribution = str(receipt.get("attribution") or "UNATTRIBUTED")
    return {
        "status": "OK",
        "operation": READBACK_OPERATION,
        "read_only": True,
        "can_execute": False,
        "request_id": request_id,
        "identity_fingerprint": None,
        "requested_identity": None,
        "source_snapshot_id": None,
        "receipt": receipt,
        "primary_lookup": {"status": "NOT_ATTEMPTED"},
        "snapshot_row": {"present": None, "lookup_status": "NOT_ATTEMPTED"},
        "identity_candidates": {
            "lookup_status": "NOT_ATTEMPTED",
            "count": 0,
            "other_snapshot_ids": [],
        },
        "diagnosis": "IDENTITY_NOT_PROVIDED",
        "interpretation": (
            "Only the invocation receipt was audited. No exact request identity was "
            "provided, so the evidence row cannot be resolved."
        ),
        "reported_failure_attribution": attribution,
        "reported_cause_supported": None,
        "reported_cause_basis": "UNATTRIBUTED",
    }


def diagnose_prop_evidence(
    client: Any,
    request: Any = None,
    *,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    """Audit a receipt and, when supplied, resolve the exact evidence identity."""
    resolved_request_id = request_id or _get(request, "request_id")
    resolved_request_id = str(resolved_request_id).strip() if resolved_request_id else None
    receipt = _lookup_receipt(client, resolved_request_id)
    result = _base_result(request_id=resolved_request_id, receipt=receipt)

    identity_error: Optional[str] = None
    identity: Optional[dict[str, Any]] = None
    if request is not None and _identity_provided(request):
        try:
            identity = normalize_identity(request)
        except ValueError as exc:
            identity_error = str(exc)

    if identity_error is not None:
        result["status"] = "INVALID_IDENTITY"
        result["error"] = identity_error
        result["interpretation"] = (
            "The supplied identity is invalid or incomplete; no evidence lookup was "
            "performed."
        )
        return result

    if identity is None:
        supported, basis = _cause_assessment(
            str(receipt.get("attribution") or "UNATTRIBUTED"),
            "IDENTITY_NOT_PROVIDED",
        )
        result["reported_cause_supported"] = supported
        result["reported_cause_basis"] = basis
        return result

    fingerprint = identity_fingerprint(identity)
    rpc = _rpc_lookup(client, identity, request)
    row = _lookup_row(client, identity)
    candidates = _lookup_candidates(client, identity)
    other_ids = list(candidates.get("other_snapshot_ids") or [])
    diagnosis, interpretation = classify_evidence_state(
        rpc=rpc,
        row=row,
        candidate_status=str(candidates.get("status") or "UNAVAILABLE"),
        other_snapshot_ids=other_ids,
    )
    attribution = str(receipt.get("attribution") or "UNATTRIBUTED")
    supported, basis = _cause_assessment(attribution, diagnosis)

    result.update(
        {
            "identity_fingerprint": fingerprint,
            "requested_identity": {
                "event_id": identity["event_id"],
                "sport": identity["sport"],
                "stat_type": identity["stat_type"],
                "line": identity["line"],
            },
            "source_snapshot_id": identity["source_snapshot_id"],
            "primary_lookup": rpc,
            "snapshot_row": (
                row.get("summary") if row.get("present") is True else {
                    "present": row.get("present"),
                    "lookup_status": row.get("status"),
                }
            ),
            "identity_candidates": {
                "lookup_status": candidates.get("status"),
                "count": len(other_ids),
                "other_snapshot_ids": other_ids,
            },
            "diagnosis": diagnosis,
            "interpretation": interpretation,
            "reported_failure_attribution": attribution,
            "reported_cause_supported": supported,
            "reported_cause_basis": basis,
        }
    )
    return result


def log_prop_evidence_failure(
    request: Any,
    *,
    code: Optional[str],
    request_id: Optional[str] = None,
    caller_class: Optional[str] = None,
) -> None:
    """Capture a non-PII join key at the failed evidence boundary.

    The player name is intentionally replaced by a fingerprint. The event id,
    sport, stat, line and snapshot id are public correlation fields already
    present in the scoring contract.
    """
    try:
        identity = normalize_identity(request)
    except Exception as exc:
        LOGGER.warning(
            "WOW_V17_PROP_EVIDENCE_FAILURE identity_fingerprint=UNAVAILABLE "
            "event_id=UNAVAILABLE sport=%s stat_type=%s line=UNAVAILABLE "
            "source_snapshot_id=UNAVAILABLE request_id=%s caller_class=%s "
            "code=%s error_type=%s can_execute=false",
            str(_get(request, "sport") or "UNKNOWN").upper(),
            _canonical_stat(
                _get(request, "sport"), _get(request, "stat_type")
            )
            or "UNKNOWN",
            request_id or "NONE",
            caller_class or "UNKNOWN",
            code or "UNKNOWN",
            type(exc).__name__,
        )
        return
    LOGGER.warning(
        "WOW_V17_PROP_EVIDENCE_FAILURE identity_fingerprint=%s event_id=%s "
        "sport=%s stat_type=%s line=%s source_snapshot_id=%s request_id=%s "
        "caller_class=%s code=%s can_execute=false",
        identity_fingerprint(identity),
        identity["event_id"],
        identity["sport"],
        identity["stat_type"],
        identity["line"],
        identity["source_snapshot_id"],
        request_id or "NONE",
        caller_class or "UNKNOWN",
        code or "UNKNOWN",
    )


def _client_from_env() -> Any:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_SERVICE_KEY) "
            "are required"
        )
    from supabase import create_client

    return create_client(url, key)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--request-json",
        default=None,
        help="Exact /score-prop JSON, or @path/to/request.json",
    )
    parser.add_argument("--request-id", default=None)
    args = parser.parse_args(argv)
    if not args.request_json and not args.request_id:
        parser.error("provide --request-json and/or --request-id")

    payload: Any = None
    try:
        if args.request_json:
            raw = args.request_json
            if raw.startswith("@"):
                raw = Path(raw[1:]).read_text(encoding="utf-8")
            payload = json.loads(raw)
        client = _client_from_env()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "CLI_INPUT_ERROR",
                    "error_type": type(exc).__name__,
                    "can_execute": False,
                },
                indent=2,
            )
        )
        return 2

    result = diagnose_prop_evidence(client, payload, request_id=args.request_id)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
