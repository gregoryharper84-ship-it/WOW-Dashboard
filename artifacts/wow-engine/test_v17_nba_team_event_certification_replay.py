"""Read-only numerical replay tests for the NBA fitted team/event bundle."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from basketball_team_event_specialist import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    MODEL_FAMILY,
    TrainingGame,
    build_pregame_features,
)
from v17.nba_team_event_certification_replay import run_nba_team_event_certification_replay
from v17.team_event_certification_replay import run_certification_replay


def _sha(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)
        self.filters = []
        self.ordering = None
        self.start = None
        self.end = None
        self.max_rows = None

    def select(self, _fields):
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def order(self, field, desc=False):
        self.ordering = (field, desc)
        return self

    def range(self, start, end):
        self.start, self.end = start, end
        return self

    def limit(self, count):
        self.max_rows = count
        return self

    def execute(self):
        rows = [
            row for row in self.rows
            if all(row.get(field) == value for field, value in self.filters)
        ]
        if self.ordering:
            field, desc = self.ordering
            rows.sort(key=lambda row: str(row.get(field) or ""), reverse=desc)
        if self.start is not None:
            rows = rows[self.start:self.end + 1]
        elif self.max_rows is not None:
            rows = rows[:self.max_rows]
        return _Result(rows)


class _DB:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Query(self.tables.get(name, []))


def _fixture():
    games = []
    base = date(2025, 3, 1)

    # Five prior appearances for both target teams. The ten opponents are
    # unique and have fewer than five prior games, so only target games form
    # feature rows under MIN_PRIOR_GAMES=5.
    for index in range(5):
        day = (base + timedelta(days=index)).isoformat()
        games.append({
            "game_id": f"history-a-{index}",
            "season": 2025,
            "game_date": day,
            "status": "Final",
            "home_team_id": "A",
            "away_team_id": f"AX{index}",
            "home_score": 100 + index,
            "away_score": 90,
            "home_win": True,
        })
        games.append({
            "game_id": f"history-b-{index}",
            "season": 2025,
            "game_date": day,
            "status": "Final",
            "home_team_id": "B",
            "away_team_id": f"BX{index}",
            "home_score": 90,
            "away_score": 95 + index,
            "home_win": False,
        })

    target_games = [
        ("train-1", "2025-03-06", "A", "B", 101, 99),
        ("train-2", "2025-03-07", "B", "A", 98, 100),
        ("holdout-1", "2025-03-08", "A", "B", 102, 100),
    ]
    for game_id, game_date, home, away, home_score, away_score in target_games:
        games.append({
            "game_id": game_id,
            "season": 2025,
            "game_date": game_date,
            "status": "Final",
            "home_team_id": home,
            "away_team_id": away,
            "home_score": home_score,
            "away_score": away_score,
            "home_win": home_score > away_score,
        })

    training_rows = []
    training_games = []
    for row in games:
        game_id = row["game_id"]
        source_hash = hashlib.sha256(("source:" + game_id).encode()).hexdigest()
        training_rows.append({
            **row,
            "source_provider": "SPORTSDATAVERSE_ESPN",
            "source_endpoint": "https://data.example/nba/schedule.csv",
            "source_retrieved_at": "2026-11-08T18:00:00+00:00",
            "source_payload_sha256": source_hash,
            "settled": True,
        })
        training_games.append(TrainingGame(
            game_id=game_id,
            sport="NBA",
            season=2025,
            game_date=date.fromisoformat(row["game_date"]),
            home_team_id=row["home_team_id"],
            away_team_id=row["away_team_id"],
            home_score=row["home_score"],
            away_score=row["away_score"],
        ))

    generated = build_pregame_features(training_games, "NBA")
    feature_rows = []
    for row in generated:
        values = {
            name: float(value)
            for name, value in zip(FEATURE_NAMES, row.values)
        }
        payload = {
            "sport": "NBA",
            "game_id": row.game_id,
            "game_date": row.game_date.isoformat(),
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "home_games_prior": row.home_games_prior,
            "away_games_prior": row.away_games_prior,
            "features": values,
        }
        feature_rows.append({
            "game_id": row.game_id,
            "as_of": datetime.combine(
                row.game_date, time.min, tzinfo=timezone.utc
            ).isoformat(),
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "home_games_prior": row.home_games_prior,
            "away_games_prior": row.away_games_prior,
            "feature_payload": payload,
            "feature_payload_sha256": _sha(payload),
        })

    version = "NBA_TEAM_EVENT_LOGISTIC_V1_TEST"
    core = {
        "sport": "NBA",
        "model_family": MODEL_FAMILY,
        "model_artifact_version": version,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "intercept": 0.0,
        "coefficients": [0.0] * len(FEATURE_NAMES),
        "means": [0.0] * len(FEATURE_NAMES),
        "scales": [1.0] * len(FEATURE_NAMES),
    }
    artifact_checksum = _sha(core)
    raw_brier = 0.25
    raw_log_loss = math.log(2.0)
    artifact_payload = {
        **core,
        "training_row_count": 2,
        "holdout_row_count": 1,
        "train_start_date": "2025-03-06",
        "train_end_date": "2025-03-07",
        "holdout_brier": raw_brier,
        "holdout_log_loss": raw_log_loss,
        "holdout_accuracy": 1.0,
        "artifact_checksum": artifact_checksum,
    }
    artifact = {
        "artifact_id": "nba-artifact-1",
        "sport": "NBA",
        "model_family": MODEL_FAMILY,
        "model_artifact_version": version,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "artifact_format": "WOW_BASKETBALL_LOGISTIC_JSON_V1",
        "artifact_payload": artifact_payload,
        "artifact_checksum": artifact_checksum,
        "training_row_count": 2,
        "train_start_date": "2025-03-06",
        "train_end_date": "2025-03-07",
        "holdout_row_count": 1,
        "holdout_brier": raw_brier,
        "holdout_log_loss": raw_log_loss,
        "holdout_accuracy": 1.0,
        "promoted": False,
        "active": False,
        "certified_at": None,
        "created_at": "2026-11-08T18:20:41+00:00",
    }
    calibrator = {
        "calibrator_id": "nba-calibrator-1",
        "sport": "NBA",
        "calibrator_version": "NBA_TEAM_EVENT_PLATT_V1_TEST",
        "model_artifact_version": version,
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "coefficients": {"a": 0.0, "b": 1.0},
        "calibration_row_count": 2,
        "brier": 0.25,
        "log_loss": math.log(2.0),
        "ece": 0.0,
        "calibration_bias": 0.0,
        "promoted": False,
        "active": False,
        "created_at": "2026-11-08T18:20:41+00:00",
    }
    holdout_evidence = {
        "holdout_rows": 1,
        "holdout_start_date": "2025-03-08",
        "holdout_end_date": "2025-03-08",
        "calibrated_brier": 0.25,
        "calibrated_log_loss": math.log(2.0),
        "mean_calibrated_probability": 0.5,
        "observed_home_win_rate": 1.0,
        "calibration_bias": -0.5,
        "publication_authorized": False,
        "can_execute": False,
    }
    certification = {
        "sport": "NBA",
        "capability_status": "SHADOW",
        "reason_code": "CERTIFICATION_REPLAY_REQUIRED",
        "model_artifact_version": version,
        "calibrator_version": calibrator["calibrator_version"],
        "corpus_rows": len(training_rows),
        "settled_rows": len(training_rows),
        "certification_notes": {
            "calibration_training_cutoff": "2025-03-07",
            "calibration_training_rows": 2,
            "calibration_holdout_overlap_rows": 0,
            "untouched_holdout_evidence": holdout_evidence,
            "provenance_complete": True,
            "promotion_attempted": False,
            "can_execute": False,
        },
        "updated_at": "2026-11-08T18:20:41+00:00",
    }
    tables = {
        "wow_basketball_team_event_model_artifacts": [artifact],
        "wow_basketball_team_event_calibrators": [calibrator],
        "wow_basketball_team_event_certification": [certification],
        "wow_team_event_specialist_certifications": [],
        "wow_nba_training_games": training_rows,
        "wow_nba_pregame_feature_rows": feature_rows,
        "wow_d1_candidate_artifacts": [],
        "wow_d1_certification_evidence_receipts": [],
    }
    return tables


def test_nba_replay_reproduces_holdout_but_keeps_incomplete_bundle_held():
    report = run_nba_team_event_certification_replay(_DB(_fixture()))

    assert report["status"] == "REPLAY_EVIDENCE_REPRODUCED_BUT_BUNDLE_INCOMPLETE"
    assert report["numerical_replay_status"] == "PASS"
    assert report["numerical_replay_metrics_match"] is True
    assert report["artifact_integrity_valid"] is True
    assert report["artifact_ready"] is False
    assert report["calibrator_model_artifact_version_match"] is True
    assert report["calibrator_checksum_persisted"] is False
    assert report["calibrator_ready"] is False
    assert report["pregame_corpus"]["feature_reconstruction_status"] == "PASS"
    assert report["pregame_corpus"]["source_provenance_fields_valid"] is True
    assert report["pregame_corpus"]["source_payload_raw_bytes_persisted"] is False
    assert report["pregame_corpus"]["source_payload_hash_independently_recomputable"] is False
    assert report["pregame_corpus"]["settled_game_rows"] == 13
    assert report["pregame_corpus"]["persisted_feature_rows"] == 3
    assert report["pregame_corpus"]["settled_games_without_features"] == 10
    assert report["pregame_corpus_ready"] is False
    assert report["exact_bundle_match"] is False
    assert report["immutable_certification_record_present"] is False
    assert report["immutable_replay_receipt_persisted"] is False
    assert report["probability_publishable"] is False
    assert report["rank_eligible"] is False
    assert report["can_execute"] is False
    assert len(report["replay_evidence_sha256"]) == 64


def test_nba_replay_does_not_treat_a_missing_artifact_as_a_candidate():
    tables = _fixture()
    tables["wow_basketball_team_event_model_artifacts"] = []
    report = run_nba_team_event_certification_replay(_DB(tables))

    assert report["status"] == "CANDIDATE_ARTIFACT_MISSING"
    assert report["blockers"] == ["CANDIDATE_ARTIFACT_MISSING"]
    assert report["probability_publishable"] is False
    assert report["can_execute"] is False


def test_nba_replay_detects_a_tampered_feature_payload_hash():
    tables = _fixture()
    tables["wow_nba_pregame_feature_rows"][0]["feature_payload_sha256"] = "f" * 64
    report = run_nba_team_event_certification_replay(_DB(tables))

    assert report["numerical_replay_status"] == "PASS"
    assert report["pregame_corpus"]["feature_reconstruction_status"] == "BLOCKED"
    assert report["pregame_corpus"]["source_payload_hash_independently_recomputable"] is False
    assert "SOURCE_PROVENANCE_NOT_CERTIFIED" in report["blockers"]
    assert report["exact_bundle_match"] is False
    assert report["probability_publishable"] is False


def test_nba_replay_blocks_when_stored_holdout_metrics_do_not_reproduce():
    tables = _fixture()
    tables["wow_basketball_team_event_model_artifacts"][0]["holdout_brier"] = 0.20
    report = run_nba_team_event_certification_replay(_DB(tables))

    assert report["numerical_replay_status"] == "METRICS_MISMATCH"
    assert report["status"] == "NUMERICAL_REPLAY_BLOCKED"
    assert "CANDIDATE_PARTITION_METRICS_MISMATCH" in report["blockers"]
    assert report["probability_publishable"] is False
    assert report["can_execute"] is False


def test_team_event_replay_endpoint_exposes_nba_artifact_lane_without_promotion():
    report = run_certification_replay(_DB(_fixture()))

    assert report["status"] == "CERTIFICATION_REPLAY_COMPLETE"
    assert report["nba_team_event_replay"]["numerical_replay_status"] == "PASS"
    assert report["summary"]["nba_team_event_replay_status"] == (
        "REPLAY_EVIDENCE_REPRODUCED_BUT_BUNDLE_INCOMPLETE"
    )
    assert report["summary"]["nba_team_event_exact_bundle_match"] is False
    assert report["automatic_certification"] is False
    assert report["automatic_promotion"] is False
    assert report["probability_publishable"] is False
    assert report["can_execute"] is False



def test_nba_replay_holds_when_calibration_metadata_overlaps_holdout():
    tables = _fixture()
    tables["wow_basketball_team_event_certification"][0]["certification_notes"][
        "calibration_holdout_overlap_rows"
    ] = 1
    report = run_nba_team_event_certification_replay(_DB(tables))

    assert report["numerical_replay_status"] == "PASS"
    assert report["calibrator_fit_cohort_metadata_valid"] is False
    assert report["calibrator_ready"] is False
    assert report["exact_bundle_match"] is False
    assert report["probability_publishable"] is False
    assert report["can_execute"] is False
