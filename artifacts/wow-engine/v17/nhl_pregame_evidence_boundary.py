"""Fail-closed NHL pregame evidence boundary; research validation, never certification.

This module does not score probabilities or promote a fitted artifact.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping, Any

CAN_EXECUTE = False
REQUIRED = (
    "official_event_id", "home_team", "away_team", "event_start_time_utc",
    "goalie_status", "injury_report", "rest_travel", "settlement_basis",
    "home_elo", "away_elo", "home_goalie_sv_pct", "away_goalie_sv_pct",
    "home_pp_pct", "away_pk_pct", "calibration_artifact",
)
FULL_GAME_SETTLEMENT = "HOME_AWAY_INCLUDING_OT_SO"
VERIFIED_GOALIE_STATUSES = frozenset({"CONFIRMED"})


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except ValueError:
        return None


def validate_nhl_pregame_evidence(
    evidence: Mapping[str, Any], *, as_of_utc: datetime,
    max_status_age_minutes: int = 60,
) -> dict[str, Any]:
    """Return typed blockers. PASS means only source completeness, not model readiness."""
    if as_of_utc.tzinfo is None:
        raise ValueError("as_of_utc must be timezone-aware")
    missing = [k for k in REQUIRED if evidence.get(k) is None or (isinstance(evidence.get(k), str) and not evidence.get(k).strip())]
    reasons: list[str] = []
    if missing:
        reasons.append("MODEL_INPUTS_INSUFFICIENT")
    if evidence.get("home_team") and evidence.get("home_team") == evidence.get("away_team"):
        reasons.append("NHL_DUPLICATE_OPPONENT_IDENTITY")
    if evidence.get("settlement_basis") != FULL_GAME_SETTLEMENT:
        reasons.append("NHL_SETTLEMENT_BASIS_UNVERIFIED")
    if str(evidence.get("goalie_status", "")).strip().upper() not in VERIFIED_GOALIE_STATUSES:
        reasons.append("NHL_GOALIE_STATUS_UNVERIFIED")
    status_at = _time(evidence.get("goalie_status_observed_at"))
    start_at = _time(evidence.get("event_start_time_utc"))
    now = as_of_utc.astimezone(timezone.utc)
    if status_at is None or status_at > now or (now - status_at).total_seconds() > max_status_age_minutes * 60:
        reasons.append("NHL_GOALIE_STATUS_STALE")
    if start_at is None or start_at <= now:
        reasons.append("NHL_EVENT_NOT_PREGAME")
    for key in ("home_goalie_sv_pct", "away_goalie_sv_pct", "home_pp_pct", "away_pk_pct"):
        try:
            n = float(evidence[key])
            if not 0 <= n <= 1:
                reasons.append("NHL_NUMERIC_FEATURE_INVALID")
        except (KeyError, ValueError, TypeError):
            reasons.append("NHL_NUMERIC_FEATURE_INVALID")
    for key in ("home_elo", "away_elo"):
        try:
            from math import isfinite
            if isinstance(evidence[key], bool) or not isfinite(float(evidence[key])):
                reasons.append("NHL_NUMERIC_FEATURE_INVALID")
        except (KeyError, ValueError, TypeError):
            reasons.append("NHL_NUMERIC_FEATURE_INVALID")
    if not evidence.get("source_snapshot_id"):
        reasons.append("NHL_SOURCE_SNAPSHOT_MISSING")
    if not evidence.get("goalie_identity_snapshot_id"):
        reasons.append("NHL_GOALIE_IDENTITY_UNVERIFIED")
    return {
        "status": "INPUT_EVIDENCE_PASS_NOT_CERTIFICATION" if not missing and not reasons else "HELD",
        "missing_fields": sorted(missing),
        "blockers": sorted(set(reasons)),
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }
