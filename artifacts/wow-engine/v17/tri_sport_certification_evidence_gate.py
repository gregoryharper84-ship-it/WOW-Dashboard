"""V17 three-priority-sport certification evidence preflight, dry-run only.

This module never fits, calibrates, certifies, promotes, publishes, ranks or
wagers. It verifies whether external *real* scorer and terminal receipts are
present before handing a candidate to the independent certification authority.

The source of truth remains each sport's governing fitted specialist and
V17_TERMINAL_REDUCER. Evidence absence is never converted to a probability.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping

CAN_EXECUTE = False
MODEL_PROBABILITY_AUTHORITY = False
CERTIFICATION_AUTHORITY = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"

TARGET_SPECIALISTS = {
    ("NCAAF", "RECEIVING_YARDS"): "wow.ncaaf-player-prop-probability-expert",
    ("NFL", "PASSING_YARDS"): "wow.nfl-player-prop-probability-expert",
    ("NBA", "POINTS"): "wow.nba-player-prop-probability-expert",
}
SCHEMA = "WOW_V17_TRI_SPORT_DEMONSTRATION_EVIDENCE_V1"
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_VALID_DIRECTIONS = {"MORE", "LESS"}


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.utcoffset() is not None else None


def _sha(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256.fullmatch(value))


def _number(value: Any) -> float | None:
    if type(value) not in (float, int):
        return None
    v = float(value)
    return v if math.isfinite(v) else None


def _prob(value: Any) -> bool:
    n = _number(value)
    return n is not None and 0.0 <= n <= 1.0


def assess_tri_sport_evidence(package: Mapping[str, Any]) -> dict[str, Any]:
    """Typed immutable evidence checklist. Never returns CERTIFIED or picks."""
    if not isinstance(package, Mapping):
        return {
            "schema": SCHEMA, "status": "INVALID_REQUEST",
            "blockers": ["TRI_SPORT_EVIDENCE_NOT_MAPPING"],
            "probability_publishable": False, "can_execute": False,
        }
    sport = str(package.get("sport") or "").upper()
    stat = str(package.get("stat_type") or "").upper()
    key = sport, stat
    if key not in TARGET_SPECIALISTS:
        return {
            "schema": SCHEMA, "status": "UNSUPPORTED_DEMONSTRATION_ROUTE",
            "sport": sport, "stat_type": stat,
            "blockers": ["TRI_SPORT_EXACT_ROUTE_UNSUPPORTED"],
            "probability_publishable": False, "can_execute": False,
        }

    failures: list[str] = []
    def require(ok: bool, code: str) -> None:
        if not ok:
            failures.append(code)

    source = package.get("historical_outcomes")
    source = source if isinstance(source, Mapping) else {}
    require(source.get("source_rights_verified") is True,
            "TRI_SPORT_SOURCE_RIGHTS_UNVERIFIED")
    require(source.get("official_event_player_stat_reconciled") is True,
            "TRI_SPORT_OUTCOME_IDENTITY_UNVERIFIED")
    require(_sha(source.get("dataset_sha256")),
            "TRI_SPORT_DATASET_HASH_MISSING")
    require(type(source.get("outcome_count")) is int and
            source["outcome_count"] >= 100,
            "TRI_SPORT_HISTORICAL_OUTCOME_COHORT_INSUFFICIENT")

    event = package.get("event")
    event = event if isinstance(event, Mapping) else {}
    kickoff = _time(event.get("event_start_utc"))
    require(bool(event.get("official_event_id")) and
            bool(event.get("official_player_id")) and kickoff is not None,
            "TRI_SPORT_EVENT_IDENTITY_OR_START_MISSING")

    evidence = package.get("pregame_features")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    observed = _time(evidence.get("source_first_observed_at"))
    feature_as_of = _time(evidence.get("feature_as_of"))
    require(_sha(evidence.get("snapshot_sha256")) and
            bool(evidence.get("immutable_source_snapshot_id")),
            "TRI_SPORT_PREGAME_SNAPSHOT_UNFROZEN")
    require(
        kickoff is not None and observed is not None and feature_as_of is not None
        and observed <= feature_as_of < kickoff,
        "TRI_SPORT_PREGAME_POINT_IN_TIME_UNVERIFIED",
    )
    require(evidence.get("player_level_verified") is True,
            "TRI_SPORT_PREGAME_PLAYER_FEATURES_UNVERIFIED")
    require(evidence.get("uses_target_event_outcome") is False,
            "TRI_SPORT_TARGET_LEAKAGE_NOT_EXCLUDED")

    artifact = package.get("fitted_model")
    artifact = artifact if isinstance(artifact, Mapping) else {}
    require(artifact.get("controlling_specialist") == TARGET_SPECIALISTS[key],
            "TRI_SPORT_CONTROLLING_SPECIALIST_INCORRECT")
    require(_sha(artifact.get("artifact_checksum")) and
            _sha(artifact.get("training_dataset_hash")) and
            _sha(artifact.get("training_code_sha")) and
            bool(artifact.get("model_artifact_version")),
            "TRI_SPORT_FITTED_ARTIFACT_PROVENANCE_MISSING")
    require(artifact.get("independent_validation_passed") is True and
            type(artifact.get("untouched_holdout_rows")) is int and
            artifact["untouched_holdout_rows"] >= 75 and
            artifact.get("chronological_split_verified") is True,
            "TRI_SPORT_UNTOUCHED_HOLDOUT_UNVERIFIED")
    require(artifact.get("model_family_fitted") is True,
            "TRI_SPORT_FITTED_MODEL_NOT_VERIFIED")

    cal = package.get("calibration")
    cal = cal if isinstance(cal, Mapping) else {}
    cp, lower, upper = (cal.get("calibrated_probability"),
                        cal.get("calibrated_lower_bound"),
                        cal.get("calibrated_upper_bound"))
    require(
        cal.get("independently_calibrated_exact_line") is True
        and bool(cal.get("calibration_artifact_id"))
        and _sha(cal.get("calibration_artifact_checksum"))
        and all(_prob(v) for v in (cp, lower, upper))
        and float(lower) <= float(cp) <= float(upper)
        if all(_prob(v) for v in (cp, lower, upper)) else False,
        "TRI_SPORT_EXACT_LINE_CALIBRATION_OR_BOUNDS_INVALID",
    )
    line = _number(package.get("exact_line"))
    require(line is not None and line >= 0.0 and
            package.get("direction") in _VALID_DIRECTIONS,
            "TRI_SPORT_EXACT_LINE_DIRECTION_INVALID")

    scorer = package.get("scorer_receipt")
    scorer = scorer if isinstance(scorer, Mapping) else {}
    require(
        scorer.get("status") == "SUCCEEDED"
        and bool(scorer.get("prediction_id"))
        and scorer.get("specialist") == TARGET_SPECIALISTS[key]
        and scorer.get("official_event_id") == event.get("official_event_id")
        and scorer.get("official_player_id") == event.get("official_player_id")
        and scorer.get("stat_type") == stat
        and scorer.get("direction") == package.get("direction")
        and _number(scorer.get("exact_line")) == line
        and _prob(scorer.get("model_probability"))
        and scorer.get("calibration_artifact_id") == cal.get("calibration_artifact_id")
        and scorer.get("can_execute") is False,
        "TRI_SPORT_GENUINE_SCORER_RECEIPT_MISSING",
    )
    terminal = package.get("terminal_receipt")
    terminal = terminal if isinstance(terminal, Mapping) else {}
    require(
        terminal.get("publisher") == TERMINAL_AUTHORITY
        and terminal.get("prediction_id") == scorer.get("prediction_id")
        and terminal.get("decision") in ("FINAL_APPROVED", "HELD")
        and terminal.get("can_execute") is False
        and bool(terminal.get("receipt_id")),
        "TRI_SPORT_TERMINAL_RECEIPT_MISSING",
    )
    # No inference is performed here. Even fully complete evidence still
    # requires independent route-specific certification; this verifier
    # cannot self-publish or create a model probability.
    return {
        "schema": SCHEMA,
        "status": (
            "EVIDENCE_COMPLETE_REQUIRES_INDEPENDENT_CERTIFICATION"
            if not failures else "BLOCKED_WITH_EXACT_REASONS"
        ),
        "sport": sport,
        "stat_type": stat,
        "controlling_specialist": TARGET_SPECIALISTS[key],
        "blockers": failures,
        "evidence_check_count": 12,
        "ready_for_independent_certification": not failures,
        "model_certified_by_this_gate": False,
        "probability_publishable": False,
        "can_execute": False,
    }
