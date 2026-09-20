from __future__ import annotations

import pytest

import prop_calibration_adapters
import prop_model_adapters
from v17.cross_sport_certification_inventory import CERTIFICATION_SPORTS
from v17.prop_route_lifecycle import (
    CALIBRATION_CERTIFIED_PASS,
    CALIBRATION_EVIDENCE_REQUIRED,
    CERTIFICATION_APPROVED,
    CERTIFICATION_REVIEW_REQUIRED,
    GOVERNED_PROMOTION_REQUIRED,
    HYDRATION_OR_RUNTIME_ADAPTER_REQUIRED,
    MODEL_BUILD_REQUIRED,
    NO_CURRENT_PROP_CATEGORY_DECLARED,
    PRODUCTION_REGISTERED,
    PropRouteLifecycleEvidence,
    assess_prop_route,
    build_prop_route_inventory,
    hydration_route_registered,
    runtime_registration_snapshot,
)


def _fitted(**overrides) -> PropRouteLifecycleEvidence:
    values = {
        "sport": "WNBA",
        "stat_type": "POINTS",
        "declared_route": True,
        "controlling_specialist_ready": True,
        "model_build_exists": True,
        "fitted_model_present": True,
        "model_family": "WNBA_PROP_POISSON_LOGGLM_V1",
        "model_artifact_version": "WNBA_PTS_POISSON_LOGGLM_V1_2026_09_05",
        "artifact_checksum": "a" * 64,
        "calibrator_version": "WNBA_PROP_PRECALIBRATION_BOOTSTRAP_V1",
        "source_provenance_ready": True,
    }
    values.update(overrides)
    return PropRouteLifecycleEvidence(**values)


def _certified(**overrides) -> PropRouteLifecycleEvidence:
    values = {
        **_fitted().__dict__,
        "calibration_evidence_status": CALIBRATION_CERTIFIED_PASS,
        "deterministic_replay_ready": True,
        "certification_review_status": CERTIFICATION_APPROVED,
        "certification_id": "PROP-CERT-TEST-WNBA-POINTS",
    }
    values.update(overrides)
    return PropRouteLifecycleEvidence(**values)


def _promoted(**overrides) -> PropRouteLifecycleEvidence:
    values = {
        **_certified().__dict__,
        "lifecycle_state": "PROSPECTIVE_CERTIFIED",
        "promoted": True,
        "active": True,
    }
    values.update(overrides)
    return PropRouteLifecycleEvidence(**values)


def test_declared_cross_sport_build_target_is_explicit_model_build_required() -> None:
    result = assess_prop_route(PropRouteLifecycleEvidence(sport="NHL", stat_type="POINTS"))
    assert result.status == MODEL_BUILD_REQUIRED
    assert "CONTROLLING_SPECIALIST_UNAVAILABLE" in result.blockers
    assert "SOURCE_PROVENANCE_NOT_CERTIFIED" in result.blockers
    assert "FITTED_MODEL_BUILD_REQUIRED" in result.blockers
    assert result.production_numerical_authority is False
    assert result.can_execute is False


def test_declared_route_without_fitted_build_requires_model_build() -> None:
    result = assess_prop_route(
        PropRouteLifecycleEvidence(
            sport="NBA",
            stat_type="FANTASY_SCORE",
            declared_route=True,
            controlling_specialist_ready=True,
            source_provenance_ready=True,
        )
    )
    assert result.status == MODEL_BUILD_REQUIRED
    assert "FITTED_MODEL_BUILD_REQUIRED" in result.blockers


def test_phase_a_precalibration_cannot_satisfy_forward_calibration_certification() -> None:
    result = assess_prop_route(
        _fitted(
            calibration_evidence_status="PRECALIBRATION_SHRINKAGE",
            deterministic_replay_ready=True,
            certification_review_status=CERTIFICATION_APPROVED,
            certification_id="WNBA-V17-PROSPECTIVE-20260915-POINTS",
            lifecycle_state="PROSPECTIVE_CERTIFIED",
            promoted=True,
            active=True,
            model_adapter_registered=True,
            calibrator_adapter_registered=True,
            hydration_registered=True,
            action_canary_verified=True,
        )
    )
    assert result.status == CALIBRATION_EVIDENCE_REQUIRED
    assert "PHASE_A_PRECALIBRATION_NOT_FORWARD_CERTIFICATION" in result.blockers
    assert result.production_numerical_authority is False


def test_calibration_pass_still_requires_independent_certification_review() -> None:
    result = assess_prop_route(
        _fitted(
            calibration_evidence_status=CALIBRATION_CERTIFIED_PASS,
            deterministic_replay_ready=True,
            certification_review_status="PENDING",
        )
    )
    assert result.status == CERTIFICATION_REVIEW_REQUIRED
    assert "INDEPENDENT_CERTIFICATION_REVIEW_REQUIRED" in result.blockers
    assert "CERTIFICATION_ID_REQUIRED" in result.blockers


def test_reviewed_exact_artifact_requires_governed_promotion() -> None:
    result = assess_prop_route(_certified())
    assert result.status == GOVERNED_PROMOTION_REQUIRED
    assert "EXACT_ARTIFACT_NOT_PROMOTED" in result.blockers
    assert "EXACT_ARTIFACT_NOT_ACTIVE" in result.blockers
    assert "CERTIFIED_LIFECYCLE_STATE_REQUIRED" in result.blockers


def test_promoted_artifact_still_requires_runtime_registration_and_action_canary() -> None:
    result = assess_prop_route(
        _promoted(
            model_adapter_registered=True,
            calibrator_adapter_registered=True,
            hydration_registered=False,
            action_canary_verified=False,
        )
    )
    assert result.status == HYDRATION_OR_RUNTIME_ADAPTER_REQUIRED
    assert "EXACT_ROUTE_HYDRATION_REQUIRED" in result.blockers
    assert "CANONICAL_ACTION_CANARY_REQUIRED" in result.blockers
    assert result.production_numerical_authority is False


def test_complete_same_artifact_chain_is_production_registered() -> None:
    result = assess_prop_route(
        _promoted(
            model_adapter_registered=True,
            calibrator_adapter_registered=True,
            hydration_registered=True,
            action_canary_verified=True,
        )
    )
    assert result.status == PRODUCTION_REGISTERED
    assert result.blockers == ()
    assert result.production_numerical_authority is True
    assert result.can_execute is False


def test_can_execute_can_never_be_granted_by_lifecycle_evidence() -> None:
    with pytest.raises(ValueError, match="cannot grant execution authority"):
        assess_prop_route(_promoted(can_execute=True))


def test_all_sports_inventory_has_explicit_row_even_without_route_observation() -> None:
    inventory = build_prop_route_inventory([_fitted(calibration_evidence_status="PRECALIBRATION_SHRINKAGE")])
    sports = {row["sport"] for row in inventory}
    assert sports == set(CERTIFICATION_SPORTS)
    by_sport = {row["sport"]: row for row in inventory if row["sport"] != "WNBA"}
    assert all(row["status"] == NO_CURRENT_PROP_CATEGORY_DECLARED for row in by_sport.values())
    assert all(row["can_execute"] is False for row in inventory)


def test_duplicate_exact_route_is_rejected_in_inventory() -> None:
    with pytest.raises(ValueError, match="duplicate exact prop lifecycle route"):
        build_prop_route_inventory([_fitted(), _fitted()])


def test_generic_hydration_registration_is_exact_route_scoped() -> None:
    assert hydration_route_registered("MLB", "PITCHER_STRIKEOUTS") is True
    assert hydration_route_registered("MLB", "PITCHING_OUTS") is True
    assert hydration_route_registered("MLB", "PLATE_APPEARANCES") is True
    assert hydration_route_registered("WNBA", "POINTS") is True
    assert hydration_route_registered("WNBA", "THREE_POINTERS_MADE") is True
    assert hydration_route_registered("NBA", "FANTASY_SCORE") is False
    assert hydration_route_registered("MLB", "1ST_INNING_PITCHES_THROWN") is False


def test_runtime_registration_snapshot_reads_code_owned_registries_without_mutating_authority() -> None:
    prop_model_adapters.register()
    prop_calibration_adapters.register()

    wnba = runtime_registration_snapshot(
        sport="WNBA",
        stat_type="POINTS",
        model_family="WNBA_PROP_POISSON_LOGGLM_V1",
        calibrator_version="WNBA_PROP_PRECALIBRATION_BOOTSTRAP_V1",
    )
    assert wnba["model_adapter_registered"] is True
    assert wnba["calibrator_adapter_registered"] is True
    assert wnba["hydration_route_registered"] is True
    assert wnba["can_execute"] is False

    fantasy = runtime_registration_snapshot(
        sport="NBA",
        stat_type="FANTASY_SCORE",
        model_family="NBA_FANTASY_SCORE_FITTED_V1",
        calibrator_version="NBA_FANTASY_SCORE_CAL_V1",
    )
    assert fantasy["hydration_route_registered"] is False
    assert fantasy["can_execute"] is False
