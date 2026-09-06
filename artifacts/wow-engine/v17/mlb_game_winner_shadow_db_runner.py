"""Database-backed V17 MLB Game Winner shadow evaluation runner.

Research/shadow only. This module reads immutable sporting evidence, constructs
exact home-relative features, fits the challenger chronologically, and compares
it with the incumbent on pristine forward grades. It cannot mutate Game Winner
admission, NO_PICK policy, cash/value gates, serving state, or the terminal
reducer. No market/payout field is selected or used.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, time, timezone
import json
import logging
from typing import Any, Iterable, Mapping, Sequence

from v17.mlb_game_winner_shadow_challenger import (
    MARKET_PRIOR_WEIGHT,
    fit_shadow_challenger,
    predict_shadow,
)
from v17.mlb_game_winner_shadow_evaluation import (
    AUTOMATIC_PROMOTION,
    CAN_EXECUTE,
    RETROSPECTIVE_PROVENANCE,
    SERVING_MODE,
    TIMESTAMPED_PREGAME_PROVENANCE,
    EvidenceRow,
    EvaluationEvidenceError,
    chronological_split,
    evaluate_forward_shadow,
    evaluate_retrospective_challenger,
    materialize_forward_run_pair,
    materialize_historical_run_pair,
)

HISTORICAL_FEATURE_TABLE = "wow_mlb_v2a_run_features_2024"
HISTORICAL_OUTCOME_TABLE = "wow_mlb_team_games_2024"
FORWARD_EVENT_TABLE = "wow_mlb_forward_shadow_events"
FORWARD_FEATURE_TABLE = "wow_mlb_forward_feature_snapshots"
FORWARD_SCORE_TABLE = "wow_mlb_forward_score_snapshots"
FORWARD_GRADE_TABLE = "wow_mlb_forward_shadow_grades"
REPORT_SCHEMA_VERSION = "WOW_MLB_GAME_WINNER_SHADOW_EVALUATION_V1"

_LOGGER = logging.getLogger("wow.v17.mlb_game_winner_shadow")


def _iso_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _historical_event_time(value: Any) -> datetime:
    if isinstance(value, date) and not isinstance(value, datetime):
        day = value
    else:
        day = date.fromisoformat(str(value)[:10])
    # Historical source contract contains exact game date, not pitch timestamp.
    # Noon UTC is deterministic partition metadata only; retrospective provenance
    # never treats it as an exact pregame evidence timestamp.
    return datetime.combine(day, time(hour=12), tzinfo=timezone.utc)


def _paginate(
    db: Any,
    table: str,
    columns: str,
    *,
    page_size: int = 1000,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        result = db.table(table).select(columns).range(start, start + page_size - 1).execute()
        batch = list(result.data or [])
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def _require_shadow_flags(row: Mapping[str, Any], *, source: str) -> None:
    if row.get("can_execute") is not False:
        raise EvaluationEvidenceError(f"GOVERNANCE_EXECUTION_FLAG_INVALID: source={source}")
    if "research_only" in row and row.get("research_only") is not True:
        raise EvaluationEvidenceError(f"GOVERNANCE_RESEARCH_FLAG_INVALID: source={source}")


def _extract_historical_outcomes(outcome_rows: Sequence[Mapping[str, Any]]) -> dict[str, bool]:
    """Return one explicit home outcome per game, preserving SQL NULL semantics.

    The source is a team-row table: exactly one team row per game carries the
    game-level ``home_win`` value and the paired team row stores NULL. NULL is
    absence of the game-level label, not False. Multiple non-null labels remain
    a hard evidence conflict even if they happen to agree.
    """
    outcomes: dict[str, bool] = {}
    for row in outcome_rows:
        _require_shadow_flags(row, source=HISTORICAL_OUTCOME_TABLE)
        raw_value = row.get("home_win")
        if raw_value is None:
            continue
        key = str(row["game_key"])
        value = bool(raw_value)
        if key in outcomes:
            if outcomes[key] != value:
                raise EvaluationEvidenceError(f"MODEL_INPUTS_CONFLICT: historical_home_win={key}")
            raise EvaluationEvidenceError(f"MODEL_INPUTS_CONFLICT: duplicate_historical_home_win={key}")
        outcomes[key] = value
    return outcomes


def load_retrospective_rows(db: Any) -> list[EvidenceRow]:
    feature_rows = _paginate(
        db,
        HISTORICAL_FEATURE_TABLE,
        "game_key,game_date,is_home,feature_names,feature_vector,research_only,can_execute",
    )
    outcome_rows = _paginate(
        db,
        HISTORICAL_OUTCOME_TABLE,
        "game_key,game_date,home_win,research_only,can_execute",
    )

    outcomes = _extract_historical_outcomes(outcome_rows)

    paired: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in feature_rows:
        _require_shadow_flags(row, source=HISTORICAL_FEATURE_TABLE)
        key = str(row["game_key"])
        side = "HOME" if bool(row["is_home"]) else "AWAY"
        slot = paired.setdefault(key, {})
        if side in slot:
            raise EvaluationEvidenceError(f"MODEL_INPUTS_CONFLICT: duplicate_historical_side={key}:{side}")
        slot[side] = row

    evidence: list[EvidenceRow] = []
    for key, sides in paired.items():
        if set(sides) != {"HOME", "AWAY"}:
            raise EvaluationEvidenceError(f"MODEL_INPUTS_INSUFFICIENT: historical_pair={key}")
        if key not in outcomes:
            raise EvaluationEvidenceError(f"MODEL_INPUTS_INSUFFICIENT: historical_outcome={key}")
        home, away = sides["HOME"], sides["AWAY"]
        if str(home["game_date"])[:10] != str(away["game_date"])[:10]:
            raise EvaluationEvidenceError(f"MODEL_INPUTS_CONFLICT: historical_date={key}")
        feature_row = materialize_historical_run_pair(
            home["feature_names"], home["feature_vector"],
            away["feature_names"], away["feature_vector"],
        )
        evidence.append(
            EvidenceRow(
                event_id=key,
                event_start_time=_historical_event_time(home["game_date"]),
                feature_row=feature_row,
                home_win=outcomes[key],
                provenance_status=RETROSPECTIVE_PROVENANCE,
            )
        )
    return sorted(evidence, key=lambda row: (row.event_start_time, row.event_id))


def _rows_by(rows: Iterable[Mapping[str, Any]], key: str) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        value = str(row[key])
        if value in out:
            raise EvaluationEvidenceError(f"MODEL_INPUTS_CONFLICT: duplicate_{key}={value}")
        out[value] = row
    return out


def load_forward_rows(db: Any) -> list[EvidenceRow]:
    grades = _paginate(
        db,
        FORWARD_GRADE_TABLE,
        "grade_id,shadow_event_id,score_snapshot_id,prediction_timestamp,outcome_timestamp,can_execute",
    )
    scores = _rows_by(
        _paginate(
            db,
            FORWARD_SCORE_TABLE,
            "score_snapshot_id,created_at,shadow_event_id,home_feature_snapshot_id,away_feature_snapshot_id,model_timestamp,calibrated_home_probability,research_only,can_execute",
        ),
        "score_snapshot_id",
    )
    features = _rows_by(
        _paginate(
            db,
            FORWARD_FEATURE_TABLE,
            "feature_snapshot_id,created_at,shadow_event_id,side,feature_names,feature_vector,research_only,can_execute",
        ),
        "feature_snapshot_id",
    )
    events = _rows_by(
        _paginate(
            db,
            FORWARD_EVENT_TABLE,
            "shadow_event_id,event_start_time,home_win,research_only,can_execute",
        ),
        "shadow_event_id",
    )

    evidence: list[EvidenceRow] = []
    seen_events: set[str] = set()
    for grade in grades:
        _require_shadow_flags(grade, source=FORWARD_GRADE_TABLE)
        event_id = str(grade["shadow_event_id"])
        if event_id in seen_events:
            raise EvaluationEvidenceError(f"MODEL_INPUTS_CONFLICT: duplicate_forward_event={event_id}")
        seen_events.add(event_id)

        score_id = str(grade["score_snapshot_id"])
        score = scores.get(score_id)
        event = events.get(event_id)
        if score is None or event is None:
            raise EvaluationEvidenceError(f"MODEL_INPUTS_INSUFFICIENT: forward_ancestry={event_id}")
        _require_shadow_flags(score, source=FORWARD_SCORE_TABLE)
        _require_shadow_flags(event, source=FORWARD_EVENT_TABLE)
        if str(score["shadow_event_id"]) != event_id:
            raise EvaluationEvidenceError(f"MODEL_INPUTS_CONFLICT: score_event={event_id}")

        home_feature = features.get(str(score["home_feature_snapshot_id"]))
        away_feature = features.get(str(score["away_feature_snapshot_id"]))
        if home_feature is None or away_feature is None:
            raise EvaluationEvidenceError(f"MODEL_INPUTS_INSUFFICIENT: forward_feature_pair={event_id}")
        for row in (home_feature, away_feature):
            _require_shadow_flags(row, source=FORWARD_FEATURE_TABLE)
            if str(row["shadow_event_id"]) != event_id:
                raise EvaluationEvidenceError(f"MODEL_INPUTS_CONFLICT: feature_event={event_id}")
        if str(home_feature["side"]).upper() != "HOME" or str(away_feature["side"]).upper() != "AWAY":
            raise EvaluationEvidenceError(f"MODEL_INPUTS_CONFLICT: forward_side={event_id}")
        if tuple(home_feature["feature_names"]) != tuple(away_feature["feature_names"]):
            raise EvaluationEvidenceError(f"MODEL_INPUTS_CONFLICT: forward_schema_pair={event_id}")

        start = _iso_datetime(event["event_start_time"])
        home_ts = _iso_datetime(home_feature["created_at"])
        away_ts = _iso_datetime(away_feature["created_at"])
        score_ts = _iso_datetime(score["created_at"])
        model_ts = _iso_datetime(score["model_timestamp"])
        prediction_ts = _iso_datetime(grade["prediction_timestamp"])
        outcome_ts = _iso_datetime(grade["outcome_timestamp"])
        if max(home_ts, away_ts, score_ts, model_ts, prediction_ts) >= start:
            raise EvaluationEvidenceError(f"GOVERNANCE_POSTGAME_LEAKAGE: forward_timestamp={event_id}")
        if outcome_ts < start:
            raise EvaluationEvidenceError(f"TEMPORAL_PROVENANCE_INVALID: forward_outcome={event_id}")
        if event.get("home_win") is None:
            raise EvaluationEvidenceError(f"MODEL_INPUTS_INSUFFICIENT: forward_home_win={event_id}")

        feature_row = materialize_forward_run_pair(
            home_feature["feature_names"], home_feature["feature_vector"],
            away_feature["feature_names"], away_feature["feature_vector"],
        )
        evidence.append(
            EvidenceRow(
                event_id=event_id,
                event_start_time=start,
                feature_row=feature_row,
                home_win=bool(event["home_win"]),
                provenance_status=TIMESTAMPED_PREGAME_PROVENANCE,
                feature_timestamp=max(home_ts, away_ts),
                outcome_timestamp=outcome_ts,
                champion_home_probability=float(score["calibrated_home_probability"]),
            )
        )
    return sorted(evidence, key=lambda row: (row.event_start_time, row.event_id))


def _automatic_boundaries(rows: Sequence[EvidenceRow]) -> tuple[datetime, datetime]:
    if len(rows) < 3:
        raise EvaluationEvidenceError("INSUFFICIENT_OOS_EVIDENCE: retrospective<3")
    ordered = sorted(rows, key=lambda row: (row.event_start_time, row.event_id))
    train_index = max(0, min(len(ordered) - 3, int(len(ordered) * 0.60) - 1))
    calibration_index = max(train_index + 1, min(len(ordered) - 2, int(len(ordered) * 0.80) - 1))
    train_end = ordered[train_index].event_start_time
    calibration_end = ordered[calibration_index].event_start_time
    if not train_end < calibration_end:
        # Advance to the first later game date so games on one date never cross folds.
        later = next((r.event_start_time for r in ordered if r.event_start_time > train_end), None)
        if later is None:
            raise EvaluationEvidenceError("INSUFFICIENT_OOS_EVIDENCE: no_calibration_boundary")
        calibration_end = later
    return train_end, calibration_end


def run_shadow_evaluation(
    db: Any,
    *,
    bootstrap_models: int = 24,
    min_forward: int = 100,
) -> dict[str, Any]:
    retrospective = load_retrospective_rows(db)
    forward = load_forward_rows(db)
    train_end, calibration_end = _automatic_boundaries(retrospective)
    split = chronological_split(
        retrospective,
        train_end=train_end,
        calibration_end=calibration_end,
    )

    retrospective_report = evaluate_retrospective_challenger(
        split,
        min_train=1000,
        min_calibration=250,
        min_holdout=250,
        bootstrap_models=bootstrap_models,
    )

    # Fit the exact same chronological train/calibration artifact used by the
    # retrospective contract, then score untouched timestamped forward rows.
    artifact = fit_shadow_challenger(
        [row.feature_row for row in split.train],
        [row.home_win for row in split.train],
        [row.feature_row for row in split.calibration],
        [row.home_win for row in split.calibration],
        feature_names=tuple(split.train[0].feature_row.keys()),
        bootstrap_models=bootstrap_models,
        seed=1706,
    )
    predictions = predict_shadow(artifact, [row.feature_row for row in forward])
    forward_report = evaluate_forward_shadow(
        forward,
        [prediction.home_probability_calibrated for prediction in predictions],
        min_forward=min_forward,
    )

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sport": "MLB",
        "lane": "GAME_WINNER",
        "retrospective_n": len(retrospective),
        "forward_n": len(forward),
        "train_end": train_end.isoformat(),
        "calibration_end": calibration_end.isoformat(),
        "retrospective": _json_safe(retrospective_report),
        "forward": _json_safe(forward_report),
        "serving_mode": SERVING_MODE,
        "automatic_promotion": AUTOMATIC_PROMOTION,
        "admission_policy_mutated": False,
        "cash_single_gate_mutated": False,
        "market_prior_weight": MARKET_PRIOR_WEIGHT,
        "probability_publishable": False,
        "can_execute": CAN_EXECUTE,
    }


def _json_safe(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: _json_safe(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
    return value


def log_shadow_evaluation(db: Any, logger: logging.Logger | None = None) -> dict[str, Any]:
    report = run_shadow_evaluation(db)
    target = logger or _LOGGER
    target.warning("WOW_MLB_GAME_WINNER_SHADOW_EVALUATION %s", json.dumps(report, sort_keys=True))
    return report


__all__ = [
    "FORWARD_EVENT_TABLE",
    "FORWARD_FEATURE_TABLE",
    "FORWARD_GRADE_TABLE",
    "FORWARD_SCORE_TABLE",
    "HISTORICAL_FEATURE_TABLE",
    "HISTORICAL_OUTCOME_TABLE",
    "REPORT_SCHEMA_VERSION",
    "_extract_historical_outcomes",
    "load_forward_rows",
    "load_retrospective_rows",
    "log_shadow_evaluation",
    "run_shadow_evaluation",
]
