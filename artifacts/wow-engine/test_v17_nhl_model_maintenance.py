from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI

import v17.nhl_model_maintenance as maintenance


class FakeQuery:
    def __init__(self, db, table):
        self.db = db
        self.table_name = table
        self.filters = {}
        self.mode = "select"
        self.payload = None
        self.on_conflict = None
        self.ignore_duplicates = False

    def select(self, *args):
        self.mode = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, value):
        return self

    def insert(self, payload):
        self.mode = "insert"
        self.payload = dict(payload)
        return self

    def upsert(self, payload, *, on_conflict=None, ignore_duplicates=False):
        self.mode = "upsert"
        self.payload = [dict(item) for item in payload]
        self.on_conflict = on_conflict
        self.ignore_duplicates = ignore_duplicates
        return self

    def execute(self):
        rows = self.db.rows.setdefault(self.table_name, [])
        if self.mode == "insert":
            payload = dict(self.payload or {})
            if self.table_name == "wow_d1_source_events":
                payload.setdefault("source_event_id", "source-1")
            elif self.table_name == "wow_d1_training_rows":
                payload.setdefault("training_row_id", "training-1")
            elif self.table_name == "wow_d1_candidate_artifacts":
                payload.setdefault("candidate_id", "candidate-1")
            rows.append(payload)
            return SimpleNamespace(data=[payload])
        if self.mode == "upsert":
            inserted = []
            keys = tuple((self.on_conflict or "").split(",")) if self.on_conflict else ()
            for payload in self.payload or []:
                duplicate = any(all(existing.get(key) == payload.get(key) for key in keys) for existing in rows) if keys else False
                if duplicate and self.ignore_duplicates:
                    continue
                rows.append(dict(payload))
                inserted.append(dict(payload))
            return SimpleNamespace(data=inserted)
        return SimpleNamespace(data=[row for row in rows if all(row.get(k) == v for k, v in self.filters.items())])


class FakeDB:
    def __init__(self):
        self.rows = {}

    def table(self, name):
        return FakeQuery(self, name)


def fake_package():
    return {
        "status": "CANDIDATE_BUILT",
        "games": [{
            "sport": "NHL", "league": "NHL", "official_event_id": "evt-1",
            "season": "20252026", "event_start_time": "2026-01-01T00:00:00+00:00",
            "home_team": "BOS", "away_team": "NYR", "home_score": 3, "away_score": 2,
            "positive_outcome": True, "source_provider": "NHL_PUBLIC_WEB_API",
            "source_uri": "https://example.test", "source_retrieved_at": "2026-09-15T00:00:00+00:00",
            "source_payload_sha256": "a" * 64, "historical_reconstruction": True, "can_execute": False,
        }],
        "feature_rows": [{
            "sport": "NHL", "league": "NHL", "official_event_id": "evt-1",
            "event_start_time": "2026-01-01T00:00:00+00:00",
            "feature_as_of": "2025-12-31T23:59:59+00:00",
            "feature_schema_version": "NHL_REGULAR_SEASON_FEATURES_V1",
            "features": {"elo_delta": 10.0},
            "source_manifest": {"market_features_used": False}, "source_manifest_sha256": "b" * 64,
            "historical_reconstruction": True, "archived_pregame_snapshot": False,
            "market_features_used": False, "can_execute": False,
        }],
        "candidate": {
            "sport": "NHL", "league": "NHL", "model_family": "NHL_REGULAR_SEASON_LOGISTIC_V1",
            "model_artifact_version": "nhl-v1", "feature_schema_version": "NHL_REGULAR_SEASON_FEATURES_V1",
            "source_policy_id": "NHL_PUBLIC_WEB_API", "training_dataset_hash": "c" * 64,
            "training_code_sha": "d" * 40, "artifact_checksum": "e" * 64,
            "artifact_payload": {"model_family": "NHL_REGULAR_SEASON_LOGISTIC_V1"},
            "calibrator_payload": {"method": "EMPIRICAL_WILSON_BINS_V1"},
            "validation_metrics": {"can_execute": False, "probability_publishable": False, "ece": 0.04},
            "training_rows": 400, "calibration_rows": 80, "test_rows": 80,
            "research_screen_pass": True, "source_review_status": "REQUIRED",
            "lifecycle_state": "CANDIDATE", "promoted": False, "active": False,
            "probability_publishable": False, "can_execute": False,
        },
        "automatic_certification": False, "automatic_promotion": False,
        "probability_publishable": False, "can_execute": False,
    }


def test_default_years_stop_before_current_season_start():
    from datetime import datetime, timezone
    assert maintenance.default_start_years(datetime(2026, 9, 15, tzinfo=timezone.utc)) == (2021, 2022, 2023, 2024, 2025)


def test_maintenance_persists_candidate_and_reconciles_training_outcome(monkeypatch):
    monkeypatch.setattr(maintenance, "build_candidate", lambda years, **kwargs: fake_package())
    db = FakeDB()
    result = maintenance.run_nhl_model_maintenance(db, start_years=(2021,), training_code_sha="f" * 40)
    assert result["status"] == "CANDIDATE_EVIDENCE_UPDATED"
    assert result["controlling_specialist"] == "wow.nhl-game-win-probability-expert"
    assert result["certification_status"] == "CANDIDATE_ONLY"
    assert result["numerical_authority"] is False
    assert "SOURCE_PROVENANCE_NOT_CERTIFIED" in result["certification_blockers"]
    assert "DETERMINISTIC_REPLAY_NOT_READY" in result["certification_blockers"]
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
    assert db.rows["wow_d1_training_rows"][0]["outcome_json"] == {"positive_outcome": True}
    assert db.rows["wow_d1_candidate_artifacts"][0]["lifecycle_state"] == "CANDIDATE"


def test_source_review_pass_still_requires_deterministic_replay_before_lifecycle_review():
    candidate = fake_package()["candidate"]
    candidate["source_review_status"] = "PASS"
    state = maintenance._certification_assessment(candidate)
    assert state["status"] == "CANDIDATE_ONLY"
    assert state["numerical_authority"] is False
    assert state["blockers"] == ["DETERMINISTIC_REPLAY_NOT_READY"]


def test_batch_persistence_is_idempotent_for_source_and_training_rows(monkeypatch):
    monkeypatch.setattr(maintenance, "build_candidate", lambda years, **kwargs: fake_package())
    db = FakeDB()
    first = maintenance.run_nhl_model_maintenance(db, start_years=(2021,), training_code_sha="f" * 40)
    second = maintenance.run_nhl_model_maintenance(db, start_years=(2021,), training_code_sha="f" * 40)
    assert first["status"] == "CANDIDATE_EVIDENCE_UPDATED"
    assert second["status"] == "CANDIDATE_EVIDENCE_UPDATED"
    assert len(db.rows["wow_d1_source_events"]) == 1
    assert len(db.rows["wow_d1_training_rows"]) == 1
    assert len(db.rows["wow_d1_candidate_artifacts"]) == 1


def test_route_install_is_idempotent():
    app = FastAPI()
    maintenance.install_nhl_model_maintenance_route(app, auth_dependency=lambda: None, db_client_fn=FakeDB)
    maintenance.install_nhl_model_maintenance_route(app, auth_dependency=lambda: None, db_client_fn=FakeDB)
    paths = [route.path for route in app.router.routes]
    assert paths.count("/internal/v17/nhl-model-maintenance") == 1
