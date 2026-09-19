"""V17 terminal governance for certified multisport team/event packages.

This module is not a sporting model. It receives an already-computed probability
package, proves the shared V17 gates, and is the only component in the new
multisport path allowed to emit FINAL_APPROVED. Sporting probabilities are
preserved on HOLD; governance never edits the model point estimate or bounds.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any, Mapping

from v17.llp_governed_package_scoring import PASS, validate_governed_scoring_package
from v17.team_event_capability_manifest import CERTIFIED_TEAM_EVENT_SPORTS

CAN_EXECUTE = False
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"
PASS_PROBABILITY_AUDIT = "PASS_PROBABILITY_AUDIT"
FINAL_APPROVED = "FINAL_APPROVED"
MODEL_QUALIFIED_HOLD = "MODEL_QUALIFIED_HOLD"


def _probability(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        return None
    return parsed


def _aware(value: Any) -> datetime | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _binary_sum_pass(package: Mapping[str, Any]) -> bool:
    home = _probability(package.get("calibrated_home_probability"))
    away = _probability(package.get("calibrated_away_probability"))
    return home is not None and away is not None and abs((home + away) - 1.0) <= 0.00001


def _soccer_sum_pass(package: Mapping[str, Any]) -> bool:
    three = package.get("three_state_1x2")
    if not isinstance(three, Mapping):
        return False
    home = _probability(three.get("home"))
    draw = _probability(three.get("draw"))
    away = _probability(three.get("away"))
    return (
        home is not None
        and draw is not None
        and away is not None
        and abs((home + draw + away) - 1.0) <= 0.00001
    )


def _evidence(req: Any) -> dict[str, Any]:
    value = getattr(req, "sport_specific_evidence", None)
    return dict(value) if isinstance(value, Mapping) else {}


def _status_blockers(req: Any, sport: str) -> list[str]:
    evidence = _evidence(req)
    blockers: list[str] = []
    if sport == "WNBA":
        token = str(evidence.get("expected_starters_rotation") or "").upper()
        if token not in {"CONFIRMED", "EXPECTED", "PROJECTED", "PROBABLE"}:
            blockers.append("WNBA_ROTATION_STATUS_NOT_MODEL_READY")
    elif sport == "NHL":
        token = str(evidence.get("goalie_status") or "").upper()
        if token not in {"CONFIRMED", "EXPECTED", "PROJECTED", "PROBABLE"}:
            blockers.append("NHL_GOALIE_STATUS_NOT_MODEL_READY")
    elif sport == "SOCCER":
        token = str(evidence.get("starting_xi_status") or "").upper()
        if token not in {"CONFIRMED", "EXPECTED", "PROJECTED", "PROBABLE"}:
            blockers.append("SOCCER_STARTING_XI_STATUS_NOT_MODEL_READY")
    elif sport == "TENNIS":
        token = str(evidence.get("participant_status") or "").upper()
        if token not in {"ACTIVE", "CONFIRMED", "SCHEDULED"}:
            blockers.append("TENNIS_PARTICIPANT_STATUS_NOT_MODEL_READY")
        if not str(evidence.get("retirement_settlement_rules") or "").strip():
            blockers.append("TENNIS_RETIREMENT_SETTLEMENT_RULES_MISSING")
    elif sport == "MMA":
        participant = str(evidence.get("participant_status") or "").upper()
        weigh_in = str(evidence.get("weigh_in_status") or "").upper()
        if participant not in {"ACTIVE", "CONFIRMED", "SCHEDULED"}:
            blockers.append("MMA_PARTICIPANT_STATUS_NOT_MODEL_READY")
        if weigh_in not in {"CONFIRMED", "PASS", "COMPLETE", "OFFICIAL"}:
            blockers.append("MMA_WEIGH_IN_STATUS_NOT_MODEL_READY")
        if not str(evidence.get("no_contest_draw_outcome_space") or "").strip():
            blockers.append("MMA_NO_CONTEST_DRAW_OUTCOME_SPACE_MISSING")
    return blockers


def reduce_multisport_team_event(
    req: Any,
    package: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply the shared V17 terminal gates to one immutable model package."""
    sport = str(package.get("sport") or getattr(req, "sport", "") or "").upper().strip()
    blockers: list[str] = []

    audit = validate_governed_scoring_package(package)
    if audit.status != PASS:
        blockers.extend(f"GOVERNED_PACKAGE:{item}" for item in audit.blockers)

    certified_specialist = CERTIFIED_TEAM_EVENT_SPORTS.get(sport)
    if certified_specialist is None:
        blockers.append("TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED")
    elif package.get("controlling_specialist") != certified_specialist:
        blockers.append("CONTROLLING_SPECIALIST_CERTIFICATION_MISMATCH")

    if package.get("can_execute") is not False:
        blockers.append("CAN_EXECUTE_MUST_BE_FALSE")
    if package.get("market_probability_used_as_model") is not False:
        blockers.append("MARKET_PROBABILITY_SUBSTITUTION_PROHIBITED")
    if package.get("generic_reasoning_used_as_model") is not False:
        blockers.append("GENERIC_REASONING_SUBSTITUTION_PROHIBITED")

    event_start = _aware(package.get("event_start_time_utc"))
    if event_start is None:
        blockers.append("EVENT_START_TIME_INVALID")
    elif event_start <= datetime.now(timezone.utc):
        blockers.append("EVENT_NOT_PREGAME")

    model_at = _aware(package.get("immutable_model_timestamp"))
    latest_at = _aware(package.get("latest_material_update_timestamp"))
    if package.get("latest_material_update_timestamp") and latest_at is None:
        blockers.append("LATEST_MATERIAL_UPDATE_TIMESTAMP_INVALID")
    if model_at is not None and latest_at is not None and model_at < latest_at:
        blockers.append("MODEL_STALE_AFTER_MATERIAL_UPDATE")

    event_key = str(package.get("event_key") or "").strip()
    official_event_id = str(package.get("official_event_id") or "").strip()
    if not event_key or not official_event_id:
        blockers.append("EVENT_MUTEX_IDENTITY_INCOMPLETE")
    if str(getattr(req, "official_event_id", "") or "").strip() != official_event_id:
        blockers.append("EVENT_MUTEX_OFFICIAL_ID_MISMATCH")

    if sport == "SOCCER":
        if not _soccer_sum_pass(package):
            blockers.append("SOCCER_1X2_PROBABILITY_SUM_INVALID")
    elif sport in {"WNBA", "NHL", "TENNIS", "MMA"}:
        if not _binary_sum_pass(package):
            blockers.append("BINARY_OUTCOME_PROBABILITY_SUM_INVALID")
    else:
        blockers.append("MULTISPORT_TERMINAL_ROUTE_UNSUPPORTED")

    blockers.extend(_status_blockers(req, sport))
    blockers = list(dict.fromkeys(blockers))
    passed = not blockers
    terminal = FINAL_APPROVED if passed else MODEL_QUALIFIED_HOLD

    return {
        "status": "PASS" if passed else "HOLD",
        "sport": sport,
        "probability_audit_result": PASS_PROBABILITY_AUDIT if audit.status == PASS else audit.status,
        "event_mutex_status": "PASS" if not any("EVENT_MUTEX" in b for b in blockers) else "HOLD",
        "postmodel_gates_status": "PASS" if not blockers else "HOLD",
        "final_gates_status": "PASS" if passed else "HOLD",
        "terminal_label": terminal,
        "terminal_ceiling": terminal,
        "blockers": blockers,
        "probability_publishable": passed,
        "rank_eligible": passed,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "FINAL_APPROVED",
    "GLOBAL_TERMINAL_REDUCER",
    "MODEL_QUALIFIED_HOLD",
    "PASS_PROBABILITY_AUDIT",
    "reduce_multisport_team_event",
]
