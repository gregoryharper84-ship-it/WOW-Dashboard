import v17.prop_lifecycle_artifact_identity_overlay as overlay
import v17.prop_lifecycle_autopilot as base
import v17.prop_lifecycle_autopilot_runtime as runtime


TOKEN = "MLB:PITCHER_STRIKEOUTS"


def _forward():
    return {
        "route_results": [{
            "sport": "MLB",
            "stat_type": "PITCHER_STRIKEOUTS",
            "collector": "MLB_STRIKEOUT_FORWARD_COHORT",
            "status": "PASS",
            "calibration_readiness": {
                "forward_prediction_n": 630,
                "forward_settled_n": 630,
                "artifact_cohorts": [{
                    "feature_schema_version": "PROP_FEATURES_V1",
                    "model_family": "MLB_PITCHER_SO_FAILURE_PATH_NB_V1",
                    "model_artifact_version": "MLB_PITCHER_SO_FAILURE_PATH_NB_V1_2026_08_29",
                    "model_artifact_checksum": "sha256:exact",
                    "calibration_version": "MLB_PITCHER_SO_CAL_V1",
                    "forward_prediction_n": 630,
                    "forward_settled_n": 630,
                    "forward_unsettled_n": 0,
                    "counting_basis": "UNIQUE_EVENT_PLAYER_STAT_LINE_THESIS_WITHIN_EXACT_ARTIFACT",
                }],
            },
        }]
    }


def _certification():
    return {
        "artifact_rows": [{
            "sport": "MLB",
            "stat_type": "PITCHER_STRIKEOUTS",
            "feature_schema_version": "PROP_FEATURES_V1",
            "model_family": "MLB_PITCHER_SO_FAILURE_PATH_NB_V1",
            "model_artifact_version": "MLB_PITCHER_SO_FAILURE_PATH_NB_V1_2026_08_29",
            "artifact_checksum": "sha256:exact",
            "calibrator_version": "MLB_PITCHER_SO_CAL_V1",
            "status": "CALIBRATION_EVIDENCE_REQUIRED",
            "policy_id": "MLB_SO_FORWARD_CERT_POLICY_V1",
            "policy_review_status": "REVIEW_REQUIRED",
            "evidence_hash": "cert-evidence",
            "promotion_package_ready": False,
            "independent_settled_thesis_n": 630,
            "metrics": {"n": 630},
            "blockers": ["PHASE_A_PRECALIBRATION_NOT_FORWARD_CERTIFICATION"],
        }]
    }


def _registration():
    return {
        "route_rows": [{
            "sport": "MLB",
            "stat_type": "PITCHER_STRIKEOUTS",
            "status": "CALIBRATION_EVIDENCE_REQUIRED",
            "model_family": "MLB_PITCHER_SO_FAILURE_PATH_NB_V1",
            "model_artifact_version": "MLB_PITCHER_SO_FAILURE_PATH_NB_V1_2026_08_29",
            "artifact_checksum": "sha256:exact",
            "calibrator_version": "MLB_PITCHER_SO_CAL_V1",
            "production_numerical_authority": False,
            "model_adapter_registered": True,
            "calibrator_adapter_registered": True,
            "hydration_route_registered": True,
            "action_canary_verified": False,
            "blockers": ["FORWARD_CALIBRATION_CERTIFICATION_REQUIRED"],
            "action_canary_blockers": ["CANONICAL_ACTION_CANARY_REQUIRED"],
        }]
    }


def _dashboard():
    return base.build_lifecycle_dashboard(
        forward=_forward(),
        settlement={"route_dispositions": [], "results": []},
        certification=_certification(),
        registration=_registration(),
        requested_tokens=[TOKEN],
        captured_at="2026-09-28T13:00:00+00:00",
    )


def test_overlay_preserves_schema_and_restores_exact_forward_and_candidate_joins():
    dashboard = _dashboard()
    overlay.preserve_artifact_feature_schema_identity(dashboard, _certification())
    row = dashboard["artifact_cohort_rows"][0]
    assert row["feature_schema_version"] == "PROP_FEATURES_V1"

    result = {"forward_capture": {"result": _forward()}, "dashboard": dashboard}
    runtime.enrich_artifact_cohort_health(result)
    assert row["cohort_identity_match"] is True
    assert row["forward_prediction_n"] == 630
    assert row["forward_settled_n"] == 630

    runtime.enrich_calibrator_candidate_health(result, {
        "artifact_packets": [{
            "sport": "MLB",
            "stat_type": "PITCHER_STRIKEOUTS",
            "feature_schema_version": "PROP_FEATURES_V1",
            "model_family": "MLB_PITCHER_SO_FAILURE_PATH_NB_V1",
            "model_artifact_version": "MLB_PITCHER_SO_FAILURE_PATH_NB_V1_2026_08_29",
            "artifact_checksum": "sha256:exact",
            "status": "CANDIDATE_EVIDENCE_READY",
            "selected_method": "PLATT_TIME_SPLIT_V1",
            "evidence_hash": "candidate-evidence",
            "training_n": 500,
            "holdout_n": 130,
            "raw_holdout_metrics": {"brier": 0.24},
            "calibrated_holdout_metrics": {"brier": 0.22},
            "fit_oof_metrics": {"brier": 0.23},
            "certification_review_packet_ready": True,
            "blockers": [],
        }]
    })
    assert row["calibrator_candidate_status"] == "CANDIDATE_EVIDENCE_READY"
    assert row["calibrator_candidate_review_ready"] is True


def test_install_is_idempotent_and_patches_base_dashboard_projection():
    overlay.install()
    first = base.build_lifecycle_dashboard
    overlay.install()
    assert base.build_lifecycle_dashboard is first

    dashboard = _dashboard()
    assert dashboard["artifact_cohort_rows"][0]["feature_schema_version"] == "PROP_FEATURES_V1"


def test_ambiguous_schema_identity_remains_fail_closed():
    dashboard = _dashboard()
    certification = _certification()
    certification["artifact_rows"].append({
        **certification["artifact_rows"][0],
        "feature_schema_version": "OTHER_SCHEMA_V2",
    })
    overlay.preserve_artifact_feature_schema_identity(dashboard, certification)
    assert dashboard["artifact_cohort_rows"][0]["feature_schema_version"] is None
