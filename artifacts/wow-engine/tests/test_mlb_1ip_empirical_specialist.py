import pytest

from mlb_1ip_artifact_pipeline import TrainingRow
from mlb_1ip_empirical_pmf import fit_empirical_pmf
from mlb_1ip_empirical_specialist import score_mlb_1ip_empirical


def _artifact():
    rows = []
    rows.extend(TrainingRow(bf=3, pitches=12 + i % 4) for i in range(450))
    rows.extend(TrainingRow(bf=4, pitches=16 + i % 5) for i in range(400))
    rows.extend(TrainingRow(bf=5, pitches=21 + i % 6) for i in range(300))
    payload = fit_empirical_pmf(rows)
    return {
        "ok": True,
        "code": "PROP_CERTIFIED_MODEL_ARTIFACT_READY",
        "model_family": payload["model_family"],
        "model_artifact_version": "MLB_1IP_TEST_ARTIFACT_V1",
        "artifact_checksum": payload["artifact_checksum"],
        "certification_id": "PROP-CERT-TEST-MLB-1IP",
        "artifact_payload": payload,
        "supported_line_min": 11.5,
        "supported_line_max": 21.5,
        "feature_schema_version": "PROP_FEATURES_V1",
        "probability_publishable": False,
        "can_execute": False,
    }


def _batters(n=4):
    return [
        {"player": f"Batter {i}", "handedness": "R", "p_pa_vs_pitcher_profile": 4.0}
        for i in range(n)
    ]


def test_official_lineup_empirical_specialist_returns_probability_package():
    result = score_mlb_1ip_empirical(
        artifact_record=_artifact(),
        starter_status="CONFIRMED",
        official_lineup_status="CONFIRMED",
        projected_top_four=[],
        line_value=15.5,
        side="MORE",
    )
    assert result["model_evaluated"] is True
    assert result["model_family"] == "MLB_1IP_CONDITIONAL_TOTAL_PITCH_PMF_V1"
    assert 0 < result["raw_probability"] < 1
    assert result["calibrated_probability"] == result["raw_probability"]
    assert result["calibrated_probability_lower_bound"] <= result["calibrated_probability"]
    assert result["calibrated_probability_upper_bound"] >= result["calibrated_probability"]
    assert result["calibration_method"] == "MLB_1IP_EMPIRICAL_TEMPORAL_CAL_V1"
    assert result["final_refresh_required"] is False
    assert result["probability_publishable"] is False
    assert result["can_execute"] is False


def test_projected_lineup_empirical_specialist_stays_hold_and_requires_refresh():
    result = score_mlb_1ip_empirical(
        artifact_record=_artifact(),
        starter_status="CONFIRMED",
        official_lineup_status="TBD",
        projected_top_four=_batters(4),
        line_value=17.5,
        side="LESS",
    )
    assert result["model_evaluated"] is True
    assert result["lineup_evidence_state"] == "PROJECTED_OR_RECONSTRUCTED"
    assert result["terminal_label"] == "MODEL_QUALIFIED_HOLD"
    assert result["final_refresh_required"] is True
    assert result["probability_publishable"] is False


def test_empirical_specialist_blocks_unsupported_line_without_probability():
    result = score_mlb_1ip_empirical(
        artifact_record=_artifact(),
        starter_status="CONFIRMED",
        official_lineup_status="CONFIRMED",
        projected_top_four=[],
        line_value=25.5,
        side="MORE",
    )
    assert result["model_evaluated"] is False
    assert result["terminal_label"] == "REJECT_OOD"
    assert result["code"] == "MLB_1IP_LINE_OUTSIDE_CERTIFIED_SUPPORT"
    assert "raw_probability" not in result
    assert result["probability_publishable"] is False


def test_empirical_specialist_rejects_wrong_artifact_family():
    artifact = _artifact()
    artifact["model_family"] = "WRONG"
    with pytest.raises(ValueError, match="MLB_1IP_CERTIFIED_ARTIFACT_FAMILY_INVALID"):
        score_mlb_1ip_empirical(
            artifact_record=artifact,
            starter_status="CONFIRMED",
            official_lineup_status="CONFIRMED",
            projected_top_four=[],
            line_value=15.5,
            side="MORE",
        )
