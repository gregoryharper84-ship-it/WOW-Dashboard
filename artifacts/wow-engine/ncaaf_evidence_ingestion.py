"""Governed persistence boundary for already-normalized NCAAF evidence rows."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False


class NCAAFAcquisitionUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def persist_normalized_evidence(db: Any, rows: Iterable[Mapping[str, Any]]) -> int:
    materialized = [dict(row) for row in rows]
    if not materialized:
        return 0
    allowed = {"PLAYER_AVAILABILITY_REPORT", "QB_STATUS", "QB_CERTAINTY", "SKILL_AVAILABILITY"}
    for row in materialized:
        if row.get("can_execute") is not False:
            raise NCAAFAcquisitionUnavailable("NCAAF_EVIDENCE_EXECUTION_FLAG_INVALID", "can_execute must be false")
        kind = str(row.get("evidence_kind") or "").upper()
        if kind not in allowed:
            raise NCAAFAcquisitionUnavailable("NCAAF_EVIDENCE_KIND_NOT_ALLOWED_BY_INGESTION", kind)
        if row.get("official_event_id") in (None, "") or row.get("event_start_time") in (None, ""):
            raise NCAAFAcquisitionUnavailable("NCAAF_EVENT_IDENTITY_INCOMPLETE", "event identity is required")
        if row.get("source_provider") in (None, "") or row.get("payload_sha256") in (None, ""):
            raise NCAAFAcquisitionUnavailable("NCAAF_EVIDENCE_PROVENANCE_INCOMPLETE", "source provider and payload hash are required")
        row.pop("probability_publishable", None)
    try:
        result = db.table("wow_ncaaf_pregame_evidence").upsert(
            materialized,
            on_conflict="official_event_id,evidence_kind,scope,source_provider,payload_sha256",
        ).execute()
    except Exception as exc:
        raise NCAAFAcquisitionUnavailable("NCAAF_EVIDENCE_PERSIST_FAILED", type(exc).__name__) from exc
    data = getattr(result, "data", None)
    return len(data) if isinstance(data, list) else len(materialized)
