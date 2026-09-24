from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from mlb_event_specialist_v16 import ProspectiveModelUnavailable
from mlb_event_prospective_runtime import _typed_prospective_failure
from v17 import mlb_event_bridge_repair as bridge
from v17.mlb_prospective_failure_taxonomy import (
    MODEL_INPUTS_INSUFFICIENT,
    MODEL_OUTPUT_INVALID,
    MODEL_SCORER_FAILED,
    MODEL_UNAVAILABLE,
    classify_mlb_prospective_failure,
)


def test_input_contract_failure_is_not_collapsed_to_model_unavailable():
    exc = ProspectiveModelUnavailable("feature_vector_mismatch")
    failure = classify_mlb_prospective_failure(exc)
    assert failure.code == MODEL_INPUTS_INSUFFICIENT
    assert failure.status_code == 422
    assert failure.can_execute is False

    routed = _typed_prospective_failure(exc)
    assert routed.status_code == 422
    assert routed.detail["code"] == MODEL_INPUTS_INSUFFICIENT
    assert routed.detail["reason"] == "feature_vector_mismatch"
    assert routed.detail["rank_eligible"] is False
    assert routed.detail["probability_publishable"] is False
    assert routed.detail["can_execute"] is False


@pytest.mark.parametrize(
    ("reason", "expected_code", "expected_status"),
    [
        ("prospective_bounds_invalid", MODEL_OUTPUT_INVALID, 409),
        ("immutable_specialist_snapshot_write_failed", MODEL_SCORER_FAILED, 503),
        ("prospective_certified_artifact_missing", MODEL_UNAVAILABLE, 409),
        ("calibration_health_not_pass", MODEL_INPUTS_INSUFFICIENT, 422),
        ("confirmed_strict_pregame_lineup_missing", MODEL_INPUTS_INSUFFICIENT, 422),
        ("prospective_path_requires_held_fitted_baseline", MODEL_INPUTS_INSUFFICIENT, 422),
        ("wow_mlb_forward_score_snapshots:required_row_missing", MODEL_INPUTS_INSUFFICIENT, 422),
        ("wow_mlb_v2d_intercept_calibration:required_row_missing", MODEL_INPUTS_INSUFFICIENT, 422),
        ("wow_mlb_v2b_distribution_state:required_row_missing", MODEL_UNAVAILABLE, 409),
    ],
)
def test_known_prospective_failures_map_to_v17_taxonomy(reason, expected_code, expected_status):
    failure = classify_mlb_prospective_failure(ProspectiveModelUnavailable(reason))
    assert (failure.code, failure.status_code) == (expected_code, expected_status)
    assert failure.can_execute is False


def test_calibration_rejection_preserves_established_blocker_contract():
    for reason in (
        "calibration_health_not_pass",
        "wow_mlb_v2d_calibration_health:required_row_missing",
        "wow_mlb_v2d_intercept_calibration:required_row_missing",
    ):
        failure = classify_mlb_prospective_failure(ProspectiveModelUnavailable(reason))
        assert failure.code == MODEL_INPUTS_INSUFFICIENT
        assert failure.status_code == 422
        assert failure.blocker_code == "CALIBRATION_ARTIFACT_INVALID_OR_UNAVAILABLE"
        assert failure.can_execute is False


def test_unknown_invoked_scorer_exception_fails_as_scorer_not_unavailable():
    failure = classify_mlb_prospective_failure(RuntimeError("unexpected_numeric_runtime"))
    assert failure.code == MODEL_SCORER_FAILED
    assert failure.status_code == 503
    assert failure.code != MODEL_UNAVAILABLE


def test_team_event_bridge_uses_same_classifier_for_specialist_input_failure(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "team_event_capability",
        lambda sport: SimpleNamespace(
            status="AVAILABLE",
            controlling_specialist="wow.mlb-game-win-probability-expert",
            blocker=None,
        ),
    )
    monkeypatch.setattr(
        bridge,
        "_resolve_bridge_payload",
        lambda req, event_api: {
            "ok": True,
            "bridge_payload": {"code": "REAL_FITTED_MODEL_PATH_PROVEN"},
            "client": object(),
            "source_snapshot_id": "snapshot-1",
            "source_snapshot_timestamp": "2026-09-24T15:00:00+00:00",
            "latest_material_update_timestamp": "2026-09-24T15:00:00+00:00",
        },
    )

    def scorer(**kwargs):
        raise ProspectiveModelUnavailable("feature_vector_mismatch")

    req = SimpleNamespace(source_snapshot_id="snapshot-1")
    with pytest.raises(HTTPException) as caught:
        bridge.score_event_v17_bridge(req, event_api=object(), scorer_override=scorer)

    assert caught.value.status_code == 422
    detail = caught.value.detail
    assert detail["code"] == MODEL_INPUTS_INSUFFICIENT
    assert detail["blocker_code"] == "SPORT_SPECIFIC_MODEL_INPUTS_INSUFFICIENT"
    assert detail["scorer_error_code"] == "feature_vector_mismatch"
    assert detail["sport_model_invoked"] is True
    assert detail["rank_eligible"] is False
    assert detail["probability_publishable"] is False
    assert detail["can_execute"] is False


def test_team_event_bridge_preserves_output_scorer_unavailable_and_calibration_taxonomy(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "team_event_capability",
        lambda sport: SimpleNamespace(
            status="AVAILABLE",
            controlling_specialist="wow.mlb-game-win-probability-expert",
            blocker=None,
        ),
    )
    monkeypatch.setattr(
        bridge,
        "_resolve_bridge_payload",
        lambda req, event_api: {
            "ok": True,
            "bridge_payload": {"code": "REAL_FITTED_MODEL_PATH_PROVEN"},
            "client": object(),
            "source_snapshot_id": "snapshot-1",
            "source_snapshot_timestamp": "2026-09-24T15:00:00+00:00",
            "latest_material_update_timestamp": "2026-09-24T15:00:00+00:00",
        },
    )
    req = SimpleNamespace(source_snapshot_id="snapshot-1")

    for reason, expected_code, expected_status, expected_blocker in (
        ("prospective_bounds_invalid", MODEL_OUTPUT_INVALID, 409, "SCORED_MODEL_VALIDATION_FAILED"),
        ("immutable_specialist_snapshot_write_failed", MODEL_SCORER_FAILED, 503, "EVENT_MODEL_BRIDGE_UNAVAILABLE"),
        ("prospective_certified_artifact_missing", MODEL_UNAVAILABLE, 409, "MLB_EVENT_MODEL_NOT_AVAILABLE"),
        ("calibration_health_not_pass", MODEL_INPUTS_INSUFFICIENT, 422, "CALIBRATION_ARTIFACT_INVALID_OR_UNAVAILABLE"),
    ):
        def scorer(**kwargs):
            raise ProspectiveModelUnavailable(reason)

        with pytest.raises(HTTPException) as caught:
            bridge.score_event_v17_bridge(req, event_api=object(), scorer_override=scorer)
        assert caught.value.status_code == expected_status
        assert caught.value.detail["code"] == expected_code
        assert caught.value.detail["blocker_code"] == expected_blocker
        assert caught.value.detail["can_execute"] is False


def test_valid_probability_math_source_is_untouched_by_taxonomy_patch():
    root = Path(__file__).resolve().parents[1]
    specialist = (root / "mlb_event_specialist_v16.py").read_text()
    assert "def _simulate(" in specialist
    assert "def _bounds(" in specialist
    assert "class ProspectiveModelUnavailable" in specialist
    assert "classify_mlb_prospective_failure" not in specialist
