from datetime import datetime, timezone

import pytest

import v17.prop_lifecycle_autopilot as base
import v17.prop_calibrator_candidate_runtime as candidate_runtime
import v17.prop_lifecycle_autopilot_runtime as runtime


NOW = datetime(2026, 9, 17, 21, 30, tzinfo=timezone.utc)
TOKEN = "MLB:PITCHER_STRIKEOUTS"


def _forward_result():
    return {
        "route_results": [
            {
                "sport": "MLB",
                "stat_type": "PITCHER_STRIKEOUTS",
                "collector": "MLB_STRIKEOUT_FORWARD_COHORT",
                "status": "PASS",
                "calibration_readiness": {
                    "forward_prediction_n": 25,
                    "forward_settled_n": 19,
                    "artifact_cohorts": [
                        {
                            "feature_schema_version": "PROP_FEATURES_V1",
                            "model_family": "SO_MODEL",
                            "model_artifact_version": "SO_ARTIFACT_V1",
                            "model_artifact_checksum": "sha256:abc",
                            "calibration_version": "SO_CAL_V1",
                            "forward_prediction_n": 25,
                            "forward_settled_n": 19,
                            "forward_unsettled_n": 6,
                            "counting_basis": "UNIQUE_EVENT_PLAYER_STAT_LINE_THESIS_WITHIN_EXACT_ARTIFACT",
                        }
                    ],
                },
            }
        ]
    }


def _settlement_result():
    return {
        "route_dispositions": [
            {
                "sport": "MLB",
                "stat_type": "PITCHER_STRIKEOUTS",
                "status": "OFFICIAL_SETTLEMENT_ADAPTER_READY",
                "official_source": "MLB_STATS_API_GAME_FEED",
            }
        ],
        "results": [],
    }


def _cert_result():
    return {
        "artifact_rows": [
            {
                "sport": "MLB",
                "stat_type": "PITCHER_STRIKEOUTS",
                "feature_schema_version": "PROP_FEATURES_V1",
                "model_family": "SO_MODEL",
                "model_artifact_version": "SO_ARTIFACT_V1",
                "artifact_checksum": "sha256:abc",
                "calibrator_version": "SO_CAL_V1",
                "status": "CALIBRATION_EVIDENCE_REQUIRED",
                "policy_id": "SO_POLICY_V1",
                "policy_review_status": "REVIEW_REQUIRED",
                "evidence_hash": "evidence-hash",
                "promotion_package_ready": False,
                "artifact_registry_lifecycle_state": "PRECALIBRATION_SHRINKAGE",
                "artifact_registry_promoted": False,
                "artifact_registry_active": True,
                "independent_settled_thesis_n": 19,
                "duplicate_or_twin_row_n": 19,
                "excluded_invalid_row_n": 0,
                "metrics": {
                    "n": 19,
                    "brier_score": 0.21,
                    "log_loss": 0.63,
                    "expected_calibration_error": 0.08,
                    "calibration_bias": -0.02,
                    "lower_bound_reliability_margin": 0.05,
                },
                "blockers": ["MIN_SETTLED_CALIBRATION_COHORT_NOT_MET"],
            }
        ]
    }


def _registration_result():
    return {
        "route_rows": [
            {
                "sport": "MLB",
                "stat_type": "PITCHER_STRIKEOUTS",
                "status": "CALIBRATION_EVIDENCE_REQUIRED",
                "model_family": "SO_MODEL",
                "model_artifact_version": "SO_ARTIFACT_V1",
                "artifact_checksum": "sha256:abc",
                "calibrator_version": "SO_CAL_V1",
                "production_numerical_authority": False,
                "model_adapter_registered": True,
                "calibrator_adapter_registered": True,
                "hydration_route_registered": True,
                "action_canary_verified": False,
                "blockers": ["FORWARD_CALIBRATION_CERTIFICATION_REQUIRED"],
                "action_canary_blockers": ["CANONICAL_ACTION_CANARY_REQUIRED"],
            }
        ]
    }


def test_base_autopilot_runs_all_stages_in_order_and_never_self_promotes(monkeypatch):
    calls = []
    monkeypatch.setattr(base, "_requested_tokens", lambda values: [TOKEN])
    monkeypatch.setattr(base, "build_forward_evidence_inventory", lambda: [])
    monkeypatch.setattr(base, "build_settlement_inventory", lambda: [])
    monkeypatch.setattr(base, "run_universal_prop_forward_evidence", lambda *a, **k: calls.append("forward") or _forward_result())
    monkeypatch.setattr(base, "run_exact_route_settlement", lambda *a, **k: calls.append("settlement") or _settlement_result())
    monkeypatch.setattr(base, "run_prop_certification_audit", lambda *a, **k: calls.append("certification") or _cert_result())
    monkeypatch.setattr(base, "run_prop_production_registration_audit", lambda *a, **k: calls.append("registration") or _registration_result())

    result = base.run_prop_lifecycle_autopilot(
        base.PropLifecycleAutopilotRequest(routes=[TOKEN], persist_health=False),
        db=object(),
        market_api=object(),
        now=NOW,
    )

    assert calls == ["forward", "settlement", "certification", "registration"]
    assert result["run_status"] == "COMPLETED"
    assert result["automatic_certification"] is False
    assert result["automatic_promotion"] is False
    assert result["can_execute"] is False
    assert result["dashboard"]["kpi"]["production_registered_n"] == 0
    assert result["dashboard"]["kpi"]["improperly_promoted_route_n"] == 0


def test_stage_failure_does_not_stop_later_lifecycle_work(monkeypatch):
    calls = []
    monkeypatch.setattr(base, "_requested_tokens", lambda values: [TOKEN])
    monkeypatch.setattr(base, "build_forward_evidence_inventory", lambda: [])
    monkeypatch.setattr(base, "build_settlement_inventory", lambda: [])

    def broken_forward(*args, **kwargs):
        calls.append("forward")
        raise RuntimeError("source down")

    monkeypatch.setattr(base, "run_universal_prop_forward_evidence", broken_forward)
    monkeypatch.setattr(base, "run_exact_route_settlement", lambda *a, **k: calls.append("settlement") or _settlement_result())
    monkeypatch.setattr(base, "run_prop_certification_audit", lambda *a, **k: calls.append("certification") or _cert_result())
    monkeypatch.setattr(base, "run_prop_production_registration_audit", lambda *a, **k: calls.append("registration") or _registration_result())

    result = base.run_prop_lifecycle_autopilot(
        base.PropLifecycleAutopilotRequest(routes=[TOKEN], persist_health=False),
        db=object(),
        market_api=object(),
        now=NOW,
    )

    assert calls == ["forward", "settlement", "certification", "registration"]
    assert result["run_status"] == "COMPLETED_WITH_STAGE_BLOCKERS"
    assert result["failed_stages"] == ["UNIVERSAL_FORWARD_CAPTURE"]
    assert result["certification_readiness"]["status"] == "PASS"
    assert result["production_registration"]["status"] == "PASS"


def test_artifact_health_uses_exact_artifact_forward_counts():
    result = {
        "forward_capture": {"result": _forward_result()},
        "dashboard": {
            "artifact_cohort_rows": [
                {
                    "sport": "MLB",
                    "stat_type": "PITCHER_STRIKEOUTS",
                    "feature_schema_version": "PROP_FEATURES_V1",
                    "model_family": "SO_MODEL",
                    "model_artifact_version": "SO_ARTIFACT_V1",
                    "artifact_checksum": "sha256:abc",
                    "calibrator_version": "SO_CAL_V1",
                    "eligible_n": 19,
                    "promotion_package_ready": False,
                }
            ],
            "kpi": {"artifact_cohort_n": 1},
        },
    }

    runtime.enrich_artifact_cohort_health(result)
    row = result["dashboard"]["artifact_cohort_rows"][0]
    assert row["forward_prediction_n"] == 25
    assert row["forward_settled_n"] == 19
    assert row["forward_unsettled_n"] == 6
    assert row["eligible_n"] == 19
    assert row["cohort_identity_match"] is True
    assert result["dashboard"]["kpi"]["artifact_forward_prediction_n"] == 25
    assert result["dashboard"]["kpi"]["artifact_forward_settled_n"] == 19
    assert result["dashboard"]["kpi"]["artifact_eligible_n"] == 19


def test_production_registered_requires_every_runtime_and_canary_guard(monkeypatch):
    monkeypatch.setattr(base, "build_forward_evidence_inventory", lambda: [])
    monkeypatch.setattr(base, "build_settlement_inventory", lambda: [])
    registration = _registration_result()
    row = registration["route_rows"][0]
    row.update({
        "status": "PRODUCTION_REGISTERED",
        "production_numerical_authority": True,
        "model_adapter_registered": True,
        "calibrator_adapter_registered": True,
        "hydration_route_registered": True,
        "action_canary_verified": True,
        "blockers": [],
        "action_canary_blockers": [],
    })
    dashboard = base.build_lifecycle_dashboard(
        forward=_forward_result(),
        settlement=_settlement_result(),
        certification=_cert_result(),
        registration=registration,
        requested_tokens=[TOKEN],
        captured_at=NOW.isoformat(),
    )
    assert dashboard["kpi"]["production_registered_n"] == 1
    assert dashboard["kpi"]["improperly_promoted_route_n"] == 0

    row["action_canary_verified"] = False
    dashboard = base.build_lifecycle_dashboard(
        forward=_forward_result(),
        settlement=_settlement_result(),
        certification=_cert_result(),
        registration=registration,
        requested_tokens=[TOKEN],
        captured_at=NOW.isoformat(),
    )
    assert dashboard["kpi"]["production_registered_n"] == 1
    assert dashboard["kpi"]["improperly_promoted_route_n"] == 1


def test_calibrator_candidate_observation_does_not_require_prediction_can_execute_column():
    predictions = [{
        "prediction_id": "pred-1",
        "event_id": "MLB:1",
        "event_start_time": "2026-09-18T00:00:00+00:00",
        "model_timestamp": "2026-09-17T20:00:00+00:00",
        "locked_at": "2026-09-17T20:01:00+00:00",
        "source_snapshot_id": "snapshot-1",
        "player": "Test Pitcher",
        "sport": "MLB",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 5.5,
        "direction": "MORE",
        "model_family": "SO_MODEL",
        "model_artifact_version": "SO_ARTIFACT_V1",
        "model_artifact_checksum": "sha256:abc",
        "feature_schema_version": "PROP_FEATURES_V1",
        "raw_model_probability": 0.67,
        "model_provider_identity": candidate_runtime.PROVIDER,
    }]
    outcomes = {
        "pred-1": {
            "prediction_id": "pred-1",
            "hit": True,
            "push": False,
            "void": False,
        }
    }

    rows, counts = candidate_runtime.build_independent_raw_observations(predictions, outcomes)

    assert len(rows) == 1
    assert rows[0].prediction_id == "pred-1"
    assert rows[0].can_execute is False
    assert counts["excluded_invalid_row_n"] == 0
