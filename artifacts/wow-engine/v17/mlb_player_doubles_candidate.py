"""Research-only fitted challenger for MLB player doubles at the 0.5 line.

The challenger is deliberately narrow: it models the binary event that a hitter
records at least one double in the game. It is trained without market prices and
may score only the exact 0.5 line. Historical calibration is descriptive research
support, not production certification. Publication/ranking/execution remain false.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

SPORT = "MLB"
STAT_TYPE = "PLAYER_DOUBLES"
MODEL_FAMILY = "MLB_PLAYER_DOUBLES_EMPIRICAL_BAYES_V1"
ARTIFACT_FORMAT = "MLB_PLAYER_DOUBLES_EMPIRICAL_BAYES_V1"
ARTIFACT_SCHEMA_VERSION = "MLB_PLAYER_DOUBLES_CANDIDATE_V1"
FEATURE_SCHEMA_VERSION = "PROP_FEATURES_V1"
CONTROLLING_SPECIALIST = "wow.mlb-player-doubles-expert"
SUPPORTED_LINE = 0.5
CAN_EXECUTE = False


class MLBPlayerDoublesCandidateError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _finite_probability(value: Any, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MLBPlayerDoublesCandidateError("MLB_DOUBLES_ARTIFACT_INVALID", f"{field} must be numeric") from exc
    if not math.isfinite(number) or not 0.0 < number < 1.0:
        raise MLBPlayerDoublesCandidateError("MLB_DOUBLES_ARTIFACT_INVALID", f"{field} must be in (0,1)")
    return number


def _normalize_player(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def validate_artifact_payload(payload: Mapping[str, Any]) -> None:
    if payload.get("artifact_schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise MLBPlayerDoublesCandidateError("MLB_DOUBLES_ARTIFACT_SCHEMA_UNSUPPORTED", "artifact schema mismatch")
    if str(payload.get("sport") or "").upper() != SPORT or str(payload.get("stat_type") or "").upper() != STAT_TYPE:
        raise MLBPlayerDoublesCandidateError("MLB_DOUBLES_ARTIFACT_ROUTE_MISMATCH", "artifact route mismatch")
    if str(payload.get("model_family") or "") != MODEL_FAMILY:
        raise MLBPlayerDoublesCandidateError("MLB_DOUBLES_ARTIFACT_FAMILY_MISMATCH", "artifact family mismatch")
    if float(payload.get("supported_line", -1)) != SUPPORTED_LINE:
        raise MLBPlayerDoublesCandidateError("MLB_DOUBLES_ARTIFACT_LINE_POLICY_INVALID", "only line 0.5 is supported")
    _finite_probability(payload.get("league_prior"), field="league_prior")
    players = payload.get("player_probabilities")
    if not isinstance(players, Mapping) or not players:
        raise MLBPlayerDoublesCandidateError("MLB_DOUBLES_ARTIFACT_INVALID", "player_probabilities missing")
    bins = payload.get("calibration_bins")
    if not isinstance(bins, list) or not bins:
        raise MLBPlayerDoublesCandidateError("MLB_DOUBLES_ARTIFACT_INVALID", "calibration_bins missing")


def _raw_probability(payload: Mapping[str, Any], player: str) -> tuple[float, str]:
    players = payload.get("player_probabilities") or {}
    wanted = _normalize_player(player)
    for name, value in players.items():
        if _normalize_player(name) == wanted:
            return _finite_probability(value, field=f"player_probability:{name}"), "PLAYER_HISTORY"
    return _finite_probability(payload.get("league_prior"), field="league_prior"), "LEAGUE_PRIOR_UNSEEN_PLAYER"


def _historical_calibration(payload: Mapping[str, Any], raw_probability: float) -> float:
    bins = payload.get("calibration_bins") or []
    candidates: list[tuple[float, float]] = []
    for row in bins:
        if not isinstance(row, Mapping):
            continue
        try:
            center = float(row.get("raw_mean"))
            calibrated = _finite_probability(row.get("calibrated_probability"), field="calibrated_probability")
        except (TypeError, ValueError, MLBPlayerDoublesCandidateError):
            continue
        if math.isfinite(center):
            candidates.append((abs(center - raw_probability), calibrated))
    if not candidates:
        raise MLBPlayerDoublesCandidateError("MLB_DOUBLES_CALIBRATOR_INVALID", "no valid historical calibration bins")
    candidates.sort(key=lambda pair: pair[0])
    return candidates[0][1]


def score_candidate(
    payload: Mapping[str, Any],
    *,
    player: str,
    line: float,
    direction: str,
) -> dict[str, Any]:
    validate_artifact_payload(payload)
    if abs(float(line) - SUPPORTED_LINE) > 1e-9:
        raise MLBPlayerDoublesCandidateError("MLB_DOUBLES_LINE_OUT_OF_DOMAIN", f"supported line is {SUPPORTED_LINE}")
    side = str(direction or "").strip().upper()
    if side not in {"MORE", "LESS"}:
        raise MLBPlayerDoublesCandidateError("PROP_DIRECTION_INVALID", "direction must be MORE or LESS")
    if not str(player or "").strip():
        raise MLBPlayerDoublesCandidateError("PROP_PLAYER_IDENTITY_UNRESOLVED", "player is required")

    raw_more, baseline_source = _raw_probability(payload, player)
    historically_calibrated_more = _historical_calibration(payload, raw_more)
    raw_side = raw_more if side == "MORE" else 1.0 - raw_more
    historical_side = historically_calibrated_more if side == "MORE" else 1.0 - historically_calibrated_more

    return {
        "candidate_family": "MLB_PLAYER_DOUBLES",
        "candidate_model_family": MODEL_FAMILY,
        "candidate_raw_probability": raw_side,
        "candidate_historical_calibrated_probability": historical_side,
        "modeled_event": "PLAYER_RECORDS_AT_LEAST_ONE_DOUBLE",
        "exact_line": SUPPORTED_LINE,
        "direction": side,
        "baseline_source": baseline_source,
        "historical_calibration_is_production_certification": False,
        "forward_calibration_required": True,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
        "blockers": [
            "MLB_PLAYER_DOUBLES_FORWARD_CALIBRATION_REQUIRED",
            "CALIBRATION_BLOCKED_NO_PUBLISH",
        ],
    }


__all__ = [
    "ARTIFACT_FORMAT",
    "ARTIFACT_SCHEMA_VERSION",
    "CAN_EXECUTE",
    "CONTROLLING_SPECIALIST",
    "FEATURE_SCHEMA_VERSION",
    "MLBPlayerDoublesCandidateError",
    "MODEL_FAMILY",
    "SPORT",
    "STAT_TYPE",
    "SUPPORTED_LINE",
    "score_candidate",
    "validate_artifact_payload",
]
