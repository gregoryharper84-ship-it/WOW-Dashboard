from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from ncaaf_candidate_training_runner import (
    NCAAFTrainingRunnerUnavailable,
    load_training_rows,
    train_and_persist_candidate,
)


class Result:
    def __init__(self, data): self.data = data


class Table:
    def __init__(self, db, name): self.db, self.name = db, name
    def select(self, columns): return Query(self.db, self.name)
    def upsert(self, rows, on_conflict=None):
        self.db.writes.append((self.name, rows, on_conflict)); return self
    def execute(self): return Result([])


class Query:
    def __init__(self, db, name): self.db, self.name = db, name; self.a = 0; self.b = 999
    def range(self, a, b): self.a, self.b = a, b; return self
    def execute(self): return Result(self.db.data[self.name][self.a:self.b+1])


class DB:
    def __init__(self, data): self.data = data; self.writes = []
    def table(self, name): return Table(self, name)


def data(n=300):
    start = datetime(2022, 8, 1, tzinfo=timezone.utc)
    games, features = [], []
    for i in range(n):
        tid = f"g-{i}"
        kickoff = start + timedelta(days=i)
        signal = ((i % 20) - 9.5) / 5.0
        games.append({
            "training_game_id": tid, "official_event_id": f"evt-{i}", "season": 2022 + i // 120,
            "event_start_time": kickoff.isoformat(), "neutral_site": False, "home_team": f"H{i}", "away_team": f"A{i}",
            "home_won": (signal + (0.25 if i % 3 else -0.25)) > 0,
        })
        features.append({
            "training_game_id": tid, "feature_schema_version": "NCAAF_FEATURES_V1",
            "feature_as_of": (kickoff - timedelta(hours=12)).isoformat(),
            "home_power_rating": signal, "away_power_rating": 0.0,
            "home_off_epa": signal*.4, "away_off_epa": 0.0, "home_def_epa": -signal*.2, "away_def_epa": 0.0,
            "home_success_rate": .5, "away_success_rate": .4, "home_explosiveness": 1.1, "away_explosiveness": 1.0,
            "home_qb_value": 1.0, "away_qb_value": .5, "home_qb_certainty": .9, "away_qb_certainty": .8,
            "home_ol_health": .9, "away_ol_health": .8, "home_def_front_health": .9, "away_def_front_health": .8,
            "home_skill_availability": .9, "away_skill_availability": .8, "home_rest_days": 7.0, "away_rest_days": 6.0,
            "travel_distance_miles": 100.0 + i%10, "home_tempo": .6, "away_tempo": .5,
            "home_turnover_volatility": .2, "away_turnover_volatility": .3,
            "home_special_teams_rating": .4, "away_special_teams_rating": .2,
            "weather_wind_mph": 5.0, "weather_precip_probability": .1,
        })
    return {"wow_ncaaf_training_games": games, "wow_ncaaf_training_features": features}


def test_load_requires_300_complete_rows():
    with pytest.raises(NCAAFTrainingRunnerUnavailable) as exc:
        load_training_rows(DB(data(299)))
    assert exc.value.code == "NCAAF_COMPLETE_TRAINING_ROWS_INSUFFICIENT"


def test_candidate_runner_persists_only_candidate_blocked_nonpublishable_artifacts():
    db = DB(data())
    result = train_and_persist_candidate(db, training_code_sha="abcdef1234567890")
    assert result["lifecycle_state"] == "CANDIDATE"
    assert result["calibration_health_status"] == "BLOCKED"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    model_write = next(rows for name, rows, _ in db.writes if name == "wow_ncaaf_fitted_model_artifacts")
    cal_write = next(rows for name, rows, _ in db.writes if name == "wow_ncaaf_calibrator_artifacts")
    assert model_write["active"] is False and model_write["promoted"] is False and model_write["probability_publishable"] is False
    assert cal_write["active"] is False and cal_write["calibration_health_status"] == "BLOCKED" and cal_write["probability_publishable"] is False
    assert model_write["can_execute"] is False and cal_write["can_execute"] is False


def test_candidate_runner_requires_auditable_code_sha():
    with pytest.raises(NCAAFTrainingRunnerUnavailable) as exc:
        train_and_persist_candidate(DB(data()), training_code_sha="bad")
    assert exc.value.code == "NCAAF_TRAINING_CODE_SHA_REQUIRED"
