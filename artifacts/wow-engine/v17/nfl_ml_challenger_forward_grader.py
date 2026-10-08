"""Pure, research-only settlement grade for the NFL ML v2 forward challenger.

No direct persistence, no model fitting, no modification of the sealed pregame
prediction, and no probability promotion or order execution. Class B handoff:
persist update_fields only after governed review authorizes the writer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite, log
import re
from typing import Any, Mapping

CAN_EXECUTE = False
PROMOTION_AUTHORIZED = False
PROBABILITY_PUBLISHABLE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CHALLENGER_ID = "NFL_ML_STATIONARY_DECAY_PLATT_COMPOSITE_LB_V1"
GRADER_VERSION = "NFL_ML_CHALLENGER_SETTLEMENT_GRADE_V1"
_EVENT_RE = re.compile(r"^(\d{4})_(\d{2})_([A-Z]{2,3})_([A-Z]{2,3})$")


class ChallengerGradeError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _str(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ChallengerGradeError(code)
    return value.strip()


def _utc(value: Any, code: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(_str(value, code).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ChallengerGradeError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ChallengerGradeError(code)
    return parsed.astimezone(timezone.utc)


def _strict_number(value: Any, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ChallengerGradeError(code)
    num = float(value)
    if not isfinite(num):
        raise ChallengerGradeError(code)
    return num


def grade_settled_challenger(
    shadow: Mapping[str, Any],
    settlement: Mapping[str, Any],
    *,
    graded_at: str,
) -> dict[str, Any]:
    """Return typed, immutable grade/update preview; write permissions are separate."""
    if not isinstance(shadow, Mapping) or not isinstance(settlement, Mapping):
        raise ChallengerGradeError("NFL_CHALLENGER_GRADE_INPUT_INVALID")
    if shadow.get("challenger_id") != CHALLENGER_ID:
        raise ChallengerGradeError("NFL_CHALLENGER_GRADE_MODEL_IDENTITY_MISMATCH")
    if shadow.get("lifecycle_state") != "FORWARD_SHADOW":
        raise ChallengerGradeError("NFL_CHALLENGER_GRADE_SOURCE_STATE_INVALID")
    if any(shadow.get(f) is not None for f in ("outcome", "hit", "graded_at", "brier", "log_loss")):
        raise ChallengerGradeError("NFL_CHALLENGER_GRADE_SOURCE_ALREADY_MODIFIED")
    if (shadow.get("can_execute") is not False
        or shadow.get("promotion_authorized") is not False
        or shadow.get("probability_publishable") is not False
        or shadow.get("terminal_authority") != TERMINAL_AUTHORITY):
        raise ChallengerGradeError("NFL_CHALLENGER_GRADE_GOVERNANCE_MISMATCH")
    shadow_id = _str(shadow.get("shadow_id"), "NFL_CHALLENGER_GRADE_SHADOW_ID_MISSING")
    event_id = _str(shadow.get("official_event_id"), "NFL_CHALLENGER_GRADE_EVENT_ID_MISSING")
    matched = _EVENT_RE.fullmatch(event_id)
    if matched is None:
        raise ChallengerGradeError("NFL_CHALLENGER_GRADE_EVENT_ID_FORMAT_INVALID")
    year, week, away_abbrev, home_abbrev = matched.groups()
    if event_id != settlement.get("game_id"):
        raise ChallengerGradeError("NFL_CHALLENGER_GRADE_SETTLEMENT_EVENT_MISMATCH")
    if (settlement.get("season") != int(year)
        or settlement.get("week") != int(week)
        or settlement.get("away_team") != away_abbrev
        or settlement.get("home_team") != home_abbrev):
        raise ChallengerGradeError("NFL_CHALLENGER_GRADE_SETTLEMENT_PARTICIPANTS_MISMATCH")

    kickoff = _utc(shadow.get("event_start_time_utc"), "NFL_CHALLENGER_GRADE_KICKOFF_INVALID")
    predicted_at = _utc(shadow.get("prediction_created_at"), "NFL_CHALLENGER_GRADE_PREDICTION_TIME_INVALID")
    settled_at = _utc(settlement.get("locked_at"), "NFL_CHALLENGER_GRADE_SETTLEMENT_TIME_INVALID")
    grade_time = _utc(graded_at, "NFL_CHALLENGER_GRADE_AT_INVALID")
    if predicted_at >= kickoff:
        raise ChallengerGradeError("NFL_CHALLENGER_GRADE_PREGAME_LEAKAGE")
    if settled_at <= kickoff or grade_time < settled_at:
        raise ChallengerGradeError("NFL_CHALLENGER_GRADE_PREMATURE_SETTLEMENT")
    if settlement.get("tie") is True:
        raise ChallengerGradeError("NFL_CHALLENGER_GRADE_NONBINARY_TIE")
    if settlement.get("tie") is not False:
        raise ChallengerGradeError("NFL_CHALLENGER_GRADE_TIE_STATUS_REQUIRED")
    if settlement.get("can_execute") is not False:
        raise ChallengerGradeError("NFL_CHALLENGER_GRADE_SETTLEMENT_EXECUTION_INVALID")
    if not _str(settlement.get("schedule_snapshot_id"),
                "NFL_CHALLENGER_GRADE_SETTLEMENT_SNAPSHOT_REQUIRED"):
        raise ChallengerGradeError("NFL_CHALLENGER_GRADE_SETTLEMENT_SNAPSHOT_REQUIRED")
    _str(settlement.get("row_inputs_hash"), "NFL_CHALLENGER_GRADE_SOURCE_HASH_REQUIRED")
    home_score, away_score = settlement.get("home_score"), settlement.get("away_score")
    if type(home_score) is not int or type(away_score) is not int or home_score < 0 or away_score < 0:
        raise ChallengerGradeError("NFL_CHALLENGER_GRADE_SCORES_INVALID")
    if home_score == away_score or type(settlement.get("home_win")) is not bool:
        raise ChallengerGradeError("NFL_CHALLENGER_GRADE_WINNER_NOT_BINARY")
    if (home_score > away_score) != settlement["home_win"]:
        raise ChallengerGradeError("NFL_CHALLENGER_GRADE_SCORE_WINNER_CONFLICT")
    home = _str(shadow.get("home_team"), "NFL_CHALLENGER_GRADE_HOME_IDENTITY_MISSING")
    away = _str(shadow.get("away_team"), "NFL_CHALLENGER_GRADE_AWAY_IDENTITY_MISSING")
    selected = _str(shadow.get("selected_participant"), "NFL_CHALLENGER_GRADE_SELECTION_MISSING")
    if home == away or selected not in {home, away}:
        raise ChallengerGradeError("NFL_CHALLENGER_GRADE_SELECTED_SIDE_INVALID")
    _str(shadow.get("source_feature_hash"), "NFL_CHALLENGER_GRADE_FEATURE_HASH_REQUIRED")
    p = _strict_number(shadow.get("calibrated_probability"),
                       "NFL_CHALLENGER_GRADE_PROBABILITY_INVALID")
    if not 0 < p < 1:
        raise ChallengerGradeError("NFL_CHALLENGER_GRADE_PROBABILITY_INVALID")
    for field in ("local_wilson_lower", "bootstrap_q10_lower", "composite_lower_bound"):
        lower = _strict_number(shadow.get(field), "NFL_CHALLENGER_GRADE_LOWER_BOUND_INVALID")
        if not 0 <= lower <= p:
            raise ChallengerGradeError("NFL_CHALLENGER_GRADE_LOWER_BOUND_INVALID")
    selected_home = selected == home
    hit = settlement["home_win"] if selected_home else not settlement["home_win"]
    y = int(hit)
    update_fields = {
        "lifecycle_state": "GRADED",
        "outcome": y,
        "hit": bool(hit),
        "brier": (p-y)**2,
        "log_loss": -(log(p) if hit else log(1.0-p)),
        "graded_at": grade_time.isoformat(),
    }
    return {
        "grade_version": GRADER_VERSION,
        "status": "RESEARCH_GRADE_PREPARED_NOT_PERSISTED",
        "shadow_id": shadow_id,
        "official_event_id": event_id,
        "settlement_game_id": settlement["game_id"],
        "settlement_snapshot_id": settlement["schedule_snapshot_id"],
        "settlement_row_inputs_hash": settlement["row_inputs_hash"],
        "update_fields": update_fields,
        "prediction_fields_unchanged": True,
        "probability_publishable": False,
        "promotion_authorized": False,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


__all__ = [
    "CAN_EXECUTE", "CHALLENGER_ID", "GRADER_VERSION", "ChallengerGradeError",
    "grade_settled_challenger",
]
