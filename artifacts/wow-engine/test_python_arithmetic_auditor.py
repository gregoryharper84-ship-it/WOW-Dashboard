from decimal import Decimal

import python_arithmetic_auditor as auditor
import wolfram_arithmetic_auditor as compatibility


def test_python_arithmetic_auditor_requires_no_external_transport(monkeypatch):
    monkeypatch.delenv("WOW_PYTHON_ARITHMETIC_AUDIT_ENABLED", raising=False)
    state = auditor.readiness()
    assert state["provider"] == "PYTHON_PRIMARY"
    assert state["status"] == "READY"
    assert state["external_transport_required"] is False
    assert state["can_execute"] is False


def test_probability_and_market_arithmetic_pass_locally():
    result = auditor.audit_claims([
        {
            "claim_id": "normalization",
            "template_id": "PROBABILITY_TOTAL",
            "inputs": {"probabilities": [0.55, 0.45]},
            "reported_result": 1.0,
        },
        {
            "claim_id": "no-vig",
            "template_id": "TWO_WAY_NO_VIG",
            "inputs": {"q_selected": 0.6, "q_opposing": 0.5},
            "reported_result": float(Decimal("0.6") / Decimal("1.1")),
        },
    ])
    assert result["verdict"] == "PASS"
    assert result["provider"] == "PYTHON_PRIMARY"
    assert result["external_transport_used"] is False
    assert result["can_execute"] is False


def test_mismatch_uses_v17_verification_conflict_semantics():
    result = auditor.audit_claims([
        {
            "claim_id": "bad-total",
            "template_id": "PROBABILITY_TOTAL",
            "inputs": {"probabilities": [0.5, 0.5]},
            "reported_result": 0.9,
        }
    ])
    assert result["verdict"] == "COMPUTATION_VERIFICATION_CONFLICT"
    assert result["can_execute"] is False


def test_legacy_import_is_compatibility_only_and_uses_python_provider():
    state = compatibility.readiness()
    assert state["provider"] == "PYTHON_PRIMARY"
    assert state["external_transport_required"] is False
    assert compatibility.PASS == auditor.PASS
