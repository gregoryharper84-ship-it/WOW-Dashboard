"""Independent contracts for the registry readers behind the V17 evidence triad."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v17.model_evidence_bundle_contract import (
    COMPONENT_REQUIRED_FIELDS,
    ModelBundleScope,
)
from v17.model_evidence_registry_readers import (
    AMBIGUOUS,
    FOUND,
    MISSING,
    NOT_CONFIGURED,
    READ_FAILED,
    assess_registry_model_bundle,
    read_model_evidence_bundle,
)

ARTIFACT_SHA = "a" * 64
DATASET_SHA = "b" * 64
CODE_SHA = "c" * 40
PREGAME_SHA = "d" * 64

TEAM_EVENT_START = "2026-11-12T19:00:00+00:00"
TEAM_EVENT_PREDICTION = "2026-11-12T18:30:00+00:00"
TEAM_EVENT_UPDATE = "2026-11-12T18:10:00+00:00"


class _Query:
    def __init__(self, rows: list[dict], *, fail: bool = False):
        self._rows = rows
        self._fail = fail
        self._filters: list[tuple[str, object]] = []
        self._order: tuple[str, bool] | None = None
        self._limit: int | None = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column: str, value: object):
        self._filters.append((column, value))
        return self

    def order(self, column: str, desc: bool = False):
        self._order = (column, desc)
        return self

    def limit(self, count: int):
        self._limit = count
        return self

    def execute(self):
        if self._fail:
            raise RuntimeError("registry unavailable")
        rows = [
            row
            for row in self._rows
            if all(str(row.get(column)) == str(value) for column, value in self._filters)
        ]
        if self._order:
            column, desc = self._order
            rows = sorted(rows, key=lambda row: str(row.get(column) or ""), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return SimpleNamespace(data=rows)


class _FakeDb:
    def __init__(self, tables: dict[str, list[dict]], *, failing: set[str] | None = None):
        self.tables = tables
        self.failing = failing or set()

    def table(self, name: str) -> _Query:
        return _Query(self.tables.get(name, []), fail=name in self.failing)


def _scope(kind="TEAM_EVENT", sport="NFL", **overrides) -> ModelBundleScope:
    values = {
        "model_kind": kind,
        "sport": sport,
        "model_family": "TEST_FITTED_LOGISTIC_V1",
        "model_artifact_version": "TEST_FITTED_LOGISTIC_V1_20261108",
        "feature_schema_version": "TEST_FEATURES_V1",
        "official_event_id": "official-event-1",
        "event_start_time": TEAM_EVENT_START,
        "prediction_timestamp": TEAM_EVENT_PREDICTION,
        "latest_material_update_timestamp": TEAM_EVENT_UPDATE,
    }
    values.update(overrides)
    if kind == "PLAYER_PROP":
        values.update(
            market_family=overrides.get("market_family", "POINTS"),
            player_id="player-1",
            stat_type=overrides.get("stat_type", "PTS"),
            period="FULL_GAME",
            line="7+",
            direction="MORE",
        )
    if kind == "WEATHER_CONTRACT":
        values.update(
            market_family=overrides.get("market_family", "HIGH_TEMPERATURE"),
            contract_id="contract-1",
            station_id="station-1",
        )
    return ModelBundleScope(**values)


def _nfl_artifact(**overrides) -> dict:
    row = {
        "artifact_id": "artifact-1",
        "sport": "NFL",
        "model_family": "TEST_FITTED_LOGISTIC_V1",
        "model_artifact_version": "TEST_FITTED_LOGISTIC_V1_20261108",
        "feature_schema_version": "TEST_FEATURES_V1",
        "artifact_checksum": ARTIFACT_SHA,
        "training_dataset_hash": DATASET_SHA,
        "training_code_sha": CODE_SHA,
        "training_rows": 5000,
        # The artifact's own foreign key is the only calibrator link used.
        "calibrator_id": "calibrator-1",
        "created_at": "2026-11-07T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def _nfl_calibrator(**overrides) -> dict:
    row = {
        "calibrator_id": "calibrator-1",
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "training_n": 2500,
        "bounds_method_version": "PREDICTIVE_BOUNDS_V1",
        "fit_end": "2026-11-06T00:00:00+00:00",
        "fit_metrics_json": {"brier": 0.22, "log_loss": 0.65, "calibration_bias": -0.01},
        "created_at": "2026-11-06T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def _nfl_pregame(event_id="other-event", **overrides) -> dict:
    row = {
        "game_id": event_id,
        "feature_schema_version": "TEST_FEATURES_V1",
        "row_inputs_hash": PREGAME_SHA,
        "locked_at": "2026-11-12T18:20:00+00:00",
    }
    row.update(overrides)
    return row


def _receipt(payload: dict, **overrides) -> dict:
    row = {
        "sport": "NFL",
        "model_version": "TEST_FITTED_LOGISTIC_V1_20261108",
        "artifact_sha256": ARTIFACT_SHA,
        "feature_schema_version": "TEST_FEATURES_V1",
        "certification_status": "CERTIFIED",
        "independent_verification_status": "PASS",
        "verification_receipt": payload,
        "created_at": "2026-11-07T12:00:00+00:00",
    }
    row.update(overrides)
    return row


def _assess(db, scope) -> dict:
    return assess_registry_model_bundle(db, scope)


def test_nfl_lane_reads_components_but_never_grants_publication():
    db = _FakeDb({
        "wow_nfl_event_fitted_model_artifacts": [_nfl_artifact()],
        "wow_calibrators": [_nfl_calibrator()],
        # The frozen row belongs to a different event: nothing substitutes it.
        "wow_nfl_pregame_feature_rows": [_nfl_pregame("other-event")],
    })
    result = _assess(db, _scope())

    sources = result["component_sources"]
    assert sources["artifact"]["status"] == FOUND
    assert sources["calibrator"]["status"] == FOUND
    assert sources["pregame_corpus"]["status"] == MISSING
    assert "MODEL_BUNDLE_PREGAME_CORPUS_MISSING" in result["blockers"]
    assert "event_start_time" in result["missing_component_fields"]["pregame_corpus"]
    assert result["bundle_complete"] is False
    assert result["status"] == "BLOCKED"
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


def test_registry_verification_columns_are_not_trusted():
    db = _FakeDb({
        "wow_nfl_event_fitted_model_artifacts": [
            _nfl_artifact(
                payload_checksum_verified=True,
                training_provenance_verified=True,
            )
        ],
        "wow_calibrators": [_nfl_calibrator()],
        "wow_nfl_pregame_feature_rows": [_nfl_pregame("official-event-1")],
    })
    result = _assess(db, _scope())

    assert "MODEL_BUNDLE_VERIFICATION_RECEIPT_MISSING" in result["blockers"]
    assert "MODEL_BUNDLE_ARTIFACT_INVALID_OR_UNVERIFIED" in result["blockers"]
    assert result["artifact_valid"] is False
    assert result["bundle_complete"] is False


def test_explicit_receipt_flags_can_validate_an_artifact():
    receipt = _receipt({
        "artifact_payload_checksum_verified": True,
        "training_provenance_verified": True,
        "calibrator_chronological_replay_verified": True,
        "calibrated_lower_bound_verified": False,
    })
    db = _FakeDb({
        "wow_nfl_event_fitted_model_artifacts": [_nfl_artifact()],
        "wow_calibrators": [_nfl_calibrator()],
        "wow_nfl_pregame_feature_rows": [_nfl_pregame("official-event-1")],
        "wow_team_event_specialist_certifications": [receipt],
    })
    result = _assess(db, _scope())

    assert "MODEL_BUNDLE_VERIFICATION_RECEIPT_MISSING" not in result["blockers"]
    assert result["artifact_valid"] is True
    # The calibrator table cannot supply an artifact checksum binding, immutable
    # calibrator checksum or holdout separation, so it stays invalid.
    assert result["calibrator_valid"] is False
    assert "calibrator_checksum" in result["missing_component_fields"]["calibrator"]
    assert "model_artifact_checksum" in result["missing_component_fields"]["calibrator"]
    assert result["probability_publishable"] is False


@pytest.mark.parametrize("sport", ["NHL", "NCAAB", "NCAAF", "SOCCER", "TENNIS", "PGA", "MMA", "BOXING", "CRICKET"])
def test_declared_but_unconfigured_lanes_are_typed_not_silent(sport):
    result = _assess(_FakeDb({}), _scope(sport=sport))
    for component in ("ARTIFACT", "CALIBRATOR", "PREGAME_CORPUS"):
        assert f"MODEL_BUNDLE_{component}_SOURCE_NOT_CONFIGURED" in result["blockers"]
    assert result["bundle_complete"] is False
    assert result["can_execute"] is False


def test_weather_contract_has_no_fitted_registry_reader():
    result = _assess(_FakeDb({}), _scope("WEATHER_CONTRACT", "WEATHER"))
    assert result["component_sources"]["artifact"]["status"] == NOT_CONFIGURED
    assert "MODEL_BUNDLE_ARTIFACT_SOURCE_NOT_CONFIGURED" in result["blockers"]


def test_read_failure_is_distinct_from_absence_and_never_raises():
    db = _FakeDb(
        {"wow_nfl_event_fitted_model_artifacts": [_nfl_artifact()]},
        failing={"wow_nfl_event_fitted_model_artifacts"},
    )
    result = _assess(db, _scope())

    assert result["component_sources"]["artifact"]["status"] == READ_FAILED
    assert "MODEL_BUNDLE_ARTIFACT_READ_FAILED" in result["blockers"]
    assert "MODEL_BUNDLE_ARTIFACT_SOURCE_NOT_CONFIGURED" not in result["blockers"]


def test_ambiguous_exact_version_blocks_instead_of_picking_a_row():
    db = _FakeDb({
        "wow_nfl_event_fitted_model_artifacts": [
            _nfl_artifact(),
            _nfl_artifact(artifact_id="artifact-2"),
        ],
    })
    evidence = read_model_evidence_bundle(db, _scope())

    assert evidence.components["artifact"].status == AMBIGUOUS
    assert "MODEL_BUNDLE_ARTIFACT_AMBIGUOUS" in evidence.read_blockers
    assert evidence.artifact is None


def test_basketball_table_schema_gaps_are_reported_per_field():
    db = _FakeDb({
        "wow_basketball_team_event_model_artifacts": [{
            "sport": "NBA",
            "artifact_id": "artifact-nba",
            "model_family": "TEST_FITTED_LOGISTIC_V1",
            "model_artifact_version": "TEST_FITTED_LOGISTIC_V1_20261108",
            "feature_schema_version": "TEST_FEATURES_V1",
            "artifact_checksum": ARTIFACT_SHA,
            "training_row_count": 7000,
            "created_at": "2026-11-07T00:00:00+00:00",
        }],
    })
    result = _assess(db, _scope(sport="NBA"))

    missing = result["missing_component_fields"]["artifact"]
    assert "training_dataset_hash" in missing
    assert "training_code_sha" in missing
    assert "MODEL_BUNDLE_ARTIFACT_INVALID_OR_UNVERIFIED" in result["blockers"]


def test_lane_selection_is_exact_by_sport():
    db = _FakeDb({
        "wow_basketball_team_event_model_artifacts": [
            {
                "sport": "WNBA",
                "artifact_id": "artifact-wnba",
                "model_family": "TEST_FITTED_LOGISTIC_V1",
                "model_artifact_version": "TEST_FITTED_LOGISTIC_V1_20261108",
                "feature_schema_version": "TEST_FEATURES_V1",
                "artifact_checksum": ARTIFACT_SHA,
                "training_row_count": 2200,
                "created_at": "2026-11-07T00:00:00+00:00",
            },
            {
                "sport": "NBA",
                "artifact_id": "artifact-nba",
                "model_family": "TEST_FITTED_LOGISTIC_V1",
                "model_artifact_version": "TEST_FITTED_LOGISTIC_V1_20261108",
                "feature_schema_version": "TEST_FEATURES_V1",
                "artifact_checksum": "e" * 64,
                "training_row_count": 7000,
                "created_at": "2026-11-07T00:00:00+00:00",
            },
        ],
    })
    evidence = read_model_evidence_bundle(db, _scope(sport="NBA"))

    assert evidence.components["artifact"].status == FOUND
    assert evidence.artifact["artifact_id"] == "artifact-nba"
    assert evidence.artifact["sport"] == "NBA"


def test_prop_lane_uses_shared_registry_and_exact_stat():
    db = _FakeDb({
        "wow_prop_fitted_model_artifacts": [
            {
                "sport": "NBA",
                "stat_type": "PTS",
                "artifact_id": "artifact-pts",
                "model_family": "TEST_FITTED_LOGISTIC_V1",
                "model_artifact_version": "TEST_FITTED_LOGISTIC_V1_20261108",
                "feature_schema_version": "TEST_FEATURES_V1",
                "artifact_checksum": ARTIFACT_SHA,
                "training_dataset_hash": DATASET_SHA,
                "training_code_sha": CODE_SHA,
                "training_rows": 4800,
                "created_at": "2026-11-07T00:00:00+00:00",
            },
            {
                "sport": "NBA",
                "stat_type": "ASSISTS",
                "artifact_id": "artifact-ast",
                "model_family": "TEST_FITTED_LOGISTIC_V1",
                "model_artifact_version": "TEST_FITTED_LOGISTIC_V1_20261108",
                "feature_schema_version": "TEST_FEATURES_V1",
                "artifact_checksum": "f" * 64,
                "training_dataset_hash": DATASET_SHA,
                "training_code_sha": CODE_SHA,
                "training_rows": 4800,
                "created_at": "2026-11-07T00:00:00+00:00",
            },
        ],
    })
    result = _assess(db, _scope("PLAYER_PROP", "NBA", stat_type="PTS"))

    assert result["component_sources"]["artifact"]["status"] == FOUND
    assert result["component_sources"]["calibrator"]["status"] == NOT_CONFIGURED
    assert result["component_sources"]["pregame_corpus"]["status"] == NOT_CONFIGURED
    assert result["missing_component_fields"]["calibrator"] == list(
        COMPONENT_REQUIRED_FIELDS["calibrator"]
    )


def test_missing_database_is_typed_and_never_raises():
    evidence = read_model_evidence_bundle(None, _scope())
    assert evidence.components["artifact"].status == READ_FAILED
    assert "MODEL_BUNDLE_ARTIFACT_READ_FAILED" in evidence.read_blockers
    assert evidence.artifact is None


def test_component_field_contract_covers_every_required_component():
    assert set(COMPONENT_REQUIRED_FIELDS) == {"artifact", "calibrator", "pregame_corpus"}
    assert all(COMPONENT_REQUIRED_FIELDS.values())
