"""Server-owned NFL team-event identity, fitted inference, and immutable snapshot persistence."""
from __future__ import annotations

import csv
from datetime import date
import gzip
import io
from typing import Any

from nfl_event_model_v17 import (
    NFLModelInputsInsufficient,
    NFLModelOutputInvalid,
    NFLModelScorerFailed,
    NFLModelUnavailable,
    feature_order_hash,
    load_champion_model,
    score_feature_row,
)
from nfl_event_prediction_features import build_prediction_feature_row

SCHEDULE_DATASET = "SCHEDULES"
RAW_BUCKET = "wow-nfl-raw"


def _paginate(query: Any, page_size: int = 500) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        result = query.range(start, start + page_size - 1).execute()
        chunk = result.data if isinstance(result.data, list) else []
        rows.extend(dict(row) for row in chunk)
        if len(chunk) < page_size:
            break
        start += page_size
    return rows


def _raw_object_path(uri: str) -> str:
    prefix = f"supabase://{RAW_BUCKET}/"
    if not str(uri or "").startswith(prefix):
        raise NFLModelInputsInsufficient("NFL_SCHEDULE_RAW_OBJECT_URI_INVALID")
    path = str(uri)[len(prefix):]
    if not path:
        raise NFLModelInputsInsufficient("NFL_SCHEDULE_RAW_OBJECT_PATH_MISSING")
    return path


def _load_latest_schedule_snapshot(db: Any) -> tuple[dict[str, Any], list[dict[str, str]]]:
    result = (
        db.table("wow_nfl_source_snapshots")
        .select("snapshot_id,dataset_name,content_sha256,fetched_at,raw_object_uri,source_status")
        .eq("dataset_name", SCHEDULE_DATASET)
        .eq("source_status", "CAPTURED")
        .order("fetched_at", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if len(rows) != 1:
        raise NFLModelInputsInsufficient("NFL_CANONICAL_SCHEDULE_SNAPSHOT_UNAVAILABLE")
    snapshot = dict(rows[0])
    try:
        raw = db.storage.from_(RAW_BUCKET).download(_raw_object_path(snapshot.get("raw_object_uri")))
        decoded = gzip.decompress(raw).decode("utf-8-sig", errors="replace")
    except Exception as exc:
        raise NFLModelInputsInsufficient("NFL_CANONICAL_SCHEDULE_BYTES_UNAVAILABLE") from exc
    schedule_rows = [dict(row) for row in csv.DictReader(io.StringIO(decoded))]
    if not schedule_rows:
        raise NFLModelInputsInsufficient("NFL_CANONICAL_SCHEDULE_EMPTY")
    return snapshot, schedule_rows


def _has_score(row: dict[str, Any]) -> bool:
    return str(row.get("home_score") or "").strip() != "" or str(row.get("away_score") or "").strip() != ""


def _prior_completed_count(schedule_rows: list[dict[str, str]], *, season: int, team: str, target_date: date) -> int:
    n = 0
    for row in schedule_rows:
        try:
            row_season = int(float(str(row.get("season") or "0")))
            gameday = date.fromisoformat(str(row.get("gameday") or "")[:10])
        except (ValueError, TypeError):
            continue
        if row_season != season or gameday >= target_date or not _has_score(row):
            continue
        if team in {str(row.get("home_team") or "").upper(), str(row.get("away_team") or "").upper()}:
            n += 1
    return n


def resolve_nfl_team_event_evidence(req: Any, *, db: Any) -> dict[str, Any]:
    snapshot, schedule_rows = _load_latest_schedule_snapshot(db)
    event_id = str(req.official_event_id or "").strip()
    exact = [row for row in schedule_rows if str(row.get("game_id") or "").strip() == event_id]
    if len(exact) != 1:
        return {"ok": False, "code": "NFL_OFFICIAL_EVENT_ID_NOT_IN_CANONICAL_SCHEDULE", "failure_code": "MODEL_INPUTS_INSUFFICIENT", "missing_fields": ["official_event_id"] if not event_id else [], "identity_mismatches": []}
    event = exact[0]
    home = str(event.get("home_team") or "").upper().strip()
    away = str(event.get("away_team") or "").upper().strip()
    mismatches: list[str] = []
    if home != str(req.home_team or "").upper().strip():
        mismatches.append("HOME_TEAM_MISMATCH")
    if away != str(req.away_team or "").upper().strip():
        mismatches.append("AWAY_TEAM_MISMATCH")
    if _has_score(event):
        mismatches.append("CANONICAL_EVENT_ALREADY_STARTED_OR_COMPLETED")
    try:
        gameday = date.fromisoformat(str(event.get("gameday") or "")[:10])
        season = int(float(str(event.get("season") or "0")))
        week = int(float(str(event.get("week") or "0")))
    except (ValueError, TypeError):
        return {"ok": False, "code": "NFL_CANONICAL_EVENT_METADATA_INVALID", "failure_code": "MODEL_INPUTS_INSUFFICIENT", "missing_fields": [], "identity_mismatches": mismatches}
    if gameday.isoformat() != str(req.requested_slate_date):
        mismatches.append("REQUESTED_SLATE_DATE_MISMATCH")
    if mismatches:
        return {"ok": False, "code": "NFL_CANONICAL_EVENT_IDENTITY_MISMATCH", "failure_code": "MODEL_INPUTS_INSUFFICIENT", "missing_fields": [], "identity_mismatches": mismatches}
    required_home_history = _prior_completed_count(schedule_rows, season=season, team=home, target_date=gameday)
    required_away_history = _prior_completed_count(schedule_rows, season=season, team=away, target_date=gameday)
    return {
        "ok": True,
        "canonical_source_snapshot_id": str(snapshot["snapshot_id"]),
        "canonical_snapshot_timestamp": str(snapshot["fetched_at"]),
        "schedule_content_sha256": str(snapshot["content_sha256"]),
        "evidence": {
            "season": season,
            "week": week,
            "gameday": gameday.isoformat(),
            "venue": str(event.get("stadium") or "").strip() or "NFL_CANONICAL_STADIUM_UNAVAILABLE",
            "official_event_status": "SCHEDULED",
            "settlement_rule": str(req.settlement_basis),
            "required_home_season_prior_games": required_home_history,
            "required_away_season_prior_games": required_away_history,
            "schedule_content_sha256": str(snapshot["content_sha256"]),
            "canonical_schedule_snapshot_id": str(snapshot["snapshot_id"]),
        },
    }


def _load_history(db: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    games = _paginate(db.table("wow_nfl_training_games").select("*").order("season").order("week").order("gameday").order("game_id"))
    summaries = _paginate(db.table("wow_nfl_game_team_summaries").select("*").order("game_id").order("team"))
    if not games or not summaries:
        raise NFLModelInputsInsufficient("NFL_HISTORICAL_FEATURE_CONTEXT_UNAVAILABLE")
    return games, summaries


def score_nfl_team_event(req: Any, *, db: Any) -> dict[str, Any]:
    evidence = dict(req.sport_specific_evidence or {})
    required = ("season", "week", "gameday", "schedule_content_sha256")
    missing = [name for name in required if evidence.get(name) in (None, "")]
    if missing:
        raise NFLModelInputsInsufficient("NFL_CANONICAL_EVIDENCE_INCOMPLETE:" + ",".join(missing))

    try:
        games, summaries = _load_history(db)
        feature_row = build_prediction_feature_row(
            training_games=games,
            team_summaries=summaries,
            game_id=req.official_event_id,
            season=int(evidence["season"]),
            week=int(evidence["week"]),
            gameday=str(evidence["gameday"]),
            home_team=req.home_team,
            away_team=req.away_team,
            schedule_content_sha256=str(evidence["schedule_content_sha256"]),
        )
    except NFLModelInputsInsufficient:
        raise
    except Exception as exc:
        raise NFLModelScorerFailed("NFL_FEATURE_ASSEMBLY_FAILED") from exc

    home_required = int(evidence.get("required_home_season_prior_games") or 0)
    away_required = int(evidence.get("required_away_season_prior_games") or 0)
    home_seen = int(feature_row["features"]["home_season_prior_games"])
    away_seen = int(feature_row["features"]["away_season_prior_games"])
    if home_seen != home_required or away_seen != away_required:
        raise NFLModelInputsInsufficient(f"NFL_CURRENT_SEASON_HISTORY_STALE:HOME={home_seen}/{home_required}:AWAY={away_seen}/{away_required}")

    try:
        model = load_champion_model(db)
        model_result = score_feature_row(model, feature_row)
    except (NFLModelUnavailable, NFLModelInputsInsufficient, NFLModelOutputInvalid):
        raise
    except Exception as exc:
        raise NFLModelScorerFailed("NFL_FITTED_SCORER_FAILED") from exc

    home_probability = float(model_result["calibrated_home_probability"])
    if home_probability >= 0.5:
        selected, opponent = str(req.home_team), str(req.away_team)
        selection_probability = home_probability
        selection_lower = float(model_result["calibrated_home_lower_bound"])
        selection_upper = float(model_result["calibrated_home_upper_bound"])
    else:
        selected, opponent = str(req.away_team), str(req.home_team)
        selection_probability = float(model_result["calibrated_away_probability"])
        selection_lower = float(model_result["calibrated_away_lower_bound"])
        selection_upper = float(model_result["calibrated_away_upper_bound"])

    payload = {
        "research_run_id": req.research_run_id,
        "event_key": req.event_key,
        "official_event_id": req.official_event_id,
        "requested_slate_date": req.requested_slate_date,
        "requested_timezone": req.requested_timezone,
        "event_start_time_utc": req.event_start_time_utc,
        "home_team": req.home_team,
        "away_team": req.away_team,
        "selected_participant": selected,
        "opponent": opponent,
        "source_snapshot_id": req.source_snapshot_id,
        "source_snapshot_timestamp": req.latest_material_update_timestamp,
        "latest_material_update_timestamp": req.latest_material_update_timestamp,
        "feature_row_hash": feature_row["row_inputs_hash"],
        "feature_schema_version": feature_row["feature_schema_version"],
        "feature_order_hash": feature_order_hash(),
        "model_artifact_id": model_result["model_artifact_id"],
        "model_family": model.model_family,
        "model_version": model_result["model_version"],
        "model_timestamp": model_result["model_timestamp"],
        "raw_home_probability": model_result["raw_home_probability"],
        "raw_away_probability": model_result["raw_away_probability"],
        "calibrated_home_probability": model_result["calibrated_home_probability"],
        "calibrated_away_probability": model_result["calibrated_away_probability"],
        "calibrated_home_lower_bound": model_result["calibrated_home_lower_bound"],
        "calibrated_home_upper_bound": model_result["calibrated_home_upper_bound"],
        "calibrated_away_lower_bound": model_result["calibrated_away_lower_bound"],
        "calibrated_away_upper_bound": model_result["calibrated_away_upper_bound"],
        "calibrated_selection_probability": selection_probability,
        "calibrated_selection_lower_bound": selection_lower,
        "calibrated_selection_upper_bound": selection_upper,
        "calibration_method": model_result["calibration_method"],
        "calibration_version": model_result["calibration_version"],
        "calibration_health_status": "PASS",
        "calibration_training_n": model_result["calibration_training_n"],
        "uncertainty_method": model_result["uncertainty_method"],
        "ranking_basis": "CALIBRATED_LOWER_BOUND",
        "ranked_probability": selection_lower,
        "model_package_valid": True,
        "provenance_complete": True,
        "model_probability_publishable": True,
        "blend_publishable": False,
        "can_execute": False,
        "model_output_snapshot": {**model_result, "feature_row_hash": feature_row["row_inputs_hash"], "feature_max_prior_gameday": feature_row.get("max_prior_gameday"), "selected_participant": selected, "ranked_probability": selection_lower, "ranking_basis": "CALIBRATED_LOWER_BOUND"},
    }
    try:
        inserted = db.table("wow_nfl_event_predictions").insert(payload).execute()
        persisted = inserted.data if isinstance(inserted.data, list) else []
    except Exception as exc:
        raise NFLModelScorerFailed("NFL_IMMUTABLE_PREDICTION_PERSISTENCE_FAILED") from exc
    if len(persisted) != 1:
        raise NFLModelScorerFailed("NFL_IMMUTABLE_PREDICTION_PERSISTENCE_NO_ROW")
    row = dict(persisted[0])

    return {
        "code": "NFL_FITTED_MODEL_PATH_PROVEN",
        **model_result,
        "score_snapshot_id": str(row["score_snapshot_id"]),
        "base_score_snapshot_id": str(row["score_snapshot_id"]),
        "event_prediction_id": str(row["event_prediction_id"]),
        "selected_participant": selected,
        "opponent": opponent,
        "calibrated_selection_probability": selection_probability,
        "ranked_probability": selection_lower,
        "rank_calibrated_lower_bound": selection_lower,
        "ranking_basis": "CALIBRATED_LOWER_BOUND",
        "source_count": len(feature_row.get("source_content_sha256s") or []),
        "data_quality_score": 1.0,
        "provenance_complete": True,
        "exact_line_supported": True,
        "failure_code": None,
        "model_reason": "CERTIFIED_NFL_P2_FITTED_MODEL",
        "blend_publishable": False,
        "probability_publishable": True,
        "can_execute": False,
    }
