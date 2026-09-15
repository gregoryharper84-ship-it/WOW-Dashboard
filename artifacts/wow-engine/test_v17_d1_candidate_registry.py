from __future__ import annotations

from types import SimpleNamespace

import pytest

from v17.d1_candidate_registry import D1RegistryError, persist_candidate_package


class FakeQuery:
    def __init__(self, db, table):
        self.db = db
        self.table_name = table
        self.filters = {}
        self.payload = None
        self.mode = "select"

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

    def execute(self):
        rows = self.db.rows.setdefault(self.table_name, [])
        if self.mode == "insert":
            payload = dict(self.payload or {})
            if self.table_name == "wow_d1_source_events":
                payload.setdefault("source_event_id", f"source-{len(rows)+1}")
            elif self.table_name == "wow_d1_training_rows":
                payload.setdefault("training_row_id", f"train-{len(rows)+1}")
            elif self.table_name == "wow_d1_candidate_artifacts":
                payload.setdefault("candidate_id", f"candidate-{len(rows)+1}")
            rows.append(payload)
            return SimpleNamespace(data=[payload])
        matched = [row for row in rows if all(row.get(k) == v for k, v in self.filters.items())]
        return SimpleNamespace(data=matched)


class FakeDB:
    def __init__(self):
        self.rows = {}

    def table(self, name):
        return FakeQuery(self, name)


def package():
    return {
        "games": [{
            "sport": "NHL",
            "league": "NHL",
            "official_event_id": "evt-1",
            "season": "20252026",
            "event_start_time": "2026-01-01T00:00:00+00:00",
            "home_team": "BOS",
            "away_team": "NYR",
            "home_score": 3,
            "away_score": 2,
            "positive_outcome": True,
            "source_provider": "NHL_PUBLIC_WEB_API",
            "source_uri": "https://example.test",
            "source_retrieved_at": "2026-09-15T00:00:00+00:00",
            "source_payload_sha256": "a" * 64,
            "historical_reconstruction": True,
            "can_execute": False,
        }],
        "feature_rows": [{
            "sport": "NHL",
            "league": "NHL",
            "official_event_id": "evt-1",
            "event_start_time": "2026-01-01T00:00:00+00:00",
            "feature_as_of": "2025-12-31T23:59:59+00:00",
            "feature_schema_version": "NHL_REGULAR_SEASON_FEATURES_V1",
            "features": {"elo_delta": 15.0},
            "positive_outcome": True,
            "source_manifest": {"market_features_used": False},
            "source_manifest_sha256": "b" * 64,
            "historical_reconstruction": True,
            "archived_pregame_snapshot": False,
            "market_features_used": False,
            "can_execute": False,
        }],
        "candidate": {
            "sport": "NHL",
            "league": "NHL",
            "model_family": "NHL_REGULAR_SEASON_LOGISTIC_V1",
            "model_artifact_version": "nhl-v1",
            "feature_schema_version": "NHL_REGULAR_SEASON_FEATURES_V1",
            "source_policy_id": "NHL_PUBLIC_WEB_API",
            "training_dataset_hash": "c" * 64,
            "training_code_sha": "d" * 40,
            "artifact_checksum": "e" * 64,
            "artifact_payload": {"model_family": "NHL_REGULAR_SEASON_LOGISTIC_V1"},
            "calibrator_payload": {"method": "EMPIRICAL_WILSON_BINS_V1"},
            "validation_metrics": {"can_execute": False, "probability_publishable": False},
            "training_rows": 400,
            "calibration_rows": 80,
            "test_rows": 80,
            "research_screen_pass": True,
            "source_review_status": "REQUIRED",
            "lifecycle_state": "CANDIDATE",
            "promoted": False,
            "active": False,
            "automatic_certification": False,
            "automatic_promotion": False,
            "probability_publishable": False,
            "can_execute": False,
        },
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def test_candidate_package_persists_inert_rows_idempotently():
    db = FakeDB()
    first = persist_candidate_package(db, package())
    second = persist_candidate_package(db, package())
    assert first["status"] == "D1_CANDIDATE_PACKAGE_PERSISTED"
    assert first["candidate"]["status"] == "CANDIDATE_REGISTERED"
    assert second["candidate"]["status"] == "ALREADY_REGISTERED_IDENTICAL"
    assert len(db.rows["wow_d1_source_events"]) == 1
    assert len(db.rows["wow_d1_training_rows"]) == 1
    assert len(db.rows["wow_d1_candidate_artifacts"]) == 1
    assert db.rows["wow_d1_candidate_artifacts"][0]["can_execute"] is False
    assert db.rows["wow_d1_candidate_artifacts"][0]["probability_publishable"] is False


def test_candidate_registry_rejects_market_features():
    db = FakeDB()
    bad = package()
    bad["feature_rows"][0]["market_features_used"] = True
    with pytest.raises(D1RegistryError) as caught:
        persist_candidate_package(db, bad)
    assert caught.value.code == "D1_TRAINING_MARKET_FEATURES_FORBIDDEN"


def test_candidate_registry_rejects_activation_or_publication():
    db = FakeDB()
    bad = package()
    bad["candidate"]["active"] = True
    with pytest.raises(D1RegistryError) as caught:
        persist_candidate_package(db, bad)
    assert caught.value.code == "D1_CANDIDATE_GOVERNANCE_FLAGS_INVALID"
