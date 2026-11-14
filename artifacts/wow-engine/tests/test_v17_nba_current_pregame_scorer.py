from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from basketball_team_event_specialist import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, MODEL_FAMILY
from v17.fitted_team_event_certification import TeamEventCertificationError
from v17.multisport_team_event_calibration import CalibrationArtifactInvalid
from v17.nba_current_pregame_scorer import (
    prepare_nba_current_pregame_scorer_receipt,
    resolve_nba_champion_artifact,
)
from v17.nba_event_identity import resolve_nba_current_event_identity
from v17.team_event_capability_manifest import CERTIFIED_TEAM_EVENT_SPORTS


EVENT_START = "2026-10-22T23:00:00Z"
FEATURE_TIMESTAMP = "2026-10-22T16:00:00Z"
TEST_NBA_SPECIALIST = "NBA_GAME_WIN_PROBABILITY_EXPERT_V1"


def _schedule(rows):
    return lambda _sport, _season: [dict(row) for row in rows]


def _identity_resolver(rows):
    return lambda **kwargs: resolve_nba_current_event_identity(
        **kwargs,
        fetcher=_schedule(rows),
    )


def _schedule_row(game_id="401999001", game_date="2026-10-22"):
    return {
        "game_id": game_id,
        "game_date": game_date,
        "home_id": "1",
        "away_id": "2",
    }


def _feature_package(event_id="espn-401999001"):
    return {
        "status": "PASS",
        "sport": "NBA",
        "official_event_id": event_id,
        "home_team_id": "espn-1",
        "away_team_id": "espn-2",
        "event_start_time_utc": EVENT_START,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "features": {name: 0.5 for name in FEATURE_NAMES},
        "source_snapshot_id": "snapshot-1",
        "source_snapshot_timestamp": "2026-10-22T15:55:00Z",
        "feature_audit": {
            "feature_as_of": "2026-10-22",
            "feature_timestamp": FEATURE_TIMESTAMP,
            "dataset_id": "nba-history-2026-10-22",
            "source_replay_status": "RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS",
            "timestamp_attestation": "VERIFIED",  # Synthetic test-only approval input
        },
    }


def _lineup(update_at="2026-10-22T15:59:00Z", rotation="CONFIRMED"):
    return {
        "injury_report": {"status": "CURRENT"},
        "expected_starters_rotation": rotation,
        "latest_material_update_timestamp": update_at,
    }


def _certification():
    certification = SimpleNamespace(
        certification_id="nba-cert-1",
        sport="NBA",
        league_scope="NBA",
        controlling_specialist=TEST_NBA_SPECIALIST,
        model_family=MODEL_FAMILY,
        model_version="NBA_MODEL_V1",
        artifact_id="nba-artifact-1",
        artifact_sha256="",
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        calibration_artifact_id="nba-calibrator-1",
        calibration_sha256="b" * 64,
        calibration_method="PLATT_TIME_SPLIT_V1",
        certification_status="CERTIFIED",
        independent_verification_status="PASS",
        can_execute=False,
    )
    certification.artifact_sha256 = _artifact_checksum(_artifact_payload(certification))
    return certification


def _artifact_payload(certification):
    payload = {
        "sport": "NBA",
        "model_family": MODEL_FAMILY,
        "model_artifact_version": certification.model_version,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_names": list(FEATURE_NAMES),
        "intercept": 0.0,
        "coefficients": [0.0] * len(FEATURE_NAMES),
        "means": [0.0] * len(FEATURE_NAMES),
        "scales": [1.0] * len(FEATURE_NAMES),
        "training_row_count": 300,
        "holdout_row_count": 100,
        "train_start_date": "2025-10-01",
        "train_end_date": "2026-01-01",
        "holdout_brier": 0.2,
        "holdout_log_loss": 0.6,
        "holdout_accuracy": 0.7,
    }
    return payload


def _artifact_checksum(payload):
    checksum_fields = (
        "sport",
        "model_family",
        "model_artifact_version",
        "feature_schema_version",
        "feature_names",
        "intercept",
        "coefficients",
        "means",
        "scales",
    )
    checksum_payload = {field: payload[field] for field in checksum_fields}
    return hashlib.sha256(
        json.dumps(checksum_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _champion_artifact(certification):
    return {
        "sport": "NBA",
        "model_family": MODEL_FAMILY,
        "model_artifact_version": certification.model_version,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "artifact_checksum": certification.artifact_sha256,
        "artifact_payload": _artifact_payload(certification),
    }


def _enable_certified_nba_test_lane(monkeypatch):
    monkeypatch.setitem(CERTIFIED_TEAM_EVENT_SPORTS, "NBA", TEST_NBA_SPECIALIST)


def test_current_season_identity_reaches_typed_feature_hold_without_publishing():
    result = prepare_nba_current_pregame_scorer_receipt(
        event_start_time=EVENT_START,
        home_team_alias="espn-1",
        away_team_alias="espn-2",
        current_feature_package=None,
        lineup_evidence=None,
        source_snapshot_id="snapshot-1",
        client=object(),
        identity_resolver=_identity_resolver([_schedule_row()]),
    )

    assert result["official_event_id"] == "espn-401999001"
    assert result["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert "NBA_CURRENT_PREGAME_FEATURE_HYDRATION_UNAVAILABLE" in result["blockers"]
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


def test_nba_without_approved_capability_manifest_entry_stays_unavailable():
    assert "NBA" not in CERTIFIED_TEAM_EVENT_SPORTS
    result = prepare_nba_current_pregame_scorer_receipt(
        event_start_time=EVENT_START,
        home_team_alias="espn-1",
        away_team_alias="espn-2",
        current_feature_package=_feature_package(),
        lineup_evidence=_lineup(),
        source_snapshot_id="snapshot-1",
        client=object(),
        identity_resolver=_identity_resolver([_schedule_row()]),
        certification_resolver=lambda *_args, **_kwargs: pytest.fail(
            "uncertified NBA capability must block before certification lookup"
        ),
    )

    assert result["code"] == "MODEL_UNAVAILABLE"
    assert result["blockers"] == ["NBA_CONTROLLING_SPECIALIST_NOT_CERTIFIED"]
    assert result["research_receipt"] is None
    assert result["raw_probability"] is None
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("controlling_specialist", "OTHER_NBA_SPECIALIST"),
        ("league_scope", "WNBA"),
    ],
)
def test_certification_must_match_approved_nba_specialist_and_scope(
    field,
    value,
    monkeypatch,
):
    _enable_certified_nba_test_lane(monkeypatch)
    certification = _certification()
    setattr(certification, field, value)
    result = prepare_nba_current_pregame_scorer_receipt(
        event_start_time=EVENT_START,
        home_team_alias="espn-1",
        away_team_alias="espn-2",
        current_feature_package=_feature_package(),
        lineup_evidence=_lineup(),
        source_snapshot_id="snapshot-1",
        client=object(),
        identity_resolver=_identity_resolver([_schedule_row()]),
        certification_resolver=lambda *_args, **_kwargs: certification,
        champion_artifact_resolver=lambda *_args: pytest.fail(
            "mismatched NBA certification must not resolve a model artifact"
        ),
    )

    assert result["code"] == "MODEL_UNAVAILABLE"
    assert result["blockers"] == [
        "NBA_CERTIFICATION_SPECIALIST_SCOPE_OR_MODEL_MISMATCH"
    ]
    assert result["research_receipt"] is None
    assert result["raw_probability"] is None
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_consecutive_date_same_pairing_identity_holds_instead_of_guessing():
    rows = [
        _schedule_row("401999001", "2026-10-22"),
        _schedule_row("401999002", "2026-10-23"),
    ]
    result = prepare_nba_current_pregame_scorer_receipt(
        event_start_time="2026-10-23T00:00:00Z",
        home_team_alias="espn-1",
        away_team_alias="espn-2",
        current_feature_package=None,
        lineup_evidence=None,
        source_snapshot_id="snapshot-1",
        client=object(),
        identity_resolver=_identity_resolver(rows),
    )

    assert result["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert result["blockers"] == ["NBA_CANONICAL_EVENT_AMBIGUOUS"]
    assert result["official_event_id"] is None
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_changed_starter_after_feature_snapshot_holds_before_model_resolution():
    result = prepare_nba_current_pregame_scorer_receipt(
        event_start_time=EVENT_START,
        home_team_alias="espn-1",
        away_team_alias="espn-2",
        current_feature_package=_feature_package(),
        lineup_evidence=_lineup(
            update_at="2026-10-22T16:01:00Z",
            rotation="CHANGED",
        ),
        source_snapshot_id="snapshot-1",
        client=object(),
        identity_resolver=_identity_resolver([_schedule_row()]),
        certification_resolver=lambda *_args, **_kwargs: pytest.fail(
            "changed starters must block before model resolution"
        ),
    )

    assert result["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert result["blockers"] == ["NBA_STARTER_CHANGED_AFTER_FEATURE_HYDRATION"]
    assert result["raw_probability"] is None
    assert result["calibrated_lower_bound"] is None
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_refreshed_features_after_starter_change_hold_without_bound_rotation_receipt():
    certification = _certification()
    package = _feature_package()
    package["feature_audit"]["feature_timestamp"] = "2026-10-22T16:02:00Z"
    result = prepare_nba_current_pregame_scorer_receipt(
        event_start_time=EVENT_START,
        home_team_alias="espn-1",
        away_team_alias="espn-2",
        current_feature_package=package,
        lineup_evidence=_lineup(
            update_at="2026-10-22T16:01:00Z",
            rotation="CHANGED",
        ),
        source_snapshot_id="snapshot-1",
        client=object(),
        identity_resolver=_identity_resolver([_schedule_row()]),
        certification_resolver=lambda *_args, **_kwargs: certification,
        champion_artifact_resolver=lambda _client, cert: _champion_artifact(cert),
    )

    assert result["blockers"] == ["NBA_CHANGED_STARTERS_RECEIPT_UNVERIFIED"]
    assert result["raw_probability"] is None
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


@pytest.mark.parametrize(
    ("field", "value", "expected_blocker"),
    [
        (
            "source_snapshot_timestamp",
            "2026-10-22T23:01:00Z",
            "NBA_SOURCE_SNAPSHOT_AFTER_KICKOFF",
        ),
        (
            "source_snapshot_timestamp",
            "2026-10-22T16:01:00Z",
            "NBA_SOURCE_SNAPSHOT_AFTER_FEATURE",
        ),
        (
            "feature_timestamp",
            "2026-10-22T23:00:00Z",
            "NBA_FEATURE_TIMESTAMP_INVALID_FOR_PREGAME",
        ),
        (
            "feature_as_of",
            "2026-10-23",
            "NBA_FEATURE_AS_OF_AFTER_CUTOFF",
        ),
        (
            "feature_as_of",
            "2026-10-22T15:56:00Z",
            "NBA_FEATURE_AS_OF_AFTER_CUTOFF",
        ),
        (
            "feature_as_of",
            "2026-10-22T15:00:00",
            "NBA_FEATURE_AS_OF_INVALID",
        ),
    ],
)
def test_feature_lineage_rejects_future_or_misordered_timestamps(field, value, expected_blocker):
    package = _feature_package()
    if field == "feature_timestamp":
        package["feature_audit"][field] = value
    elif field == "feature_as_of":
        package["feature_audit"][field] = value
    else:
        package[field] = value

    result = prepare_nba_current_pregame_scorer_receipt(
        event_start_time=EVENT_START,
        home_team_alias="espn-1",
        away_team_alias="espn-2",
        current_feature_package=package,
        lineup_evidence=_lineup(),
        source_snapshot_id="snapshot-1",
        client=object(),
        identity_resolver=_identity_resolver([_schedule_row()]),
        certification_resolver=lambda *_args, **_kwargs: pytest.fail(
            "invalid evidence chronology must block before model resolution"
        ),
    )

    assert result["blockers"] == [expected_blocker]
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


@pytest.mark.parametrize(
    ("field", "expected_blocker"),
    [
        ("source_snapshot_id", "NBA_SOURCE_SNAPSHOT_ID_MISMATCH"),
        ("source_snapshot_timestamp", "NBA_SOURCE_SNAPSHOT_PROVENANCE_MISSING"),
        ("dataset_id", "NBA_FEATURE_DATASET_ID_MISSING"),
        ("feature_timestamp", "NBA_FEATURE_TIMESTAMP_MISSING"),
    ],
)
def test_current_feature_hydration_requires_bound_timestamped_lineage(field, expected_blocker):
    package = _feature_package()
    if field in {"source_snapshot_id", "source_snapshot_timestamp"}:
        package.pop(field)
    else:
        package["feature_audit"].pop(field)

    result = prepare_nba_current_pregame_scorer_receipt(
        event_start_time=EVENT_START,
        home_team_alias="espn-1",
        away_team_alias="espn-2",
        current_feature_package=package,
        lineup_evidence=_lineup(),
        source_snapshot_id="snapshot-1",
        client=object(),
        identity_resolver=_identity_resolver([_schedule_row()]),
    )

    assert result["blockers"] == [expected_blocker]
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_certified_artifact_without_current_calibrator_resolver_remains_typed_hold(monkeypatch):
    _enable_certified_nba_test_lane(monkeypatch)
    certification = _certification()
    result = prepare_nba_current_pregame_scorer_receipt(
        event_start_time=EVENT_START,
        home_team_alias="espn-1",
        away_team_alias="espn-2",
        current_feature_package=_feature_package(),
        lineup_evidence=_lineup(),
        source_snapshot_id="snapshot-1",
        client=object(),
        identity_resolver=_identity_resolver([_schedule_row()]),
        certification_resolver=lambda *_args, **_kwargs: certification,
        champion_artifact_resolver=lambda _client, cert: _champion_artifact(cert),
    )

    assert result["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert result["blockers"] == ["NBA_CALIBRATOR_PAYLOAD_RESOLVER_UNAVAILABLE"]
    assert result["certification_id"] == "nba-cert-1"
    assert result["model_artifact_id"] == "nba-artifact-1"
    assert result["model_artifact_version"] == "NBA_MODEL_V1"
    assert result["calibration_artifact_id"] == "nba-calibrator-1"
    assert result["calibration_artifact_sha256"] == "b" * 64
    assert result["calibrated_probability"] is None
    assert result["research_receipt"]["status"] == "RESEARCH_ONLY_NOT_FOR_PUBLICATION"
    assert result["research_receipt"]["raw_probability"] == pytest.approx(0.5)
    assert result["research_receipt"]["source_snapshot_timestamp"] == "2026-10-22T15:55:00Z"
    assert result["research_receipt"]["feature_timestamp"] == FEATURE_TIMESTAMP
    assert result["research_receipt"]["dataset_id"] == "nba-history-2026-10-22"
    assert result["research_receipt"]["model_artifact_id"] == "nba-artifact-1"
    assert result["research_receipt"]["calibration_artifact_id"] == "nba-calibrator-1"
    assert result["research_receipt"]["probability_publishable"] is False
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


@pytest.mark.parametrize(
    ("source_timestamp", "feature_timestamp", "feature_as_of"),
    [
        (
            "2026-10-22T15:55:00Z",
            FEATURE_TIMESTAMP,
            "2026-10-22",
        ),
        (
            "2026-10-22T15:55:00Z",
            FEATURE_TIMESTAMP,
            "2026-10-22T16:55:00+01:00",
        ),
        (
            FEATURE_TIMESTAMP,
            FEATURE_TIMESTAMP,
            "2026-10-22T15:55:00Z",
        ),
    ],
)
def test_valid_date_and_timestamp_as_of_boundaries_reach_research_only_hold(
    source_timestamp,
    feature_timestamp,
    feature_as_of,
    monkeypatch,
):
    _enable_certified_nba_test_lane(monkeypatch)
    certification = _certification()
    package = _feature_package()
    package["source_snapshot_timestamp"] = source_timestamp
    package["feature_audit"]["feature_timestamp"] = feature_timestamp
    package["feature_audit"]["feature_as_of"] = feature_as_of
    result = prepare_nba_current_pregame_scorer_receipt(
        event_start_time=EVENT_START,
        home_team_alias="espn-1",
        away_team_alias="espn-2",
        current_feature_package=package,
        lineup_evidence=_lineup(),
        source_snapshot_id="snapshot-1",
        client=object(),
        identity_resolver=_identity_resolver([_schedule_row()]),
        certification_resolver=lambda *_args, **_kwargs: certification,
        champion_artifact_resolver=lambda _client, cert: _champion_artifact(cert),
    )

    assert result["blockers"] == ["NBA_CALIBRATOR_PAYLOAD_RESOLVER_UNAVAILABLE"]
    assert result["research_receipt"]["raw_probability"] == pytest.approx(0.5)
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_resolved_calibrator_still_holds_until_nba_bounds_policy_is_approved(monkeypatch):
    _enable_certified_nba_test_lane(monkeypatch)
    certification = _certification()
    result = prepare_nba_current_pregame_scorer_receipt(
        event_start_time=EVENT_START,
        home_team_alias="espn-1",
        away_team_alias="espn-2",
        current_feature_package=_feature_package(),
        lineup_evidence=_lineup(),
        source_snapshot_id="snapshot-1",
        client=object(),
        identity_resolver=_identity_resolver([_schedule_row()]),
        certification_resolver=lambda *_args, **_kwargs: certification,
        champion_artifact_resolver=lambda _client, cert: _champion_artifact(cert),
        calibration_artifact_resolver=lambda _client, _cert: {
            "calibration_version": "NBA_PLATT_REVIEW_REQUIRED"
        },
    )

    assert result["blockers"] == ["NBA_DYNAMIC_BOUNDS_POLICY_NOT_APPROVED"]
    assert result["calibration_version"] == "NBA_PLATT_REVIEW_REQUIRED"
    assert result["raw_probability"] is None
    assert result["calibrated_probability"] is None
    assert result["calibrated_lower_bound"] is None
    assert result["calibrated_upper_bound"] is None
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


def test_tampered_certified_model_payload_cannot_emit_research_score(monkeypatch):
    _enable_certified_nba_test_lane(monkeypatch)
    certification = _certification()
    artifact = _champion_artifact(certification)
    artifact["artifact_payload"]["coefficients"][0] = 0.5
    result = prepare_nba_current_pregame_scorer_receipt(
        event_start_time=EVENT_START,
        home_team_alias="espn-1",
        away_team_alias="espn-2",
        current_feature_package=_feature_package(),
        lineup_evidence=_lineup(),
        source_snapshot_id="snapshot-1",
        client=object(),
        identity_resolver=_identity_resolver([_schedule_row()]),
        certification_resolver=lambda *_args, **_kwargs: certification,
        champion_artifact_resolver=lambda _client, _cert: artifact,
    )

    assert result["code"] == "MODEL_OUTPUT_INVALID"
    assert result["blockers"] == ["NBA_CERTIFIED_MODEL_ARTIFACT_PAYLOAD_MISMATCH"]
    assert result["research_receipt"] is None
    assert result["raw_probability"] is None
    assert result["calibrated_lower_bound"] is None
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


@pytest.mark.parametrize(
    ("resolver_error", "expected_code", "expected_blocker"),
    [
        (
            CalibrationArtifactInvalid(["CALIBRATION_CERTIFICATION_NOT_PASS"]),
            "MODEL_INPUTS_INSUFFICIENT",
            "CALIBRATION_CERTIFICATION_NOT_PASS",
        ),
        (
            ValueError("malformed calibration response"),
            "MODEL_OUTPUT_INVALID",
            "NBA_CERTIFIED_CALIBRATOR_INVALID",
        ),
    ],
)
def test_invalid_calibration_resolution_preserves_failure_class(
    resolver_error,
    expected_code,
    expected_blocker,
    monkeypatch,
):
    _enable_certified_nba_test_lane(monkeypatch)
    certification = _certification()
    result = prepare_nba_current_pregame_scorer_receipt(
        event_start_time=EVENT_START,
        home_team_alias="espn-1",
        away_team_alias="espn-2",
        current_feature_package=_feature_package(),
        lineup_evidence=_lineup(),
        source_snapshot_id="snapshot-1",
        client=object(),
        identity_resolver=_identity_resolver([_schedule_row()]),
        certification_resolver=lambda *_args, **_kwargs: certification,
        champion_artifact_resolver=lambda _client, cert: _champion_artifact(cert),
        calibration_artifact_resolver=lambda *_args: _raise_calibration_error(
            resolver_error
        ),
    )

    assert result["code"] == expected_code
    assert result["typed_failure"] == expected_code
    assert result["blockers"] == [expected_blocker]
    assert result["raw_probability"] is None
    assert result["calibrated_probability"] is None
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


class _Response:
    def __init__(self, data):
        self.data = data


class _Rpc:
    def __init__(self, data):
        self.data = data

    def execute(self):
        return _Response(self.data)


class _Client:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return _Rpc(self.data)


def test_champion_resolver_binds_exact_nba_certification_and_rpc_identity():
    certification = _certification()
    row = {
        "sport": "NBA",
        "model_family": MODEL_FAMILY,
        "model_artifact_version": certification.model_version,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "artifact_checksum": certification.artifact_sha256,
        "artifact_payload": {"sport": "NBA"},
    }
    client = _Client([row])

    resolved = resolve_nba_champion_artifact(client, certification)

    assert resolved == row
    assert client.calls == [
        (
            "wow_basketball_certified_model_artifact",
            {"p_sport": "NBA"},
        )
    ]


def test_champion_resolver_rejects_other_artifact_checksum():
    certification = _certification()
    client = _Client(
        [
            {
                "sport": "NBA",
                "model_family": MODEL_FAMILY,
                "model_artifact_version": certification.model_version,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "artifact_checksum": "c" * 64,
                "artifact_payload": {"sport": "NBA"},
            }
        ]
    )

    with pytest.raises(ValueError, match="NBA_CERTIFIED_MODEL_ARTIFACT_CERTIFICATION_MISMATCH"):
        resolve_nba_champion_artifact(client, certification)


def test_uncertified_artifact_is_not_converted_to_scorer_failure(monkeypatch):
    _enable_certified_nba_test_lane(monkeypatch)
    result = prepare_nba_current_pregame_scorer_receipt(
        event_start_time=EVENT_START,
        home_team_alias="espn-1",
        away_team_alias="espn-2",
        current_feature_package=_feature_package(),
        lineup_evidence=_lineup(),
        source_snapshot_id="snapshot-1",
        client=object(),
        identity_resolver=_identity_resolver([_schedule_row()]),
        certification_resolver=lambda *_args, **_kwargs: (
            _raise_not_certified()
        ),
    )

    assert result["code"] == "MODEL_UNAVAILABLE"
    assert result["typed_failure"] == "MODEL_UNAVAILABLE"
    assert result["blockers"] == ["TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED"]
    assert result["can_execute"] is False


@pytest.mark.parametrize(
    ("error_code", "expected_failure"),
    [
        ("TEAM_EVENT_CERTIFICATION_REGISTRY_UNAVAILABLE", "MODEL_SCORER_FAILED"),
        ("TEAM_EVENT_CERTIFICATION_REGISTRY_INVALID_RESPONSE", "MODEL_OUTPUT_INVALID"),
    ],
)
def test_certification_resolution_failures_keep_their_typed_class(
    error_code,
    expected_failure,
    monkeypatch,
):
    _enable_certified_nba_test_lane(monkeypatch)
    result = prepare_nba_current_pregame_scorer_receipt(
        event_start_time=EVENT_START,
        home_team_alias="espn-1",
        away_team_alias="espn-2",
        current_feature_package=_feature_package(),
        lineup_evidence=_lineup(),
        source_snapshot_id="snapshot-1",
        client=object(),
        identity_resolver=_identity_resolver([_schedule_row()]),
        certification_resolver=lambda *_args, **_kwargs: (
            _raise_certification_error(error_code)
        ),
    )

    assert result["typed_failure"] == expected_failure
    assert result["code"] == expected_failure
    assert result["blockers"] == [error_code]
    assert result["can_execute"] is False


def _raise_not_certified():
    raise TeamEventCertificationError(
        "TEAM_EVENT_SPECIALIST_ARTIFACT_NOT_CERTIFIED",
        "NBA",
    )


def _raise_certification_error(code):
    raise TeamEventCertificationError(code, "NBA")


def _raise_calibration_error(error):
    raise error


@pytest.mark.parametrize(("source_status", "timestamp_status", "expected"), [
    ("MISSING", "VERIFIED", "NBA_SOURCE_EVENT_REPLAY_NOT_VERIFIED"),
    ("RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS", "NOT_ESTABLISHED", "NBA_SOURCE_TIMESTAMP_ATTESTATION_NOT_VERIFIED"),
    ("RAW_CAPTURE_HASH_AND_EVENT_REPLAY_PASS", None, "NBA_SOURCE_TIMESTAMP_ATTESTATION_NOT_VERIFIED"),
])
def test_research_only_source_clock_cannot_reach_certified_model_even_when_manifest_enabled(
    monkeypatch, source_status, timestamp_status, expected
):
    _enable_certified_nba_test_lane(monkeypatch)
    feature = _feature_package()
    feature["feature_audit"]["source_replay_status"] = source_status
    feature["feature_audit"]["timestamp_attestation"] = timestamp_status
    receipt = prepare_nba_current_pregame_scorer_receipt(
        event_start_time=EVENT_START,
        home_team_alias="espn-1",
        away_team_alias="espn-2",
        current_feature_package=feature,
        lineup_evidence=_lineup(),
        source_snapshot_id="snapshot-1",
        client=object(),
        identity_resolver=_identity_resolver([_schedule_row()]),
        certification_resolver=lambda *_args, **_kwargs: pytest.fail(
            "unverified raw source cannot invoke even a test-certified model"
        ),
    )
    assert receipt["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert receipt["blockers"] == [expected]
    assert receipt["raw_probability"] is None
    assert receipt["calibrated_probability"] is None
    assert receipt["probability_publishable"] is False
    assert receipt["rank_eligible"] is False
    assert receipt["can_execute"] is False
