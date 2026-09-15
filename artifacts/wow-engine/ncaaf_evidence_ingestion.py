"""Governed persistence boundary for already-normalized NCAAF evidence rows."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False

# These are the pregame evidence families the NCAAF feature compiler can consume
# or the governance layer can retain. Acceptance here is not sufficient for
# model use: the database provider registry, temporal constraints, provenance
# grades, payload hashes, event/team identity, and feature compiler all remain
# independent fail-closed gates.
ALLOWED_EVIDENCE_KINDS = frozenset({
    "TEAM_POWER",
    "OFF_EPA",
    "DEF_EPA",
    "SUCCESS_RATE",
    "EXPLOSIVENESS",
    "QB_STATUS",
    "QB_VALUE",
    "QB_CERTAINTY",
    "OL_HEALTH",
    "DEF_FRONT_HEALTH",
    "SKILL_AVAILABILITY",
    "REST_TRAVEL",
    "TEMPO",
    "TURNOVER_VOLATILITY",
    "SPECIAL_TEAMS",
    "WEATHER",
    "MARKET_NO_VIG",
    "PLAYER_AVAILABILITY_REPORT",
})


class NCAAFAcquisitionUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def persist_normalized_evidence(db: Any, rows: Iterable[Mapping[str, Any]]) -> int:
    materialized = [dict(row) for row in rows]
    if not materialized:
        return 0
    for row in materialized:
        if row.get("can_execute") is not False:
            raise NCAAFAcquisitionUnavailable("NCAAF_EVIDENCE_EXECUTION_FLAG_INVALID", "can_execute must be false")
        kind = str(row.get("evidence_kind") or "").upper()
        if kind not in ALLOWED_EVIDENCE_KINDS:
            raise NCAAFAcquisitionUnavailable("NCAAF_EVIDENCE_KIND_NOT_ALLOWED_BY_INGESTION", kind)
        if row.get("official_event_id") in (None, "") or row.get("event_start_time") in (None, ""):
            raise NCAAFAcquisitionUnavailable("NCAAF_EVENT_IDENTITY_INCOMPLETE", "event identity is required")
        if row.get("source_provider") in (None, "") or row.get("payload_sha256") in (None, ""):
            raise NCAAFAcquisitionUnavailable("NCAAF_EVIDENCE_PROVENANCE_INCOMPLETE", "source provider and payload hash are required")
        row["evidence_kind"] = kind
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


__all__ = [
    "ALLOWED_EVIDENCE_KINDS",
    "CAN_EXECUTE",
    "NCAAFAcquisitionUnavailable",
    "PROBABILITY_PUBLISHABLE",
    "persist_normalized_evidence",
]
