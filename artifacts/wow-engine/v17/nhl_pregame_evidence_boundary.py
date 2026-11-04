"""Fail-closed NHL pregame evidence boundary; research validation, never certification.

This module does not score probabilities or promote a fitted artifact.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping, Any

from v17.nhl_event_identity import team_abbreviation

CAN_EXECUTE = False
REQUIRED = (
    "official_event_id", "home_team", "away_team", "event_start_time_utc",
    "goalie_status", "injury_report", "rest_travel", "settlement_basis",
    "home_elo", "away_elo", "home_goalie_sv_pct", "away_goalie_sv_pct",
    "home_pp_pct", "away_pk_pct", "calibration_artifact",
)
# Outcome-space label used by the NHL fitted model. It is not a settlement
# contract name and must not be compared as one.
FULL_GAME_SETTLEMENT = "HOME_AWAY_INCLUDING_OT_SO"
# Externally settled market contract for the same full-game home/away winner
# including overtime and shootout. Kept deliberately distinct from the outcome
# label; both are accepted here and no other basis is.
SETTLEMENT_CONTRACT = "FULL_GAME_MONEYLINE_INCLUDING_OVERTIME_SHOOTOUT"
ACCEPTED_SETTLEMENT_BASES = frozenset({FULL_GAME_SETTLEMENT, SETTLEMENT_CONTRACT})
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


def validate_nhl_pregame_evidence(
    evidence: Mapping[str, Any], *, as_of_utc: datetime,
    max_status_age_minutes: int = 60,
    resolved_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return typed blockers. PASS means only source completeness, not model readiness."""
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
    if evidence.get("settlement_basis") not in ACCEPTED_SETTLEMENT_BASES:
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
