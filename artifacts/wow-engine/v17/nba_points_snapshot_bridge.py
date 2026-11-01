"""Research-only NBA POINTS pregame source-to-canonical-snapshot bridge.

Consumes provenance-bearing records supplied by a separate source adapter. It
does not fetch data, create a line, score, calibrate, certify, write, publish or
execute. The existing V17 validator and snapshot factory own the payload.
"""
from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

CAN_EXECUTE = False
SPORT, STAT_TYPE = "NBA", "POINTS"


class NBAPointsSourceError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _text(obj: Mapping[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise NBAPointsSourceError(f"NBA_POINTS_{key.upper()}_MISSING")
    return value.strip()


def _time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise NBAPointsSourceError(f"NBA_POINTS_{field.upper()}_MISSING")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NBAPointsSourceError(f"NBA_POINTS_{field.upper()}_INVALID") from exc
    if timestamp.utcoffset() is None:
        raise NBAPointsSourceError(f"NBA_POINTS_{field.upper()}_NAIVE")
    return timestamp.astimezone(timezone.utc)


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise NBAPointsSourceError(f"NBA_POINTS_{field.upper()}_INVALID")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NBAPointsSourceError(f"NBA_POINTS_{field.upper()}_INVALID") from exc
    if not math.isfinite(result):
        raise NBAPointsSourceError(f"NBA_POINTS_{field.upper()}_INVALID")
    return result


def build_nba_points_snapshot(
    *,
    target: Mapping[str, Any],
    roster: Mapping[str, Any],
    line_observation: Mapping[str, Any],
    evidence: Mapping[str, Any],
    verified_roster_receipt: Mapping[str, Any] | None = None,
    now: datetime | None = None,
    resolve_identity: Callable[..., Mapping[str, Any]] | None = None,
    validate_evidence: Callable[[Any, str], dict[str, Any]] | None = None,
    snapshot_factory: Callable[[Any, dict[str, Any]], tuple[str, str, dict[str, Any]]] | None = None,
    row_factory: Callable[..., Any] | None = None,
    evidence_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Verify exact source identity before passing through the existing V17 contract."""
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise NBAPointsSourceError("NBA_POINTS_NOW_NAIVE")
    now = now.astimezone(timezone.utc)
    event_start_string = _text(target, "event_start_time")
    event_start = _time(event_start_string, "event_start_time")
    if event_start <= now:
        raise NBAPointsSourceError("NBA_POINTS_EVENT_NOT_PREGAME")
    captured = _time(evidence.get("captured_at"), "captured_at")
    if captured > now or captured >= event_start:
        raise NBAPointsSourceError("NBA_POINTS_CAPTURE_NOT_PREGAME")

    home, away = _text(target, "home_team_alias"), _text(target, "away_team_alias")
    player, player_id = _text(target, "player"), _text(target, "player_id")
    team_id, opponent_id = _text(target, "team_id"), _text(target, "opponent_id")
    if team_id == opponent_id:
        raise NBAPointsSourceError("NBA_POINTS_TEAM_IDENTITY_CONFLICT")
    direction = _text(target, "direction").upper()
    if direction not in {"MORE", "LESS"}:
        raise NBAPointsSourceError("NBA_POINTS_DIRECTION_INVALID")
    if str(target.get("period") or "").strip().upper() != "FULL_GAME":
        raise NBAPointsSourceError("NBA_POINTS_PERIOD_UNSUPPORTED")
    line = _number(target.get("line"), "line")
    if line <= 0:
        raise NBAPointsSourceError("NBA_POINTS_LINE_UNSUPPORTED")

    line_provider = _text(line_observation, "provider")
    if str(line_observation.get("line_type") or "") != "EXACT_BOARD_LINE":
        raise NBAPointsSourceError("NBA_POINTS_ADJACENT_LINE_NOT_EXACT")
    if _number(line_observation.get("line"), "observed_line") != line:
        raise NBAPointsSourceError("NBA_POINTS_EXACT_LINE_MISMATCH")
    line_captured = _time(line_observation.get("captured_at"), "line_timestamp")
    if line_captured > captured or line_captured >= event_start:
        raise NBAPointsSourceError("NBA_POINTS_LINE_TIMESTAMP_INVALID")

    if resolve_identity is None:
        from v17.nba_event_identity import resolve_nba_current_event_identity
        resolve_identity = resolve_nba_current_event_identity
    try:
        canonical = dict(resolve_identity(
            event_start_time=event_start_string,
            home_team_alias=home,
            away_team_alias=away,
        ))
    except Exception as exc:
        raise NBAPointsSourceError("NBA_POINTS_CANONICAL_EVENT_UNRESOLVED") from exc
    event_id = _text(canonical, "event_id")
    if canonical.get("identity_resolution") != "SPORTSDATAVERSE_SCHEDULE_EXACT_TEAM_DATE_MATCH":
        raise NBAPointsSourceError("NBA_POINTS_CANONICAL_EVENT_UNVERIFIED")
    expected = {str(canonical.get("home_team_id") or ""), str(canonical.get("away_team_id") or "")}
    if {team_id, opponent_id} != expected:
        raise NBAPointsSourceError("NBA_POINTS_CANONICAL_TEAM_MISMATCH")

    # A line for another event/player/side is not exact evidence merely because
    # its numeric value happens to match the requested board line.
    if _text(line_observation, "event_id") != event_id:
        raise NBAPointsSourceError("NBA_POINTS_BOARD_EVENT_MISMATCH")
    if _text(line_observation, "player_id") != player_id:
        raise NBAPointsSourceError("NBA_POINTS_BOARD_PLAYER_MISMATCH")
    if _text(line_observation, "stat_type").upper() != STAT_TYPE:
        raise NBAPointsSourceError("NBA_POINTS_BOARD_STAT_MISMATCH")
    if _text(line_observation, "direction").upper() != direction:
        raise NBAPointsSourceError("NBA_POINTS_BOARD_DIRECTION_MISMATCH")
    if _text(line_observation, "period").upper() != "FULL_GAME":
        raise NBAPointsSourceError("NBA_POINTS_BOARD_PERIOD_MISMATCH")
    if _time(line_observation.get("event_start_time"), "board_event_start_time") != event_start:
        raise NBAPointsSourceError("NBA_POINTS_BOARD_START_MISMATCH")
    # A season-roster match does not prove this player's actual current
    # game-specific availability; bind the roster observation to this event.
    if _text(roster, "event_id") != event_id:
        raise NBAPointsSourceError("NBA_POINTS_ROSTER_EVENT_MISMATCH")

    roster_id, roster_team, roster_name = (_text(roster, key) for key in ("player_id", "team_id", "player"))
    if (roster_id != player_id or roster_team != team_id
            or " ".join(roster_name.split()).casefold() != " ".join(player.split()).casefold()):
        raise NBAPointsSourceError("NBA_POINTS_ROSTER_PLAYER_MISMATCH")
    if _text(roster, "availability").upper() not in {"ACTIVE", "AVAILABLE", "CONFIRMED_ACTIVE"}:
        raise NBAPointsSourceError("NBA_POINTS_PLAYER_AVAILABILITY_HOLD")
    roster_time = _time(roster.get("captured_at"), "roster_timestamp")
    if roster_time > captured or roster_time >= event_start:
        raise NBAPointsSourceError("NBA_POINTS_ROSTER_TIMESTAMP_INVALID")
    if not isinstance(evidence.get("role_status"), Mapping):
        raise NBAPointsSourceError("NBA_POINTS_ROLE_EVIDENCE_MISSING")
    role_time = _time(evidence.get("role_timestamp"), "role_timestamp")
    if role_time > captured or role_time >= event_start:
        raise NBAPointsSourceError("NBA_POINTS_ROLE_TIMESTAMP_INVALID")

    logs, points = evidence.get("box_score_log"), evidence.get("game_log")
    if (not isinstance(logs, list) or not isinstance(points, list)
            or len(logs) < 10 or len(points) < 10 or len(logs) != len(points)):
        raise NBAPointsSourceError("NBA_POINTS_L10_EVIDENCE_INCOMPLETE")
    seen_events: set[str] = set()
    for log, value in zip(logs, points):
        if not isinstance(log, Mapping):
            raise NBAPointsSourceError("NBA_POINTS_PRIOR_GAME_INVALID")
        prior_id = _text(log, "event_id")
        if prior_id in seen_events or prior_id == event_id:
            raise NBAPointsSourceError("NBA_POINTS_PRIOR_GAME_DUPLICATE_OR_TARGET")
        seen_events.add(prior_id)
        if _text(log, "player_id") != player_id:
            raise NBAPointsSourceError("NBA_POINTS_PRIOR_GAME_PLAYER_MISMATCH")
        prior_start = _time(log.get("event_start_time"), "prior_game_start")
        if prior_start >= event_start or prior_start >= captured:
            raise NBAPointsSourceError("NBA_POINTS_PRIOR_GAME_FUTURE_LEAKAGE")
        if _number(value, "game_points") != _number(log.get("points"), "box_points"):
            raise NBAPointsSourceError("NBA_POINTS_GAME_BOX_SCORE_MISMATCH")
    source_times = evidence.get("source_timestamps")
    if not isinstance(source_times, Mapping) or not source_times:
        raise NBAPointsSourceError("NBA_POINTS_SOURCE_TIMESTAMPS_MISSING")
    for key, raw_timestamp in source_times.items():
        if not isinstance(key, str) or not key.strip():
            raise NBAPointsSourceError("NBA_POINTS_SOURCE_IDENTITY_INVALID")
        timestamp = _time(raw_timestamp, "source_timestamp")
        if timestamp > captured or timestamp >= event_start:
            raise NBAPointsSourceError("NBA_POINTS_SOURCE_TIMESTAMP_INVALID")

    # An optional, independently verified ESPN/NBA pregame roster receipt is
    # the *only* path to settlement-ready source identity. A generic source
    # snapshot without this receipt remains research-only and may not persist.
    evidence_for_factory = dict(evidence)
    verified_identity: dict[str, Any] | None = None
    if verified_roster_receipt is not None:
        if (verified_roster_receipt.get("status")
                != "NBA_CURRENT_ROSTER_ACTIVE_SOURCE_VERIFIED_ROLE_NOT_PROVEN"
                or verified_roster_receipt.get("write_performed") is not False
                or verified_roster_receipt.get("can_execute") is not False):
            raise NBAPointsSourceError("NBA_POINTS_OFFICIAL_ROSTER_RECEIPT_REQUIRED")
        verified_roster = verified_roster_receipt.get("roster")
        proof = verified_roster_receipt.get("source_identity")
        if not isinstance(verified_roster, Mapping) or not isinstance(proof, Mapping):
            raise NBAPointsSourceError("NBA_POINTS_OFFICIAL_ROSTER_RECEIPT_REQUIRED")
        if any(verified_roster.get(k) != roster.get(k) for k in (
                "event_id", "player_id", "team_id", "player", "availability", "captured_at")):
            raise NBAPointsSourceError("NBA_POINTS_OFFICIAL_ROSTER_RECEIPT_CONTRADICTION")
        if (proof.get("espn_event_id") != event_id
                or str(proof.get("nba_person_id")) != player_id
                or not re.fullmatch(r"[0-9]{10}", str(proof.get("nba_game_id") or ""))
                or not re.fullmatch(r"[A-Z]{2,4}", str(proof.get("team_tricode") or ""))
                or not isinstance(proof.get("source_names"), list)
                or not {"ESPN_GAME_SUMMARY", "NBA_CDN_LIVE_BOXSCORE"}.issubset(
                    set(str(x) for x in proof["source_names"] if isinstance(x, str))
                )
                or _time(proof.get("event_start_time"), "official_event_start") != event_start):
            raise NBAPointsSourceError("NBA_POINTS_OFFICIAL_ROSTER_RECEIPT_CONTRADICTION")
        for key in ("espn_captured_at", "nba_captured_at"):
            source_time = _time(proof.get(key), key)
            if source_time > captured or source_time >= event_start:
                raise NBAPointsSourceError("NBA_POINTS_OFFICIAL_ROSTER_RECEIPT_CONTRADICTION")
        threshold = line_observation.get("literal_threshold")
        if not isinstance(threshold, str) or not threshold.strip():
            raise NBAPointsSourceError("NBA_POINTS_LITERAL_EXACT_OFFER_REQUIRED")
        if str(line_observation.get("threshold_type") or "").upper() != "EXACT_NUMERIC":
            raise NBAPointsSourceError("NBA_POINTS_LITERAL_EXACT_OFFER_REQUIRED")
        try:
            offered = Decimal(threshold)
        except InvalidOperation as exc:
            raise NBAPointsSourceError("NBA_POINTS_LITERAL_EXACT_OFFER_REQUIRED") from exc
        if not offered.is_finite() or offered != Decimal(str(line)):
            raise NBAPointsSourceError("NBA_POINTS_LITERAL_EXACT_OFFER_REQUIRED")
        role = dict(evidence["role_status"])
        ledger = evidence.get("opportunity_ledger")
        if not isinstance(ledger, Mapping):
            raise NBAPointsSourceError("NBA_POINTS_LITERAL_EXACT_OFFER_REQUIRED")
        ledger = dict(ledger)
        for key, value in (
            ("period", "FULL_GAME"),
            ("literal_threshold", threshold),
            ("threshold_type", "EXACT_NUMERIC"),
        ):
            if key in ledger and ledger[key] != value:
                raise NBAPointsSourceError("NBA_POINTS_LITERAL_EXACT_OFFER_CONTRADICTION")
            ledger[key] = value
        for key, value in (
            ("source_identity", dict(proof)),
            ("official_game_id", proof["nba_game_id"]),
            ("player_id", proof["nba_person_id"]),
        ):
            if key in role and role[key] != value:
                raise NBAPointsSourceError("NBA_POINTS_OFFICIAL_ROSTER_RECEIPT_CONTRADICTION")
            role[key] = value
        evidence_for_factory["role_status"] = role
        evidence_for_factory["opportunity_ledger"] = ledger
        verified_identity = dict(proof)

    if any(obj is None for obj in (row_factory, evidence_factory, validate_evidence, snapshot_factory)):
        from pick_request_runtime_core import PickRequestRow, RawPropEvidence, _validate_evidence, _snapshot_payload
        row_factory = row_factory or PickRequestRow
        evidence_factory = evidence_factory or RawPropEvidence
        validate_evidence = validate_evidence or _validate_evidence
        snapshot_factory = snapshot_factory or _snapshot_payload
    frozen = evidence_factory.model_validate(evidence_for_factory)
    row = row_factory(
        row_key=f"nba-points-forward:{event_id}:{player_id}:{line}:{direction}",
        event_id=event_id, event_start_time=event_start.isoformat(),
        sport=SPORT, player=player, stat_type=STAT_TYPE,
        line=line, direction=direction, evidence=frozen,
        source_type="NORMALIZED", platform=line_provider,
        opponent=opponent_id, source_capture_timestamp=captured.isoformat(),
    )
    try:
        normalized = validate_evidence(row, STAT_TYPE)
        snapshot_id, fingerprint, snapshot = snapshot_factory(row, normalized)
    except Exception as exc:
        raise NBAPointsSourceError("NBA_POINTS_CANONICAL_EVIDENCE_REJECTED") from exc
    snapshot = dict(snapshot)
    if (str(snapshot.get("event_id")) != event_id
            or str(snapshot.get("stat_type")) != STAT_TYPE
            or str(snapshot.get("player")) != player):
        raise NBAPointsSourceError("NBA_POINTS_SNAPSHOT_IDENTITY_MISMATCH")
    if _number(snapshot.get("line"), "snapshot_line") != line:
        raise NBAPointsSourceError("NBA_POINTS_SNAPSHOT_LINE_MISMATCH")
    snapshot["source_snapshot_id"] = snapshot_id
    if verified_identity is not None:
        if (snapshot.get("role_status") != evidence_for_factory["role_status"]
                or snapshot.get("opportunity_ledger") != evidence_for_factory["opportunity_ledger"]):
            raise NBAPointsSourceError("NBA_POINTS_VERIFIED_SNAPSHOT_PROOF_NOT_FROZEN")
    return {
        "status": "SOURCE_VALIDATED_SNAPSHOT_NOT_PERSISTED",
        "sport": SPORT, "stat_type": STAT_TYPE,
        "source_snapshot_id": snapshot_id,
        "evidence_fingerprint": fingerprint,
        "snapshot": snapshot,
        "official_roster_verified_for_snapshot": verified_identity is not None,
        "source_identity": {
            "canonical_event_id": event_id,
            "canonical_resolution": canonical["identity_resolution"],
            "player_id": player_id, "team_id": team_id,
            "roster_captured_at": roster_time.isoformat(),
            "board_captured_at": line_captured.isoformat(),
            "board_line_provider": line_provider,
            **({
                "nba_game_id": verified_identity["nba_game_id"],
                "nba_person_id": verified_identity["nba_person_id"],
            } if verified_identity is not None else {}),
        },
        "probability_publishable": False,
        "rank_eligible": False,
        "write_performed": False,
        "can_execute": False,
    }


__all__ = ["CAN_EXECUTE", "NBAPointsSourceError", "build_nba_points_snapshot"]
