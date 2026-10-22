from __future__ import annotations

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


EVENT_START = "2026-10-22T23:00:00Z"
FEATURE_TIMESTAMP = "2026-10-22T16:00:00Z"


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
        },
    }


def _lineup(update_at="2026-10-22T15:59:00Z", rotation="CONFIRMED"):
    return {
        "injury_report": {"status": "CURRENT"},
        "expected_starters_rotation": rotation,
        "latest_material_update_timestamp": update_at,
    }


def _certification():
    return SimpleNamespace(
        certification_id="nba-cert-1",
        sport="NBA",
        league_scope="NBA",
        controlling_specialist="NBA_GAME_WIN_PROBABILITY_EXPERT_V1",
        model_family=MODEL_FAMILY,
        model_version="NBA_MODEL_V1",
        artifact_id="nba-artifact-1",
        artifact_sha256="a" * 64,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        calibration_artifact_id="nba-calibrator-1",
        calibration_sha256="b" * 64,
        calibration_method="PLATT_TIME_SPLIT_V1",
        certification_status="CERTIFIED",
        independent_verification_status="PASS",
        can_execute=False,
    )


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


def test_refreshed_features_after_starter_change_pass_freshness_gate():
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
        champion_artifact_resolver=lambda _client, _cert: {
            "model_artifact_version": certification.model_version
        },
    )

    assert result["blockers"] == ["NBA_CALIBRATOR_PAYLOAD_RESOLVER_UNAVAILABLE"]
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


def test_certified_artifact_without_current_calibrator_resolver_remains_typed_hold():
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
        champion_artifact_resolver=lambda _client, _cert: {
            "model_artifact_version": certification.model_version
        },
    )

    assert result["code"] == "MODEL_INPUTS_INSUFFICIENT"
    assert result["blockers"] == ["NBA_CALIBRATOR_PAYLOAD_RESOLVER_UNAVAILABLE"]
    assert result["certification_id"] == "nba-cert-1"
    assert result["model_artifact_id"] == "nba-artifact-1"
    assert result["model_artifact_version"] == "NBA_MODEL_V1"
    assert result["calibration_artifact_id"] == "nba-calibrator-1"
    assert result["calibration_artifact_sha256"] == "b" * 64
    assert result["calibrated_probability"] is None
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


def test_resolved_calibrator_still_holds_until_nba_bounds_policy_is_approved():
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
        champion_artifact_resolver=lambda _client, _cert: {
            "model_artifact_version": certification.model_version
        },
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
):
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
        champion_artifact_resolver=lambda _client, _cert: {
            "model_artifact_version": certification.model_version
        },
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


def test_uncertified_artifact_is_not_converted_to_scorer_failure():
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
):
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
