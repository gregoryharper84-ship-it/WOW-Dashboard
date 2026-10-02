from __future__ import annotations

import hashlib

from v17.class_b_distribution_protocol import (
    ClassBGateValidator,
    LineDomain,
    SupportType,
    TriadBinding,
)


def _binding() -> TriadBinding:
    raw = "model-sha:features-v1:cal-v1"
    return TriadBinding(
        model_artifact_sha="model-sha",
        feature_contract_version="features-v1",
        calibration_artifact_id="cal-v1",
        triad_digest_sha256=hashlib.sha256(raw.encode()).hexdigest(),
    )


class Specialist:
    specialist_id = "test-specialist"
    support_type = SupportType.DISCRETE
    domain_bounds = LineDomain(0.0, 100.0, 0.5)
    triad_binding = _binding()

    def evaluate_cdf(self, line: float) -> float:
        return 0.6

    def evaluate_push_mass(self, line: float) -> float:
        return 0.05

    def verify_normalization(self, tolerance: float = 1e-4) -> bool:
        return True


def _validate(specialist, **kwargs):
    return ClassBGateValidator().validate_and_evaluate(
        specialist,
        sport="NFL",
        market_type="PLAYER_PROP",
        stat_type="RECEIVING_YARDS",
        line=50.5,
        **kwargs,
    )


def test_qualified_specialist_remains_non_executable_and_reducer_subordinate():
    receipt = _validate(Specialist())

    assert receipt.model_qualified is True
    assert receipt.can_execute is False
    assert receipt.typed_gate_reason == "QUALIFIED_FOR_V17_TERMINAL_REDUCER"
    assert receipt.verification_matrix["triad_digest_verified"] is True


def test_bad_digest_fails_closed_with_specific_typed_reason():
    specialist = Specialist()
    specialist.triad_binding = TriadBinding("model-sha", "features-v1", "cal-v1", "0" * 64)

    receipt = _validate(specialist)

    assert receipt.model_qualified is False
    assert receipt.can_execute is False
    assert receipt.typed_gate_reason.startswith("TriadDigestMismatchError:")
    assert "MODEL_UNAVAILABLE" not in receipt.typed_gate_reason


def test_off_grid_line_fails_closed():
    specialist = Specialist()
    specialist.domain_bounds = LineDomain(0.0, 100.0, 1.0)

    receipt = _validate(specialist)

    assert receipt.model_qualified is False
    assert receipt.can_execute is False
    assert receipt.typed_gate_reason.startswith("UnsupportedLineStepError:")


def test_combo_dependency_is_required_when_declared():
    receipt = _validate(Specialist(), is_combo_prop=True, has_certified_dependency=False)

    assert receipt.model_qualified is False
    assert receipt.can_execute is False
    assert receipt.typed_gate_reason.startswith("DependencyArtifactMissingError:")


def test_exposure_artifact_is_required_when_declared():
    receipt = _validate(Specialist(), requires_exposure_model=True, has_certified_exposure=False)

    assert receipt.model_qualified is False
    assert receipt.can_execute is False
    assert receipt.typed_gate_reason.startswith("ExposureModelUnqualifiedError:")


def test_unexpected_specialist_error_is_not_rewritten_as_model_unavailable():
    class Broken(Specialist):
        def verify_normalization(self, tolerance: float = 1e-4) -> bool:
            raise RuntimeError("artifact read failed")

    receipt = _validate(Broken())

    assert receipt.model_qualified is False
    assert receipt.can_execute is False
    assert receipt.typed_gate_reason.startswith("DistributionEvaluationError: RuntimeError:")
    assert "MODEL_UNAVAILABLE" not in receipt.typed_gate_reason
