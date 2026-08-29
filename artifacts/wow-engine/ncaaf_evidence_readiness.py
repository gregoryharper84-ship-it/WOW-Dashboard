"""Fail-closed completeness assessment for governed NCAAF pregame evidence.

This module does not derive or publish probabilities. It answers only whether
an event has the complete timestamped evidence families required to proceed to
feature construction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False

TEAM_SCOPED_KINDS = (
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
)
EVENT_SCOPED_KINDS = ("WEATHER",)
OPTIONAL_EVENT_KINDS = ("MARKET_NO_VIG",)
REQUIRED_SCOPES = ("HOME", "AWAY")


@dataclass(frozen=True)
class EvidenceReadiness:
    official_event_id: str
    ready: bool
    required_slots: int
    satisfied_slots: int
    missing_slots: tuple[str, ...]
    rejected_rows: int
    blocker_codes: tuple[str, ...]
    can_execute: bool = False
    probability_publishable: bool = False


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def assess_pregame_evidence(
    *,
    official_event_id: str,
    event_start_time: str,
    rows: Iterable[Mapping[str, Any]],
) -> EvidenceReadiness:
    kickoff = _parse_ts(event_start_time)
    if kickoff is None:
        raise ValueError("event_start_time must be an offset-aware ISO-8601 timestamp")

    required = {
        *(f"{kind}:{scope}" for kind in TEAM_SCOPED_KINDS for scope in REQUIRED_SCOPES),
        *(f"{kind}:EVENT" for kind in EVENT_SCOPED_KINDS),
    }
    satisfied: set[str] = set()
    rejected = 0

    for row in rows:
        if str(row.get("official_event_id") or "") != official_event_id:
            rejected += 1
            continue
        ts = _parse_ts(row.get("evidence_timestamp"))
        if ts is None or ts >= kickoff:
            rejected += 1
            continue
        grade = str(row.get("provenance_grade") or "UNVERIFIED").upper()
        if grade not in {"A", "B", "C"}:
            rejected += 1
            continue
        blockers = row.get("blocker_codes")
        if not isinstance(blockers, list) or blockers:
            rejected += 1
            continue

        kind = str(row.get("evidence_kind") or "").upper()
        scope = str(row.get("scope") or "").upper()
        slot = f"{kind}:{scope}"
        if slot in required:
            satisfied.add(slot)

    missing = tuple(sorted(required.difference(satisfied)))
    blockers: list[str] = []
    if missing:
        blockers.append("NCAAF_PREGAME_EVIDENCE_INCOMPLETE")
    if rejected:
        blockers.append("NCAAF_PREGAME_EVIDENCE_ROWS_REJECTED")

    return EvidenceReadiness(
        official_event_id=official_event_id,
        ready=not missing,
        required_slots=len(required),
        satisfied_slots=len(satisfied),
        missing_slots=missing,
        rejected_rows=rejected,
        blocker_codes=tuple(blockers),
    )
