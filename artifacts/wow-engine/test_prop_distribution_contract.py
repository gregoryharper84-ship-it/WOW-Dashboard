from datetime import datetime, timezone

import pytest

from prop_distribution_contract import (
    CertifiedBundle,
    CoverageDecision,
    PropDistributionContractError,
    PropInferenceRequest,
    RawDiscreteDistribution,
    derive_line_probabilities,
    mix_failure_paths,
)


def _request_payload():
    return {
        "event_id": "WNBA:2026:TEST",
        "player_id": "player-1",
        "sport": "WNBA",
        "league_season": "2026",
        "stat_type": "ASSISTS",
        "evidence_snapshot_id": "snapshot-1",
        "market_identity_id": "market-1",
        "as_of_timestamp": "2026-08-29T01:00:00+00:00",
        "request_id": "request-1",
        "feature_schema_version": "WNBA_PROP_FEATURES_V1",
    }


def _distribution(support=None, *, in_distribution=True):
    return RawDiscreteDistribution(
        support=support or {0: 0.1, 1: 0.2, 2: 0.3, 3: 0.4},
        coverage=CoverageDecision(
            in_distribution=in_distribution,
            ood_score=0.1 if in_distribution else 0.91,
            coverage_failures=() if in_distribution else ("ROLE_REGIME_UNSEEN",),
        ),
        model_artifact_version="WNBA_ASSISTS_V1.0.0",
        training_code_sha="a" * 40,
        training_dataset_hash="b" * 64,
        feature_schema_version="WNBA_PROP_FEATURES_V1",
        feature_transform_sha="c" * 40,
        feature_snapshot_hash="d" * 64,
        artifact_checksum="e" * 64,
        inference_timestamp=datetime.now(timezone.utc).isoformat(),
    )


def _bundle(**overrides):
    values = {
        "model_artifact_version": "WNBA_ASSISTS_V1.0.0",
        "calibrator_version": "WNBA_ASSISTS_CAL_V1.0.0",
        "feature_transform_version": "WNBA_PROP_TRANSFORM_V1.0.0",
        "specialist_version": "WNBA_ASSISTS_SPECIALIST_V1.0.0",
        "certification_id": "cert-1",
        "feature_schema_version": "WNBA_PROP_FEATURES_V1",
        "training_dataset_hash": "b" * 64,
        "training_code_sha": "a" * 40,
        "artifact_checksum": "e" * 64,
        "lifecycle_state": "PROSPECTIVE_CERTIFIED",
        "supported_sport": "WNBA",
        "supported_stat_type": "ASSISTS",
        "supported_line_min": 0.5,
        "supported_line_max": 15.5,
    }
    values.update(overrides)
    return CertifiedBundle(**values)


def test_provider_request_is_direction_free_and_router_controls_bundle():
    request = PropInferenceRequest.from_mapping(_request_payload())
    assert request.stat_type == "ASSISTS"

    for forbidden in (
        "direction",
        "model_version",
        "calibrator_version",
        "certification_id",
    ):
        payload = _request_payload()
        payload[forbidden] = "caller-choice"
        with pytest.raises(PropDistributionContractError) as exc:
            PropInferenceRequest.from_mapping(payload)
        assert exc.value.code == "CALLER_CONTROLLED_BUNDLE_OR_DIRECTION_PROHIBITED"


def test_bundle_requires_exact_certified_compatibility():
    request = PropInferenceRequest.from_mapping(_request_payload())
    _bundle().assert_compatible(request, 5.5)

    with pytest.raises(PropDistributionContractError) as exc:
        _bundle(feature_schema_version="OTHER").assert_compatible(request, 5.5)
    assert exc.value.code == "MODEL_CALIBRATOR_BUNDLE_MISMATCH"

    with pytest.raises(PropDistributionContractError) as exc:
        _bundle(lifecycle_state="SHADOW_PROSPECTIVE").assert_compatible(request, 5.5)
    assert exc.value.code == "PROP_BUNDLE_NOT_CERTIFIED"


def test_pmf_rejects_invalid_or_provider_publishable_output():
    with pytest.raises(PropDistributionContractError) as exc:
        _distribution({0: 0.2, 1: 0.7})
    assert exc.value.code == "PROP_PMF_NOT_NORMALIZED"

    with pytest.raises(PropDistributionContractError) as exc:
        RawDiscreteDistribution(
            **{
                **_distribution().__dict__,
                "publication_status": "PUBLISHABLE",
            }
        )
    assert exc.value.code == "PROVIDER_PUBLICATION_AUTHORITY_PROHIBITED"


def test_whole_number_and_half_line_settlement_are_derived_from_one_pmf():
    distribution = _distribution({3: 0.2, 4: 0.3, 5: 0.5})

    whole = derive_line_probabilities(distribution, 4.0)
    assert whole.probability_more == pytest.approx(0.5)
    assert whole.probability_less == pytest.approx(0.2)
    assert whole.push_probability == pytest.approx(0.3)

    half = derive_line_probabilities(distribution, 4.5)
    assert half.probability_more == pytest.approx(0.5)
    assert half.probability_less == pytest.approx(0.5)
    assert half.push_probability == 0.0


def test_distribution_statistics_are_internally_consistent():
    distribution = _distribution({0: 0.1, 1: 0.2, 2: 0.3, 3: 0.4})
    assert distribution.expected_value == pytest.approx(2.0)
    assert distribution.variance == pytest.approx(1.0)
    assert distribution.quantile(0.1) == 0
    assert distribution.quantile(0.5) == 2
    assert distribution.quantile(0.9) == 3


def test_ood_abstention_requires_machine_readable_failure():
    with pytest.raises(PropDistributionContractError) as exc:
        CoverageDecision(in_distribution=False, ood_score=0.9, coverage_failures=())
    assert exc.value.code == "OOD_REASON_REQUIRED"

    held = _distribution(in_distribution=False)
    assert held.coverage.coverage_failures == ("ROLE_REGIME_UNSEEN",)
    assert held.publication_status == "NOT_EVALUATED"
    assert held.can_execute is False


def test_role_failure_paths_change_unconditional_distribution():
    mixed = mix_failure_paths(
        (
            (0.7, {3: 0.2, 4: 0.8}),
            (0.2, {1: 0.5, 2: 0.5}),
            (0.1, {0: 1.0}),
        )
    )
    assert mixed == pytest.approx({0: 0.1, 1: 0.1, 2: 0.1, 3: 0.14, 4: 0.56})
    assert sum(mixed.values()) == pytest.approx(1.0)


def test_bundle_fingerprint_changes_when_any_bound_component_changes():
    baseline = _bundle().bundle_fingerprint
    assert baseline != _bundle(calibrator_version="WNBA_ASSISTS_CAL_V1.0.1").bundle_fingerprint
