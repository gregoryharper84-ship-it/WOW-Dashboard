"""Server-owned NFL team-event identity, fitted inference, and immutable snapshot persistence."""
from __future__ import annotations

import csv
from datetime import date, datetime
import gzip
import io
from typing import Any
from zoneinfo import ZoneInfo

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

# Identity aliases only. These never contribute to model features or probability math.
NFL_TEAM_NAME_TO_ABBREVIATION = {
    "arizona cardinals": "ARI",
    "atlanta falcons": "ATL",
    "baltimore ravens": "BAL",
    "buffalo bills": "BUF",
    "carolina panthers": "CAR",
    "chicago bears": "CHI",
    "cincinnati bengals": "CIN",
    "cleveland browns": "CLE",
    "dallas cowboys": "DAL",
    "denver broncos": "DEN",
    "detroit lions": "DET",
    "green bay packers": "GB",
    "houston texans": "HOU",
    "indianapolis colts": "IND",
    "jacksonville jaguars": "JAX",
    "kansas city chiefs": "KC",
    "las vegas raiders": "LV",
    "los angeles chargers": "LAC",
    "los angeles rams": "LA",
    "miami dolphins": "MIA",
    "minnesota vikings": "MIN",
    "new england patriots": "NE",
    "new orleans saints": "NO",
    "new york giants": "NYG",
    "new york jets": "NYJ",
    "philadelphia eagles": "PHI",
    "pittsburgh steelers": "PIT",
    "san francisco 49ers": "SF",
    "seattle seahawks": "SEA",
    "tampa bay buccaneers": "TB",
    "tennessee titans": "TEN",
    "washington commanders": "WAS",
}
NFL_CANONICAL_TEAM_CODES = frozenset(NFL_TEAM_NAME_TO_ABBREVIATION.values())


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


def _canonical_team(value: Any) -> str | None:
    raw = " ".join(str(value or "").strip().split())
    if not raw:
        return None
    upper = raw.upper()
    if upper in NFL_CANONICAL_TEAM_CODES:
        return upper
    return NFL_TEAM_NAME_TO_ABBREVIATION.get(raw.casefold())


def _aware_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed


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
    provider_event_id = str(req.official_event_id or "").strip()
    requested_home = _canonical_team(req.home_team)
    requested_away = _canonical_team(req.away_team)
    missing_fields: list[str] = []
    if not provider_event_id:
        missing_fields.append("official_event_id")
    if requested_home is None:
        missing_fields.append("home_team")
    if requested_away is None:
        missing_fields.append("away_team")
    if missing_fields:
        return {
            "ok": False,
            "code": "NFL_PROVIDER_EVENT_IDENTITY_INCOMPLETE",
            "failure_code": "MODEL_INPUTS_INSUFFICIENT",
            "missing_fields": missing_fields,
            "identity_mismatches": [],
        }

    try:
        requested_date = date.fromisoformat(str(req.requested_slate_date))
    except (TypeError, ValueError):
        return {
            "ok": False,
            "code": "NFL_REQUESTED_SLATE_DATE_INVALID",
            "failure_code": "MODEL_INPUTS_INSUFFICIENT",
            "missing_fields": ["requested_slate_date"],
            "identity_mismatches": [],
        }

    event_start = _aware_datetime(req.event_start_time_utc)
    if event_start is None:
        return {
            "ok": False,
            "code": "NFL_EVENT_START_TIME_INVALID",
            "failure_code": "MODEL_INPUTS_INSUFFICIENT",
            "missing_fields": ["event_start_time_utc"],
            "identity_mismatches": [],
        }
    try:
        local_start_date = event_start.astimezone(ZoneInfo(str(req.requested_timezone))).date()
    except Exception:
        return {
            "ok": False,
            "code": "NFL_REQUESTED_TIMEZONE_INVALID",
            "failure_code": "MODEL_INPUTS_INSUFFICIENT",
            "missing_fields": ["requested_timezone"],
            "identity_mismatches": [],
        }
    if local_start_date != requested_date:
        return {
            "ok": False,
            "code": "NFL_EVENT_START_SLATE_DATE_MISMATCH",
            "failure_code": "MODEL_INPUTS_INSUFFICIENT",
            "missing_fields": [],
            "identity_mismatches": ["EVENT_START_SLATE_DATE_MISMATCH"],
        }

    exact = [row for row in schedule_rows if str(row.get("game_id") or "").strip() == provider_event_id]
    identity_resolution = "EXACT_CANONICAL_EVENT_ID"
    if len(exact) > 1:
        return {
            "ok": False,
            "code": "NFL_CANONICAL_EVENT_ID_AMBIGUOUS",
            "failure_code": "MODEL_INPUTS_INSUFFICIENT",
            "missing_fields": [],
            "identity_mismatches": ["DUPLICATE_CANONICAL_EVENT_ID"],
        }
    if len(exact) == 1:
        event = exact[0]
    else:
        candidates: list[dict[str, str]] = []
        for row in schedule_rows:
            try:
                row_date = date.fromisoformat(str(row.get("gameday") or "")[:10])
            except (TypeError, ValueError):
                continue
            row_home = str(row.get("home_team") or "").upper().strip()
            row_away = str(row.get("away_team") or "").upper().strip()
            if row_date == requested_date and row_home == requested_home and row_away == requested_away:
                candidates.append(row)
        if not candidates:
            return {
                "ok": False,
                "code": "NFL_PROVIDER_EVENT_ID_CANONICAL_MATCH_NOT_FOUND",
                "failure_code": "MODEL_INPUTS_INSUFFICIENT",
                "missing_fields": [],
                "identity_mismatches": ["PROVIDER_EVENT_ID_NOT_CANONICAL"],
            }
        if len(candidates) != 1:
            return {
                "ok": False,
                "code": "NFL_PROVIDER_EVENT_ID_CANONICAL_MATCH_AMBIGUOUS",
                "failure_code": "MODEL_INPUTS_INSUFFICIENT",
                "missing_fields": [],
                "identity_mismatches": ["PROVIDER_EVENT_ID_CANONICAL_MATCH_AMBIGUOUS"],
            }
        event = candidates[0]
        identity_resolution = "PROVIDER_ID_TO_CANONICAL_SCHEDULE_MATCH"

    canonical_event_id = str(event.get("game_id") or "").strip()
    home = str(event.get("home_team") or "").upper().strip()
    away = str(event.get("away_team") or "").upper().strip()
    mismatches: list[str] = []
    if home != requested_home:
        mismatches.append("HOME_TEAM_MISMATCH")
    if away != requested_away:
        mismatches.append("AWAY_TEAM_MISMATCH")
    if _has_score(event):
        mismatches.append("CANONICAL_EVENT_ALREADY_STARTED_OR_COMPLETED")
    try:
        gameday = date.fromisoformat(str(event.get("gameday") or "")[:10])
        season = int(float(str(event.get("season") or "0")))
        week = int(float(str(event.get("week") or "0")))
    except (ValueError, TypeError):
        return {
            "ok": False,
            "code": "NFL_CANONICAL_EVENT_METADATA_INVALID",
            "failure_code": "MODEL_INPUTS_INSUFFICIENT",
            "missing_fields": [],
            "identity_mismatches": mismatches,
        }
    if gameday != requested_date:
        mismatches.append("REQUESTED_SLATE_DATE_MISMATCH")
    if not canonical_event_id:
        mismatches.append("CANONICAL_EVENT_ID_MISSING")
    if mismatches:
        return {
            "ok": False,
            "code": "NFL_CANONICAL_EVENT_IDENTITY_MISMATCH",
            "failure_code": "MODEL_INPUTS_INSUFFICIENT",
            "missing_fields": [],
            "identity_mismatches": mismatches,
        }

    required_home_history = _prior_completed_count(schedule_rows, season=season, team=home, target_date=gameday)
    required_away_history = _prior_completed_count(schedule_rows, season=season, team=away, target_date=gameday)
    return {
        "ok": True,
        "canonical_event_id": canonical_event_id,
        "provider_event_id": provider_event_id,
        "canonical_home_team": home,
        "canonical_away_team": away,
        "identity_resolution": identity_resolution,
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
            "canonical_game_id": canonical_event_id,
            "provider_event_id": provider_event_id,
            "canonical_home_team": home,
            "canonical_away_team": away,
            "identity_resolution": identity_resolution,
        },
    }


def _load_history(db: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    games = _paginate(db.table("wow_nfl_training_games").select("*").order("season").order("week").order("gameday").order("game_id"))
    summaries = _paginate(db.table("wow_nfl_game_team_summaries").select("*").order("game_id").order("team"))
    if not games or not summaries:
        raise NFLModelInputsInsufficient("NFL_HISTORICAL_FEATURE_CONTEXT_UNAVAILABLE")
    return games, summaries


def _prediction_feature_row(
    *,
    db: Any,
    evidence: dict[str, Any],
    canonical_game_id: str,
    canonical_home_team: str,
    canonical_away_team: str,
) -> dict[str, Any]:
    games, summaries = _load_history(db)
    return build_prediction_feature_row(
        training_games=games,
        team_summaries=summaries,
        game_id=canonical_game_id,
        season=int(evidence["season"]),
        week=int(evidence["week"]),
        gameday=str(evidence["gameday"]),
        home_team=canonical_home_team,
        away_team=canonical_away_team,
        schedule_content_sha256=str(evidence["schedule_content_sha256"]),
    )


def _history_counts(feature_row: dict[str, Any]) -> tuple[int, int]:
    features = feature_row["features"]
    return (
        int(features["home_season_prior_games"]),
        int(features["away_season_prior_games"]),
    )


def score_nfl_team_event(req: Any, *, db: Any) -> dict[str, Any]:
    evidence = dict(req.sport_specific_evidence or {})
    required = (
        "season",
        "week",
        "gameday",
        "schedule_content_sha256",
        "canonical_game_id",
        "canonical_home_team",
        "canonical_away_team",
    )
    missing = [name for name in required if evidence.get(name) in (None, "")]
    if missing:
        raise NFLModelInputsInsufficient("NFL_CANONICAL_EVIDENCE_INCOMPLETE:" + ",".join(missing))

    canonical_game_id = str(evidence["canonical_game_id"])
    canonical_home_team = str(evidence["canonical_home_team"])
    canonical_away_team = str(evidence["canonical_away_team"])

    try:
        feature_row = _prediction_feature_row(
            db=db,
            evidence=evidence,
            canonical_game_id=canonical_game_id,
            canonical_home_team=canonical_home_team,
            canonical_away_team=canonical_away_team,
        )
    except NFLModelInputsInsufficient:
        raise
    except Exception as exc:
        raise NFLModelScorerFailed("NFL_FEATURE_ASSEMBLY_FAILED") from exc

    home_required = int(evidence.get("required_home_season_prior_games") or 0)
    away_required = int(evidence.get("required_away_season_prior_games") or 0)
    home_seen, away_seen = _history_counts(feature_row)

    # The schedule ledger can refresh before nflverse PBP.  Repair only the
    # under-count case, then recompute the identical feature row and retain the
    # exact history-count gate.  Over-counts remain immediately fail-closed.
    if (
        (home_seen < home_required or away_seen < away_required)
        and home_seen <= home_required
        and away_seen <= away_required
    ):
        try:
            from v17.nfl_current_season_summary_refresh import refresh_current_season_summaries

            refresh_current_season_summaries(db, season=int(evidence["season"]))
            feature_row = _prediction_feature_row(
                db=db,
                evidence=evidence,
                canonical_game_id=canonical_game_id,
                canonical_home_team=canonical_home_team,
                canonical_away_team=canonical_away_team,
            )
            home_seen, away_seen = _history_counts(feature_row)
        except NFLModelInputsInsufficient:
            raise
        except Exception as exc:
            raise NFLModelInputsInsufficient(
                "NFL_CURRENT_SEASON_HISTORY_REFRESH_FAILED:"
                f"{type(exc).__name__}:HOME={home_seen}/{home_required}:"
                f"AWAY={away_seen}/{away_required}"
            ) from exc

    if home_seen != home_required or away_seen != away_required:
        raise NFLModelInputsInsufficient(
            f"NFL_CURRENT_SEASON_HISTORY_STALE:HOME={home_seen}/{home_required}:"
            f"AWAY={away_seen}/{away_required}"
        )

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

    model_output_snapshot = {
        **model_result,
        "feature_row_hash": feature_row["row_inputs_hash"],
        "feature_max_prior_gameday": feature_row.get("max_prior_gameday"),
        "selected_participant": selected,
        "ranked_probability": selection_lower,
        "ranking_basis": "CALIBRATED_LOWER_BOUND",
        "canonical_game_id": canonical_game_id,
        "provider_event_id": evidence.get("provider_event_id"),
        "canonical_home_team": canonical_home_team,
        "canonical_away_team": canonical_away_team,
        "identity_resolution": evidence.get("identity_resolution"),
    }
    payload = {
        "research_run_id": req.research_run_id,
        "event_key": req.event_key,
        "official_event_id": canonical_game_id,
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
        "model_output_snapshot": model_output_snapshot,
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
        "canonical_event_id": canonical_game_id,
        "provider_event_id": evidence.get("provider_event_id"),
        "identity_resolution": evidence.get("identity_resolution"),
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
