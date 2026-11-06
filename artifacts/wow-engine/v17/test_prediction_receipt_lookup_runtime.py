from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from v17.prediction_receipt_lookup_runtime import (
    PredictionReceiptLookupBatch,
    install_prediction_receipt_lookup_route,
    lookup_prediction_receipts,
    _SELECT_FIELDS,
)


class _Query:
    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, _fields):
        return self

    def eq(self, field, value):
        self.rows = [row for row in self.rows if row.get(field) == value]
        return self

    def gte(self, field, value):
        self.rows = [row for row in self.rows if str(row.get(field)) >= str(value)]
        return self

    def lt(self, field, value):
        self.rows = [row for row in self.rows if str(row.get(field)) < str(value)]
        return self

    def order(self, field, desc=False):
        self.rows.sort(key=lambda row: str(row.get(field) or ""), reverse=desc)
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class _Db:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "wow_predictions"
        return _Query(self.rows)


def _prediction(**overrides):
    now = datetime.now(timezone.utc)
    event_start = now + timedelta(hours=3)
    base = {
        "prediction_id": "11111111-1111-1111-1111-111111111111",
        "created_at": (now - timedelta(minutes=2)).isoformat(),
        "event_id": "MLB:2026-09-13:BOS-KC",
        "event_start_time": event_start.isoformat(),
        "model_timestamp": (now - timedelta(minutes=3)).isoformat(),
        "locked_at": (now - timedelta(minutes=2)).isoformat(),
        "player": "Payton Tolle",
        "team": "BOS",
        "opponent": "KC",
        "sport": "MLB",
        "market_type": "PROP_DISCRETE_PMF",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 4.5,
        "direction": "MORE",
        "source_snapshot_id": "22222222-2222-2222-2222-222222222222",
        "raw_model_probability": 0.70,
        "independent_model_probability": 0.69,
        "calibrated_probability": 0.68,
        "calibrated_probability_lower_bound": 0.62,
        "calibrated_probability_upper_bound": 0.74,
        "calibration_status": "PRECALIBRATION_SHRINKAGE",
        "calibration_method": "PRECALIBRATION_SHRINKAGE",
        "calibration_version": "v1",
        "probability_publishable": True,
        "probability_ceiling": "MODEL_QUALIFIED_HOLD",
        "money_lane_status": "PAYOUT_UNRESOLVED",
        "data_gaps": [],
        "blockers": [],
    }
    base.update(overrides)
    return base


def _exact_identity(**overrides):
    identity = {
        "event_id": "MLB:2026-09-13:BOS-KC",
        "player": "Payton Tolle",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 4.5,
        "direction": "MORE",
    }
    identity.update(overrides)
    return identity


def test_exact_thesis_recovers_immutable_pregame_probability_package():
    batch = PredictionReceiptLookupBatch.model_validate(
        {"rows": [{"row_key": "tolle-k", **_exact_identity(), "sport": "MLB"}]}
    )
    result = lookup_prediction_receipts(_Db([_prediction()]), batch)
    assert result["rows_matched"] == 1
    assert result["rows_blocked"] == 0
    receipt = result["rows"][0]["matches"][0]
    assert receipt["governed_prediction_table"] == "wow_predictions"
    assert receipt["is_immutable_pregame"] is True
    assert receipt["calibrated_probability"] == 0.68
    assert receipt["calibrated_lower_bound"] == 0.62
    assert receipt["can_execute"] is False


def test_prediction_id_with_conflicting_line_is_blocked_not_silently_retrieved():
    batch = PredictionReceiptLookupBatch.model_validate(
        {
            "rows": [
                {
                    "prediction_id": "11111111-1111-1111-1111-111111111111",
                    **_exact_identity(line=3.5),
                }
            ]
        }
    )
    result = lookup_prediction_receipts(_Db([_prediction()]), batch)
    row = result["rows"][0]
    assert result["rows_blocked"] == 1
    assert result["rows_matched"] == 0
    assert row["code"] == "PREDICTION_RECEIPT_IDENTITY_CONFLICT"
    assert row["detail"]["conflicting_fields"] == ["line"]
    assert row["matches"] == []


def test_prediction_id_with_conflicting_direction_is_blocked():
    batch = PredictionReceiptLookupBatch.model_validate(
        {
            "rows": [
                {
                    "prediction_id": "11111111-1111-1111-1111-111111111111",
                    **_exact_identity(direction="LESS"),
                }
            ]
        }
    )
    result = lookup_prediction_receipts(_Db([_prediction()]), batch)
    row = result["rows"][0]
    assert row["status"] == "BLOCKED"
    assert row["code"] == "PREDICTION_RECEIPT_IDENTITY_CONFLICT"
    assert row["detail"]["conflicting_fields"] == ["direction"]


def test_prediction_id_with_matching_identity_succeeds():
    batch = PredictionReceiptLookupBatch.model_validate(
        {
            "rows": [
                {
                    "prediction_id": "11111111-1111-1111-1111-111111111111",
                    **_exact_identity(),
                }
            ]
        }
    )
    result = lookup_prediction_receipts(_Db([_prediction()]), batch)
    assert result["rows_matched"] == 1
    assert result["rows"][0]["code"] == "IMMUTABLE_PREGAME_PREDICTION_MATCHED"


def test_fallback_requires_complete_event_player_stat_line_direction_identity():
    batch = PredictionReceiptLookupBatch.model_validate(
        {
            "rows": [
                {
                    "event_id": "MLB:2026-09-13:BOS-KC",
                    "player": "Payton Tolle",
                    "stat_type": "PITCHER_STRIKEOUTS",
                }
            ]
        }
    )
    result = lookup_prediction_receipts(_Db([_prediction()]), batch)
    row = result["rows"][0]
    assert result["rows_blocked"] == 1
    assert row["code"] == "PREDICTION_RECEIPT_LOOKUP_IDENTITY_INSUFFICIENT"
    assert row["detail"]["missing_identity_fields"] == ["line", "direction"]


def test_materially_different_line_does_not_match_full_fallback_identity():
    batch = PredictionReceiptLookupBatch.model_validate(
        {"rows": [{**_exact_identity(line=3.5), "sport": "MLB"}]}
    )
    result = lookup_prediction_receipts(_Db([_prediction()]), batch)
    assert result["rows_not_found"] == 1
    assert result["rows"][0]["code"] == "IMMUTABLE_PREGAME_PREDICTION_NOT_FOUND"


def test_ambiguous_exact_identity_rows_are_not_guessed():
    second = _prediction(
        prediction_id="33333333-3333-3333-3333-333333333333",
        created_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    )
    batch = PredictionReceiptLookupBatch.model_validate(
        {"rows": [{**_exact_identity(), "sport": "MLB"}]}
    )
    result = lookup_prediction_receipts(_Db([_prediction(), second]), batch)
    assert result["rows_ambiguous"] == 1
    assert result["rows"][0]["code"] == "MULTIPLE_PREDICTION_RECEIPTS_MATCH"


def test_lookup_route_is_read_only_and_installs_once():
    app = FastAPI()
    db = _Db([_prediction()])
    dep = Depends(lambda: None)
    install_prediction_receipt_lookup_route(app, db_client_fn=lambda: db, auth_dependency=dep)
    install_prediction_receipt_lookup_route(app, db_client_fn=lambda: db, auth_dependency=dep)
    assert (
        sum(
            getattr(route, "path", None) == "/v17/prediction-receipts/lookup"
            for route in app.router.routes
        )
        == 1
    )

    client = TestClient(app)
    response = client.post(
        "/v17/prediction-receipts/lookup",
        json={"rows": [{"prediction_id": "11111111-1111-1111-1111-111111111111"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["rows_matched"] == 1
    assert body["can_execute"] is False

def test_read_only_projection_includes_schema_verified_fitted_lineage():
    expected = {
        "model_provider_identity", "model_family", "model_artifact_version",
        "model_artifact_checksum", "model_bundle_fingerprint",
        "model_artifact_lifecycle_state", "feature_schema_version",
        "feature_transform_version", "feature_snapshot_hash",
        "training_dataset_hash", "training_code_sha", "specialist_version",
        "certification_id", "controlling_specialist",
        "scoring_profile_id", "scoring_profile_sha256", "evidence_source_kind",
    }
    selected = set(_SELECT_FIELDS.split(","))
    assert expected <= selected
    # The underlying ledger does not have these independent exact bindings;
    # adding them to SQL would turn every lookup into a 400/error.
    assert "period" not in selected
    assert "entity_id" not in selected


def test_exact_receipt_round_trips_artifact_metadata_without_certifying_identity():
    expected = {
        "model_artifact_version": "MLB_SO_FITTED_V17_A",
        "model_artifact_checksum": "a" * 64,
        "model_bundle_fingerprint": "b" * 64,
        "model_artifact_lifecycle_state": "PROMOTED",
        "specialist_version": "MLB_SO_V17",
        "certification_id": "cert-001",
        "controlling_specialist": "MLB_PITCHER_STRIKEOUTS",
        "feature_snapshot_hash": "c" * 64,
        "training_dataset_hash": "d" * 64,
        "training_code_sha": "e" * 40,
        "calibration_version": "CAL_V17",
    }
    batch = PredictionReceiptLookupBatch.model_validate(
        {"rows": [{"row_key": "tolle-k", **_exact_identity(), "sport": "MLB"}]}
    )
    result = lookup_prediction_receipts(_Db([_prediction(**expected)]), batch)
    match = result["rows"][0]["matches"][0]
    assert match["is_immutable_pregame"] is True
    for key, value in expected.items():
        assert match["prediction"][key] == value
    assert match["calibrated_probability"] == 0.68
    assert match["calibrated_lower_bound"] == 0.62
    assert match["can_execute"] is False
    assert result["can_execute"] is False
