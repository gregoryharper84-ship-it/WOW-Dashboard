"""Independent synthetic contracts for the three-part V17 model evidence bundle."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from v17.model_evidence_bundle_contract import (
    CAN_EXECUTE,
    COMPONENTS,
    MODEL_KINDS,
    TEAM_EVENT_BUNDLE_REQUIREMENTS,
    ModelBundleScope,
    assess_model_bundle,
)
from v17.team_event_capability_manifest import EXPECTED_TEAM_EVENT_SPORTS


CHECKSUM = "a" * 64
CORPUS_SHA = "b" * 64
CALIBRATOR_SHA = "c" * 64
FEATURE_SHA = "d" * 64
ARTIFACT_SHA = "e" * 64


def _valid(kind="TEAM_EVENT", sport="NBA", market_family=None):
    if kind != "TEAM_EVENT" and market_family is None:
        market_family = "POINTS" if kind == "PLAYER_PROP" else "HIGH_TEMPERATURE"
    scope = ModelBundleScope(
        model_kind=kind,
        sport=sport,
        model_family="TEST_FITTED_LOGISTIC_V1",
        model_artifact_version="TEST_FITTED_LOGISTIC_V1_20261108",
        feature_schema_version="TEST_FEATURES_V1",
        market_family=market_family,
        official_event_id="official-2026-11-12-1",
        event_start_time="2026-11-12T19:00:00+00:00",
        prediction_timestamp="2026-11-12T18:30:00+00:00",
        latest_material_update_timestamp="2026-11-12T18:10:00+00:00",
    )
    identity = {
        "sport": sport,
        "market_family": market_family,
        "model_family": scope.model_family,
        "model_artifact_version": scope.model_artifact_version,
        "feature_schema_version": scope.feature_schema_version,
    }
    artifact = {
        **identity,
        "artifact_id": "artifact-123",
        "artifact_checksum": ARTIFACT_SHA,
        "training_dataset_hash": CORPUS_SHA,
        "training_code_sha": "0123456789abcdef",
        "payload_checksum_verified": True,
        "training_provenance_verified": True,
        "training_rows": 5000,
    }
    calibrator = {
        **identity,
        "model_artifact_checksum": ARTIFACT_SHA,
        "calibrator_id": "cal-123",
        "calibrator_checksum": CALIBRATOR_SHA,
        "calibration_method": "PLATT_TIME_SPLIT_V1",
        "calibration_training_n": 2500,
        "calibration_cohort_sha256": CORPUS_SHA,
        "holdout_evidence_sha256": CHECKSUM,
        "bounds_method_version": "PREDICTIVE_BOUNDS_V1",
        "chronological_replay_verified": True,
        "calibrated_lower_bound_verified": True,
        "holdout_overlap_rows": 0,
        "brier": 0.22,
        "log_loss": 0.65,
        "calibration_bias": -0.01,
        "calibration_fit_end": "2026-11-07T00:00:00+00:00",
    }
    pregame = {
        **identity,
        "model_artifact_checksum": ARTIFACT_SHA,
        "official_event_id": scope.official_event_id,
        "event_start_time": scope.event_start_time,
        "source_snapshot_id": "source-123",
        "source_snapshot_timestamp": "2026-11-12T18:00:00+00:00",
        "source_payload_sha256": CORPUS_SHA,
        "feature_snapshot_id": "feature-123",
        "feature_snapshot_timestamp": "2026-11-12T18:20:00+00:00",
        "feature_snapshot_sha256": FEATURE_SHA,
        "source_payload_independently_verified": True,
        "feature_replay_verified": True,
        "official_event_identity_verified": True,
        "settlement_basis_verified": True,
        "event_status": "SCHEDULED",
    }
    return scope, artifact, calibrator, pregame


def _assess(scope, artifact, calibrator, pregame):
    return assess_model_bundle(
        scope, artifact=artifact, calibrator=calibrator, pregame_corpus=pregame
    ).as_dict()


def test_contract_requires_triad_for_every_declared_team_event_sport():
    assert set(TEAM_EVENT_BUNDLE_REQUIREMENTS) == set(EXPECTED_TEAM_EVENT_SPORTS)
    assert set(COMPONENTS) == {"artifact", "calibrator", "pregame_corpus"}
    assert MODEL_KINDS == {"TEAM_EVENT", "PLAYER_PROP", "WEATHER_CONTRACT"}
    assert CAN_EXECUTE is False

    for sport in EXPECTED_TEAM_EVENT_SPORTS:
        scope, artifact, calibrator, pregame = _valid(sport=sport)
        result = _assess(scope, artifact, calibrator, pregame)
        assert result["bundle_complete"] is True, sport
        assert result["status"] == "READY_FOR_INDEPENDENT_REVIEW", sport
        assert result["probability_publishable"] is False, sport
        assert result["rank_eligible"] is False, sport
        assert result["automatic_certification"] is False, sport
        assert result["can_execute"] is False, sport


@pytest.mark.parametrize("kind,sport,market_family", [
    ("PLAYER_PROP", "NBA", "POINTS"),
    ("PLAYER_PROP", "NFL", "RECEIVING_YARDS"),
    ("PLAYER_PROP", "MLB", "STRIKEOUTS"),
    ("PLAYER_PROP", "WNBA", "ASSISTS"),
    ("WEATHER_CONTRACT", "WEATHER", "HIGH_TEMPERATURE"),
])
def test_exact_model_kinds_share_bundle_but_never_certify(kind, sport, market_family):
    scope, artifact, calibrator, pregame = _valid(kind, sport, market_family)
    result = _assess(scope, artifact, calibrator, pregame)
    assert result["bundle_complete"] is True
    assert result["model_kind"] == kind
    assert result["market_family"] == market_family
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


@pytest.mark.parametrize("missing,typed", [
    ("artifact", "MODEL_BUNDLE_ARTIFACT_MISSING"),
    ("calibrator", "MODEL_BUNDLE_CALIBRATOR_MISSING"),
    ("pregame_corpus", "MODEL_BUNDLE_PREGAME_CORPUS_MISSING"),
])
def test_missing_component_is_its_own_blocker(missing, typed):
    scope, artifact, calibrator, pregame = _valid()
    rows = {"artifact": artifact, "calibrator": calibrator, "pregame_corpus": pregame}
    rows[missing] = None
    result = assess_model_bundle(scope, **rows).as_dict()
    assert result["status"] == "BLOCKED"
    assert typed in result["blockers"]
    assert result["bundle_complete"] is False
    assert result["probability_publishable"] is False


@pytest.mark.parametrize("component,field,value", [
    ("artifact", "sport", "NFL"),
    ("artifact", "model_family", "OTHER_FAMILY"),
    ("artifact", "model_artifact_version", "OTHER_ARTIFACT"),
    ("artifact", "feature_schema_version", "OTHER_SCHEMA"),
    ("artifact", "artifact_checksum", "invalid"),
    ("artifact", "training_dataset_hash", None),
    ("artifact", "payload_checksum_verified", False),
    ("artifact", "training_provenance_verified", False),
    ("calibrator", "sport", "WNBA"),
    ("calibrator", "model_artifact_version", "OTHER_ARTIFACT"),
    ("calibrator", "model_artifact_checksum", "f"*64),
    ("calibrator", "calibrator_checksum", None),
    ("calibrator", "calibrated_lower_bound_verified", False),
    ("calibrator", "chronological_replay_verified", False),
    ("calibrator", "holdout_overlap_rows", 1),
    ("calibrator", "calibration_fit_end", "2026-11-13T00:00:00+00:00"),
    ("calibrator", "brier", float("nan")),
    ("pregame_corpus", "sport", "NFL"),
    ("pregame_corpus", "model_artifact_version", "OTHER_ARTIFACT"),
    ("pregame_corpus", "model_artifact_checksum", "f"*64),
    ("pregame_corpus", "feature_schema_version", "OTHER_SCHEMA"),
    ("pregame_corpus", "official_event_id", "OTHER_EVENT"),
    ("pregame_corpus", "source_payload_independently_verified", False),
    ("pregame_corpus", "feature_replay_verified", False),
    ("pregame_corpus", "official_event_identity_verified", False),
    ("pregame_corpus", "settlement_basis_verified", False),
    ("pregame_corpus", "source_snapshot_timestamp", "2026-11-12T19:10:00+00:00"),
    ("pregame_corpus", "feature_snapshot_timestamp", "2026-11-12T19:00:00+00:00"),
    ("pregame_corpus", "event_status", "STARTED"),
])
def test_mismatches_or_unverified_components_fail_closed(component, field, value):
    scope, artifact, calibrator, pregame = _valid()
    rows = {"artifact": artifact, "calibrator": calibrator, "pregame_corpus": pregame}
    rows[component][field] = value
    result = assess_model_bundle(scope, **rows).as_dict()
    assert "MODEL_BUNDLE_" + component.upper() + "_INVALID_OR_UNVERIFIED" in result["blockers"]
    assert result["bundle_complete"] is False
    assert result["rank_eligible"] is False


def test_material_update_after_features_invalidates_pregame_corpus():
    scope, artifact, calibrator, pregame = _valid()
    scope = replace(scope, latest_material_update_timestamp="2026-11-12T18:25:00+00:00")
    result = _assess(scope, artifact, calibrator, pregame)
    assert "MODEL_BUNDLE_PREGAME_CORPUS_INVALID_OR_UNVERIFIED" in result["blockers"]
    assert result["bundle_complete"] is False


def test_started_event_and_naive_timestamp_block_even_with_all_three_rows():
    scope, artifact, calibrator, pregame = _valid()
    scope = replace(scope, prediction_timestamp="2026-11-12T19:10:00+00:00")
    result = _assess(scope, artifact, calibrator, pregame)
    assert "MODEL_BUNDLE_EVENT_OR_PREDICTION_TIME_INVALID" in result["blockers"]
    assert result["bundle_complete"] is False

    scope = replace(scope, prediction_timestamp="2026-11-12T18:30:00")
    result = _assess(scope, artifact, calibrator, pregame)
    assert "MODEL_BUNDLE_EVENT_OR_PREDICTION_TIME_INVALID" in result["blockers"]
    assert result["bundle_complete"] is False


def test_unidentified_prop_family_is_unsupported_not_adjacent_line():
    scope, artifact, calibrator, pregame = _valid("PLAYER_PROP", "NBA", "POINTS")
    scope = replace(scope, market_family=None)
    result = _assess(scope, artifact, calibrator, pregame)
    assert "MODEL_BUNDLE_ROUTE_UNSUPPORTED" in result["blockers"]
    assert result["bundle_complete"] is False


def test_fabricated_ready_flags_do_not_override_missing_data():
    scope, artifact, calibrator, pregame = _valid()
    artifact = {"bundle_complete": True, "probability_publishable": True, "can_execute": True}
    result = _assess(scope, artifact, calibrator, pregame)
    assert "MODEL_BUNDLE_ARTIFACT_INVALID_OR_UNVERIFIED" in result["blockers"]
    assert result["status"] == "BLOCKED"
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False
