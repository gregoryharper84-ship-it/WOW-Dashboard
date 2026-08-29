"""Normalize verified official-conference NCAAF availability reports.

The output is RAW pregame evidence (`PLAYER_AVAILABILITY_REPORT`), not a
model-ready QB/skill feature. Conference-defined play percentages are preserved
only where the official policy itself defines them. No qualitative status is
silently converted to a probability.

This module does not fetch pages, approve evidence providers, construct fitted
features, score games, publish probabilities, or enable execution.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
RAW_EVIDENCE_KIND = "PLAYER_AVAILABILITY_REPORT"


class NCAAvailabilityUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AvailabilityPolicy:
    conference: str
    provider_key: str
    policy_version: str
    policy_url: str
    report_url: str
    pregame_statuses: tuple[str, ...]
    gameday_statuses: tuple[str, ...]
    source_defined_play_probability: Mapping[str, float]
    not_listed_means_available: bool = False


POLICIES: dict[str, AvailabilityPolicy] = {
    "BIG12": AvailabilityPolicy(
        conference="BIG12",
        provider_key="BIG12_OFFICIAL_AVAILABILITY",
        policy_version="BIG12_AVAILABILITY_2025_26",
        policy_url="https://big12sports.com/sports/2025/8/21/availability-reporting.aspx",
        report_url="https://big12sports.com/sports/2025/8/14/FBreporting.aspx",
        pregame_statuses=("AVAILABLE", "PROBABLE", "QUESTIONABLE", "DOUBTFUL", "OUT"),
        gameday_statuses=("AVAILABLE", "GAME TIME DECISION", "OUT"),
        source_defined_play_probability={
            "AVAILABLE": 1.00,
            "PROBABLE": 0.75,
            "QUESTIONABLE": 0.50,
            "DOUBTFUL": 0.25,
            "OUT": 0.00,
        },
    ),
    "ACC": AvailabilityPolicy(
        conference="ACC",
        provider_key="ACC_OFFICIAL_AVAILABILITY",
        policy_version="ACC_AVAILABILITY_2026",
        policy_url="https://theacc.com/sports/2025/8/28/availability-reporting.aspx",
        report_url="https://theacc.com/sports/2025/8/28/availability-reporting-football.aspx",
        pregame_statuses=("AVAILABLE", "PROBABLE", "QUESTIONABLE", "DOUBTFUL", "OUT"),
        gameday_statuses=("AVAILABLE", "GAME TIME DECISION", "OUT"),
        source_defined_play_probability={
            "AVAILABLE": 1.00,
            "PROBABLE": 0.75,
            "QUESTIONABLE": 0.50,
            "DOUBTFUL": 0.25,
            "OUT": 0.00,
        },
    ),
    "BIGTEN": AvailabilityPolicy(
        conference="BIGTEN",
        provider_key="BIGTEN_OFFICIAL_AVAILABILITY",
        policy_version="BIGTEN_AVAILABILITY_2026",
        policy_url="https://bigten.org/fb/article/60284/",
        report_url="https://bigten.org/FBReports",
        pregame_statuses=("PROBABLE", "QUESTIONABLE", "DOUBTFUL", "OUT", "OUT (1ST HALF)"),
        gameday_statuses=("GAME TIME DECISION", "OUT", "OUT (1ST HALF)"),
        source_defined_play_probability={},
        not_listed_means_available=True,
    ),
    "SUNBELT": AvailabilityPolicy(
        conference="SUNBELT",
        provider_key="SUNBELT_OFFICIAL_AVAILABILITY",
        policy_version="SUNBELT_AVAILABILITY_2025_PLUS",
        policy_url="https://sunbeltsports.org/news/2025/8/20/sun-belt-to-institute-availability-reporting-for-2025-football-season.aspx",
        report_url="https://sunbeltsports.org/news/2025/8/11/football-availability-report-new.aspx",
        pregame_statuses=("AVAILABLE", "PROBABLE", "QUESTIONABLE", "DOUBTFUL", "OUT"),
        gameday_statuses=("AVAILABLE", "GAME TIME DECISION", "OUT"),
        source_defined_play_probability={},
    ),
}


def candidate_provider_rows() -> list[dict[str, Any]]:
    """Return REVIEW-ONLY provider rows. They are never active by default."""
    return [
        {
            "provider_key": policy.provider_key,
            "provider_class": "OFFICIAL_CONFERENCE",
            "max_provenance_grade": "A",
            "allowed_evidence_kinds": [RAW_EVIDENCE_KIND],
            "active": False,
            "approval_reference": policy.policy_url,
            "notes": f"Candidate official conference availability source; parser/source review required before activation ({policy.policy_version}).",
            "can_execute": False,
        }
        for policy in POLICIES.values()
    ]


def _aware(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise NCAAvailabilityUnavailable("NCAAF_AVAILABILITY_TIMESTAMP_INVALID", f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NCAAvailabilityUnavailable("NCAAF_AVAILABILITY_TIMESTAMP_INVALID", f"{field} is malformed") from exc
    if parsed.utcoffset() is None:
        raise NCAAvailabilityUnavailable("NCAAF_AVAILABILITY_TIMESTAMP_INVALID", f"{field} must be offset-aware")
    return parsed


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_status(value: Any) -> str:
    return " ".join(str(value or "").strip().upper().split())


def normalize_player_availability(
    *,
    conference: str,
    official_event_id: str,
    event_start_time: str,
    report_timestamp: str,
    report_phase: str,
    team: str,
    player: str,
    status: str,
    position: Optional[str] = None,
    source_record_id: Optional[str] = None,
    source_uri: Optional[str] = None,
) -> dict[str, Any]:
    """Normalize one official conference player-availability observation.

    `report_phase` is PRE_GAME or GAME_DAY. Missing/unlisted players are never
    synthesized here, even for policies that define unlisted players as
    available; doing so requires a separately proven roster snapshot.
    """
    key = str(conference or "").strip().upper().replace(" ", "")
    policy = POLICIES.get(key)
    if policy is None:
        raise NCAAvailabilityUnavailable(
            "NCAAF_AVAILABILITY_POLICY_UNVERIFIED",
            f"No verified availability normalization policy is installed for {conference!r}.",
        )

    kickoff = _aware(event_start_time, "event_start_time")
    reported = _aware(report_timestamp, "report_timestamp")
    if reported >= kickoff:
        raise NCAAvailabilityUnavailable(
            "NCAAF_AVAILABILITY_NOT_PREGAME",
            "Availability evidence must be timestamped strictly before kickoff.",
        )

    phase = str(report_phase or "").strip().upper()
    if phase not in {"PRE_GAME", "GAME_DAY"}:
        raise NCAAvailabilityUnavailable("NCAAF_AVAILABILITY_PHASE_INVALID", "report_phase must be PRE_GAME or GAME_DAY")

    normalized_status = _normalize_status(status)
    allowed = policy.pregame_statuses if phase == "PRE_GAME" else policy.gameday_statuses
    if normalized_status not in allowed:
        raise NCAAvailabilityUnavailable(
            "NCAAF_AVAILABILITY_STATUS_UNRECOGNIZED",
            f"Status {normalized_status!r} is not defined by {policy.policy_version} for {phase}.",
        )

    payload: dict[str, Any] = {
        "conference": policy.conference,
        "policy_version": policy.policy_version,
        "report_phase": phase,
        "status": normalized_status,
        "position": str(position).strip().upper() if position else None,
        "source_defined_play_probability": None,
        "probability_source": None,
        "not_listed_means_available": policy.not_listed_means_available,
    }
    if normalized_status in policy.source_defined_play_probability:
        payload["source_defined_play_probability"] = float(policy.source_defined_play_probability[normalized_status])
        payload["probability_source"] = "OFFICIAL_CONFERENCE_POLICY"

    hash_payload = {
        **payload,
        "team": str(team),
        "player": str(player),
        "official_event_id": str(official_event_id),
        "report_timestamp": reported.isoformat(),
    }
    return {
        "official_event_id": str(official_event_id),
        "event_start_time": kickoff.isoformat(),
        "evidence_kind": RAW_EVIDENCE_KIND,
        "scope": "EVENT",
        "team": None,
        "player": str(player),
        "source_provider": policy.provider_key,
        "source_record_id": source_record_id,
        "source_uri": source_uri or policy.report_url,
        "evidence_timestamp": reported.isoformat(),
        "provenance_grade": "A",
        "payload": {**payload, "team": str(team)},
        "payload_sha256": _canonical_hash(hash_payload),
        "blocker_codes": [],
        "probability_publishable": False,
        "can_execute": False,
    }


def normalize_report_rows(
    *,
    conference: str,
    official_event_id: str,
    event_start_time: str,
    report_timestamp: str,
    report_phase: str,
    team: str,
    players: Iterable[Mapping[str, Any]],
    source_record_id: Optional[str] = None,
    source_uri: Optional[str] = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for player in players:
        rows.append(
            normalize_player_availability(
                conference=conference,
                official_event_id=official_event_id,
                event_start_time=event_start_time,
                report_timestamp=report_timestamp,
                report_phase=report_phase,
                team=team,
                player=str(player.get("player") or player.get("name") or ""),
                status=str(player.get("status") or ""),
                position=str(player.get("position")) if player.get("position") is not None else None,
                source_record_id=source_record_id,
                source_uri=source_uri,
            )
        )
    return rows
