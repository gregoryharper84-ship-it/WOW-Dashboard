"""Read-only NHL forward-grading join for immutable pregame receipts.

This is an independent audit primitive, NOT a scorer, training signal,
source writer, automatic certification, or retrospective forecast generator.
Callers must retrieve actual immutable forecast and settled source ledger rows
from governed storage. Never synthesize a missing forecast from final scores.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any, Iterable, Mapping

from v17.nhl_event_identity import team_abbreviation
from v17.nhl_provenance import (
    SOURCE_PAYLOAD_HASH_SCOPE, SOURCE_PROVENANCE_VERSION,
    provenance_envelope_hash,
)

CAN_EXECUTE = False
_HEX = re.compile(r"[0-9a-f]{64}\Z", flags=re.ASCII)


class NHLForwardGradeError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _dt(raw: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(raw or "").replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def _hash(value: Any) -> bool:
    return isinstance(value, str) and _HEX.fullmatch(value) is not None


def _p(value: Any) -> float | None:
    if type(value) not in {float, int}:
        return None
    n = float(value)
    return n if math.isfinite(n) and 0 < n < 1 else None


def grade_nhl_pregame_forecast(
    forecast: Mapping[str, Any], settled: Mapping[str, Any]
) -> dict[str, Any]:
    """Grade one exact immutable forecast against an independently settled event.

    A result is only a research grade. Verifying storage immutability and source
    acquisition permissions requires independent database/readback evidence.
    """
    if forecast.get("can_execute") is not False:
        raise NHLForwardGradeError("NHL_FORECAST_EXECUTION_GOVERNANCE_INVALID")
    if not isinstance(settled.get("outcome_json"), Mapping):
        raise NHLForwardGradeError("NHL_SETTLEMENT_PAYLOAD_MISSING")
    outcome = settled["outcome_json"]
    event_id = forecast.get("official_event_id")
    source_id = settled.get("official_event_id")
    if (
        not isinstance(event_id, str)
        or re.fullmatch(r"[0-9]{10}", event_id, flags=re.ASCII) is None
        or event_id != source_id
        or settled.get("sport") != "NHL"
    ):
        raise NHLForwardGradeError("NHL_GRADE_EVENT_IDENTITY_MISMATCH")

    forecast_at = _dt(forecast.get("forecast_created_at_utc"))
    scheduled_at = _dt(settled.get("event_start_time"))
    retrieved_at = _dt(settled.get("source_retrieved_at"))
    if (
        forecast_at is None or scheduled_at is None
        or forecast_at >= scheduled_at
        or retrieved_at is None or retrieved_at < scheduled_at
    ):
        raise NHLForwardGradeError("NHL_GRADE_CHRONOLOGY_INVALID")

    for key in ("immutable_prediction_id", "fitted_model_version", "source_snapshot_id"):
        if not isinstance(forecast.get(key), str) or not forecast[key].strip():
            raise NHLForwardGradeError("NHL_FORECAST_RECEIPT_PROVENANCE_INCOMPLETE")
    for key in ("model_artifact_checksum", "feature_snapshot_hash"):
        if not _hash(forecast.get(key)):
            raise NHLForwardGradeError("NHL_FORECAST_RECEIPT_PROVENANCE_INCOMPLETE")

    prob = _p(forecast.get("calibrated_probability"))
    lower = _p(forecast.get("calibrated_lower_bound"))
    if prob is None or lower is None or lower > prob:
        raise NHLForwardGradeError("NHL_FORECAST_CALIBRATION_INVALID")
    if (
        settled.get("source_provider") != "NHL_PUBLIC_WEB_API"
        or not isinstance(settled.get("source_uri"), str)
        or not settled["source_uri"].startswith("https://api-web.nhle.com/v1/")
        or not _hash(settled.get("source_payload_sha256"))
        or not _hash(outcome.get("source_payload_commitment"))
        or outcome.get("payload_hash_scope") != SOURCE_PAYLOAD_HASH_SCOPE
        or outcome.get("provenance_version") != SOURCE_PROVENANCE_VERSION
        or outcome.get("game_type") != 2
        or outcome.get("game_state") not in {"FINAL", "OFF"}
        or type(outcome.get("neutral_site")) is not bool
    ):
        raise NHLForwardGradeError("NHL_SETTLEMENT_PROVENANCE_INVALID")

    home = team_abbreviation(settled.get("home_participant"))
    away = team_abbreviation(settled.get("away_participant"))
    selected = team_abbreviation(forecast.get("selected_participant"))
    if not home or not away or home == away or selected not in {home, away}:
        raise NHLForwardGradeError("NHL_GRADE_SELECTED_SIDE_UNVERIFIED")
    home_score, away_score = outcome.get("home_score"), outcome.get("away_score")
    if (
        type(home_score) is not int or type(away_score) is not int
        or min(home_score, away_score) < 0 or home_score == away_score
    ):
        raise NHLForwardGradeError("NHL_SETTLEMENT_SCORE_INVALID")
    expected = provenance_envelope_hash(
        official_event_id=event_id,
        season=settled.get("season"),
        event_start_time=settled.get("event_start_time"),
        home_team=home, away_team=away,
        home_score=home_score, away_score=away_score,
        game_type=2, game_state=outcome["game_state"],
        neutral_site=outcome["neutral_site"],
    )
    if settled.get("source_payload_sha256") != expected:
        raise NHLForwardGradeError("NHL_SETTLEMENT_ENVELOPE_HASH_MISMATCH")
    won = (home_score > away_score) if selected == home else (away_score > home_score)
    y = int(won)
    brier = (prob - y) ** 2
    log_loss = -(y * math.log(prob) + (1 - y) * math.log1p(-prob))
    return {
        "status": "NHL_FORWARD_GRADE_RESEARCH_ONLY",
        "immutable_prediction_id": forecast["immutable_prediction_id"],
        "official_event_id": event_id,
        "selected_participant": selected,
        "fitted_model_version": forecast["fitted_model_version"],
        "model_artifact_checksum": forecast["model_artifact_checksum"],
        "source_snapshot_id": forecast["source_snapshot_id"],
        "feature_snapshot_hash": forecast["feature_snapshot_hash"],
        "forecast_created_at_utc": forecast_at.isoformat(),
        "settlement_source_sha256": expected,
        "settlement_state": outcome["game_state"],
        "outcome": y,
        "calibrated_probability": prob,
        "calibrated_lower_bound": lower,
        "brier": brier,
        "log_loss": log_loss,
        "prediction_authority": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def summarize_nhl_forward_grade_cohort(grades: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Exact-model chronological cohort report; never certify a thin sample."""
    rows = list(grades)
    if not rows:
        return {"status": "NHL_NO_GENUINE_FORWARD_GRADES", "n": 0,
                "can_execute": False, "probability_publishable": False}
    model_versions = {row.get("fitted_model_version") for row in rows}
    if len(model_versions) != 1:
        raise NHLForwardGradeError("NHL_GRADE_MODEL_VERSION_MIXED")
    identifiers = [(row.get("official_event_id"), row.get("immutable_prediction_id"))
                   for row in rows]
    if len(set(identifiers)) != len(rows):
        raise NHLForwardGradeError("NHL_GRADE_DUPLICATE_FORECAST")
    if len({x[0] for x in identifiers}) != len(rows):
        raise NHLForwardGradeError("NHL_GRADE_DUPLICATE_EVENT")
    if any(row.get("status") != "NHL_FORWARD_GRADE_RESEARCH_ONLY"
           or row.get("can_execute") is not False for row in rows):
        raise NHLForwardGradeError("NHL_GRADE_RECEIPT_INVALID")
    n = len(rows)
    avg_p = sum(float(row["calibrated_probability"]) for row in rows) / n
    avg_y = sum(int(row["outcome"]) for row in rows) / n
    avg_bound = sum(float(row["calibrated_lower_bound"]) for row in rows) / n
    return {
        "status": "NHL_FORWARD_COHORT_RESEARCH_ONLY",
        "fitted_model_version": next(iter(model_versions)),
        "n": n,
        "brier": sum(float(r["brier"]) for r in rows) / n,
        "log_loss": sum(float(r["log_loss"]) for r in rows) / n,
        "forecast_mean": avg_p,
        "observed_frequency": avg_y,
        "calibration_bias": avg_p - avg_y,
        "lower_bound_mean": avg_bound,
        "lower_bound_observed_minus_forecast": avg_y - avg_bound,
        "certification_eligible": False,
        "sample_warning": "INSUFFICIENT_FORWARD_COHORT" if n < 100 else "INDEPENDENT_CERTIFICATION_REQUIRED",
        "can_execute": False,
        "probability_publishable": False,
    }
