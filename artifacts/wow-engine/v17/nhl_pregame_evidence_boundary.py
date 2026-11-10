"""Fail-closed NHL pregame evidence boundary; research validation, never certification.

This module does not score probabilities or promote a fitted artifact.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping, Any

from v17.nhl_event_identity import team_abbreviation
from v17.nhl_starter_goalie_confirmation import MIN_INDEPENDENT_SOURCES

CAN_EXECUTE = False
REQUIRED = (
    "official_event_id", "home_team", "away_team", "event_start_time_utc",
    "goalie_status", "injury_report", "rest_travel", "settlement_basis",
    "home_elo", "away_elo", "home_goalie_sv_pct", "away_goalie_sv_pct",
    "home_pp_pct", "away_pk_pct", "calibration_artifact",
)
FULL_GAME_SETTLEMENT = "HOME_AWAY_INCLUDING_OT_SO"
VERIFIED_GOALIE_STATUSES = frozenset({"CONFIRMED"})
STRUCTURED_EVIDENCE = frozenset({"injury_report", "rest_travel", "calibration_artifact"})


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _goalie_confirmation_blockers(
    confirmation: Mapping[str, Any] | None,
    evidence: Mapping[str, Any],
    *,
    official_id: Any,
) -> list[str]:
    """Require an independently confirmed starter receipt bound to this event.

    A caller-provided ``goalie_status='CONFIRMED'`` string is not proof. The
    receipt must come from ``v17.nhl_starter_goalie_confirmation``, carry at
    least two independent official sources, and agree with the evidence row on
    the exact event, snapshot identity and observation instant.
    """
    if not isinstance(confirmation, Mapping):
        return ["NHL_GOALIE_CONFIRMATION_RECEIPT_MISSING"]
    blockers: list[str] = []
    if (
        confirmation.get("status") != "STARTER_GOALIE_CONFIRMED_RESEARCH_ONLY"
        or confirmation.get("code") != "NHL_STARTER_GOALIE_INDEPENDENTLY_CONFIRMED"
        or str(confirmation.get("official_event_id") or "") != str(official_id or "")
        or str(confirmation.get("goalie_status") or "").strip().upper() != "CONFIRMED"
        or confirmation.get("blockers")
    ):
        blockers.append("NHL_GOALIE_CONFIRMATION_RECEIPT_NOT_CONFIRMED")
    count = confirmation.get("independent_source_count")
    if type(count) is not int or count < MIN_INDEPENDENT_SOURCES:
        blockers.append("NHL_GOALIE_CONFIRMATION_SOURCES_NOT_INDEPENDENT")
    receipt_snapshot = confirmation.get("goalie_identity_snapshot_id")
    if (
        not isinstance(receipt_snapshot, str)
        or not receipt_snapshot.strip()
        or receipt_snapshot != evidence.get("goalie_identity_snapshot_id")
    ):
        blockers.append("NHL_GOALIE_IDENTITY_SNAPSHOT_MISMATCH")
    receipt_at = _time(confirmation.get("goalie_status_observed_at"))
    evidence_at = _time(evidence.get("goalie_status_observed_at"))
    if receipt_at is None or evidence_at is None or receipt_at != evidence_at:
        blockers.append("NHL_GOALIE_CONFIRMATION_TIME_MISMATCH")
    home_goalie = str(confirmation.get("home_goalie_id") or "").strip()
    away_goalie = str(confirmation.get("away_goalie_id") or "").strip()
    if not home_goalie or not away_goalie or home_goalie == away_goalie:
        blockers.append("NHL_GOALIE_IDENTITY_UNVERIFIED")
    if (
        confirmation.get("probability_publishable") is not False
        or confirmation.get("rank_eligible") is not False
        or confirmation.get("can_execute") is not False
    ):
        blockers.append("NHL_GOALIE_CONFIRMATION_AUTHORITY_INVALID")
    return blockers


def validate_nhl_pregame_evidence(
    evidence: Mapping[str, Any], *, as_of_utc: datetime,
    max_status_age_minutes: int = 60,
    resolved_identity: Mapping[str, Any] | None = None,
    goalie_confirmation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return typed blockers. PASS means only source completeness, not model readiness.

    A goalie can pass only with two independent official starter receipts. A
    caller's ``goalie_status`` label alone never satisfies the gate.
    """
    if as_of_utc.tzinfo is None:
        raise ValueError("as_of_utc must be timezone-aware")
    def _missing_required(value: Any) -> bool:
        return (value is None
                or (isinstance(value, str) and not value.strip())
                or (isinstance(value, (Mapping, list, tuple, set, frozenset)) and not value))

    missing = [
        k for k in REQUIRED
        if (_missing_required(evidence.get(k))
            or (k in STRUCTURED_EVIDENCE
                and (not isinstance(evidence.get(k), Mapping) or not evidence[k])))
    ]
    reasons: list[str] = []
    if missing:
        reasons.append("MODEL_INPUTS_INSUFFICIENT")
    # Source aliases are not canonical identity: require recognized NHL teams.
    home_raw, away_raw = evidence.get("home_team"), evidence.get("away_team")
    home = team_abbreviation(home_raw) if isinstance(home_raw, str) else None
    away = team_abbreviation(away_raw) if isinstance(away_raw, str) else None
    if not home or not away:
        reasons.append("NHL_TEAM_IDENTITY_UNVERIFIED")
    elif home == away:
        reasons.append("NHL_DUPLICATE_OPPONENT_IDENTITY")
    official_id = evidence.get("official_event_id")
    if not (isinstance(official_id, str) and len(official_id) == 10 and official_id.isascii() and official_id.isdigit()):
        reasons.append("NHL_EVENT_IDENTITY_UNVERIFIED")
    if evidence.get("settlement_basis") != FULL_GAME_SETTLEMENT:
        reasons.append("NHL_SETTLEMENT_BASIS_UNVERIFIED")
    if str(evidence.get("goalie_status", "")).strip().upper() not in VERIFIED_GOALIE_STATUSES:
        reasons.append("NHL_GOALIE_STATUS_UNVERIFIED")
    status_at = _time(evidence.get("goalie_status_observed_at"))
    start_at = _time(evidence.get("event_start_time_utc"))
    now = as_of_utc.astimezone(timezone.utc)
    # This must be supplied by the trusted server-side NHL schedule resolver,
    # never taken from caller-controlled sport_specific_evidence. A numeric
    # string or a claimed provider name is not sufficient canonical identity.
    if not isinstance(resolved_identity, Mapping):
        reasons.append("NHL_CANONICAL_IDENTITY_RECEIPT_MISSING")
    else:
        source = resolved_identity.get("identity_source")
        verified_at = _time(resolved_identity.get("identity_verified_at"))
        resolved_start = _time(resolved_identity.get("event_start_time"))
        receipt_home = resolved_identity.get("home_team")
        receipt_away = resolved_identity.get("away_team")
        receipt_home = team_abbreviation(receipt_home) if isinstance(receipt_home, str) else None
        receipt_away = team_abbreviation(receipt_away) if isinstance(receipt_away, str) else None
        if (
            resolved_identity.get("identity_provider") != "NHL_PUBLIC_WEB_API"
            or not isinstance(source, str)
            or not source.startswith("https://api-web.nhle.com/v1/club-schedule-season/")
            or not isinstance(official_id, str)
            or str(resolved_identity.get("event_id") or "") != official_id
            or not home or not away
            or receipt_home != home or receipt_away != away
            or resolved_start is None or start_at is None
            or abs((resolved_start - start_at).total_seconds()) > 30 * 60
            or verified_at is None or (verified_at - now).total_seconds() > 5 * 60
            or (now - verified_at).total_seconds() > 24 * 3600
            or resolved_identity.get("market_features_used") is not False
            or resolved_identity.get("prediction_authority") is not False
            or resolved_identity.get("can_execute") is not False
        ):
            reasons.append("NHL_CANONICAL_IDENTITY_RECEIPT_MISMATCH")
    if status_at is None or status_at > now or (now - status_at).total_seconds() > max_status_age_minutes * 60:
        reasons.append("NHL_GOALIE_STATUS_STALE")
    reasons.extend(
        _goalie_confirmation_blockers(
            goalie_confirmation, evidence, official_id=official_id
        )
    )
    # A caller may supply a start time up to 30 minutes different from the
    # official schedule. Only the server-owned resolved start can prove that
    # the game is still pregame; a later caller timestamp cannot override it.
    if (
        start_at is None
        or start_at <= now
        or (isinstance(resolved_identity, Mapping)
            and (resolved_start is None or resolved_start <= now))
    ):
        reasons.append("NHL_EVENT_NOT_PREGAME")
    for key in ("home_goalie_sv_pct", "away_goalie_sv_pct", "home_pp_pct", "away_pk_pct"):
        try:
            n = float(evidence[key])
            from math import isfinite
            if isinstance(evidence[key], bool) or not isinstance(evidence[key], (int, float)) or not isfinite(n) or not 0 <= n <= 1:
                reasons.append("NHL_NUMERIC_FEATURE_INVALID")
        except (KeyError, ValueError, TypeError, OverflowError):
            reasons.append("NHL_NUMERIC_FEATURE_INVALID")
    for key in ("home_elo", "away_elo"):
        try:
            from math import isfinite
            if isinstance(evidence[key], bool) or not isinstance(evidence[key], (int, float)) or not isfinite(float(evidence[key])):
                reasons.append("NHL_NUMERIC_FEATURE_INVALID")
        except (KeyError, ValueError, TypeError, OverflowError):
            reasons.append("NHL_NUMERIC_FEATURE_INVALID")
    if not isinstance(evidence.get("source_snapshot_id"), str) or not evidence["source_snapshot_id"].strip():
        reasons.append("NHL_SOURCE_SNAPSHOT_MISSING")
    if not isinstance(evidence.get("goalie_identity_snapshot_id"), str) or not evidence["goalie_identity_snapshot_id"].strip():
        reasons.append("NHL_GOALIE_IDENTITY_UNVERIFIED")
    return {
        "status": "INPUT_EVIDENCE_PASS_NOT_CERTIFICATION" if not missing and not reasons else "HELD",
        "missing_fields": sorted(missing),
        "blockers": sorted(set(reasons)),
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }
