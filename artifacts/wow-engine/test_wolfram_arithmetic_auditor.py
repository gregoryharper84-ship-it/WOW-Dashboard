from decimal import Decimal
from types import SimpleNamespace

import pytest

from wolfram_arithmetic_auditor import (
    CALCULATION_MISMATCH,
    INPUT_INVALID,
    NOT_REQUIRED,
    PASS,
    UNAVAILABLE,
    audit_claims,
    persist_audit,
    readiness,
)


class SequenceProvider:
    def __init__(self, *values: str):
        self.values = iter(Decimal(value) for value in values)
        self.expressions: list[str] = []

    def evaluate(self, expression: str) -> Decimal:
        self.expressions.append(expression)
        return next(self.values)


def test_probability_and_no_vig_claims_receive_independent_receipts():
    provider = SequenceProvider("1", "0.52380952380952380952")
    result = audit_claims(
        [
            {
                "claim_id": "pmf",
                "template_id": "PROBABILITY_TOTAL",
                "inputs": {"probabilities": [0.52, 0.43, 0.05]},
                "reported_result": 1.0,
            },
            {
                "claim_id": "no-vig",
                "template_id": "TWO_WAY_NO_VIG",
                "inputs": {"q_selected": 0.55, "q_opposing": 0.50},
                "reported_result": 0.5238095238095238,
            },
        ],
        provider=provider,
    )
    assert result["verdict"] == PASS
    assert [receipt["verdict"] for receipt in result["receipts"]] == [PASS, PASS]
    assert all(len(receipt["input_payload_hash"]) == 64 for receipt in result["receipts"])
    assert result["blocks_model_probability"] is False
    assert result["can_execute"] is False
    assert all("appid" not in expression.lower() for expression in provider.expressions)


@pytest.mark.parametrize(
    ("template_id", "inputs", "reported", "wolfram"),
    [
        ("AMERICAN_ODDS_IMPLIED_PROBABILITY", {"american_odds": -110}, 110 / 210, "0.52380952380952380952"),
        ("POWER_JOINT_BREAK_EVEN", {"gross_multiplier": 3}, 1 / 3, "0.33333333333333333333"),
        ("GROSS_RETURN", {"stake": 10, "gross_multiplier": 3}, 30, "30"),
        ("NET_PROFIT", {"stake": 10, "gross_multiplier": 3}, 20, "20"),
        (
            "FIXED_ODDS_EXPECTED_PROFIT",
            {"p_win": 0.55, "p_loss": 0.35, "profit_multiple": 100 / 110},
            0.55 * (100 / 110) - 0.35,
            "0.15",
        ),
        (
            "FIXED_ODDS_BREAK_EVEN_UNCONDITIONAL",
            {"refundable_probability": 0.10, "profit_multiple": 100 / 110},
            0.9 / (1 + 100 / 110),
            "0.47142857142857142857",
        ),
    ],
)
def test_supported_financial_templates(template_id, inputs, reported, wolfram):
    result = audit_claims(
        [{"template_id": template_id, "inputs": inputs, "reported_result": reported}],
        provider=SequenceProvider(wolfram),
    )
    assert result["verdict"] == PASS


def test_reported_or_provider_disagreement_is_typed_mismatch():
    provider_mismatch = audit_claims(
        [{
            "template_id": "PROBABILITY_COMPLEMENT",
            "inputs": {"probability": 0.40},
            "reported_result": 0.60,
        }],
        provider=SequenceProvider("0.61"),
    )
    assert provider_mismatch["verdict"] == CALCULATION_MISMATCH

    reported_mismatch = audit_claims(
        [{
            "template_id": "PROBABILITY_COMPLEMENT",
            "inputs": {"probability": 0.40},
            "reported_result": 0.61,
        }],
        provider=SequenceProvider("0.60"),
    )
    assert reported_mismatch["verdict"] == CALCULATION_MISMATCH


def test_caller_cannot_submit_an_arbitrary_expression():
    result = audit_claims(
        [{
            "template_id": "RUN_ARBITRARY_EXPRESSION",
            "inputs": {"expression": "anything"},
            "reported_result": 1,
        }],
        provider=SequenceProvider("1"),
    )
    assert result["verdict"] == INPUT_INVALID
    assert result["receipts"][0]["error_type"] == "ValueError"


def test_missing_server_credential_is_scoped_unavailable(monkeypatch):
    monkeypatch.setenv("WOW_WOLFRAM_ARITHMETIC_AUDIT_ENABLED", "1")
    monkeypatch.delenv("WOLFRAM_ALPHA_APP_ID", raising=False)
    result = audit_claims(
        [{
            "template_id": "PROBABILITY_COMPLEMENT",
            "inputs": {"probability": 0.40},
            "reported_result": 0.60,
        }]
    )
    assert result["verdict"] == UNAVAILABLE
    assert result["blocks_model_probability"] is False
    assert result["can_execute"] is False
    assert "WOLFRAM_ALPHA_APP_ID" not in str(result)
    assert readiness()["status"] == "CREDENTIAL_MISSING"


def test_disabled_audit_is_explicitly_not_required():
    result = audit_claims([], required=False)
    assert result["verdict"] == NOT_REQUIRED
    assert result["audit_required"] is False


class _InsertQuery:
    def __init__(self, rows):
        self.rows = rows
        self.payload = None

    def insert(self, payload):
        self.payload = payload
        return self

    def execute(self):
        return SimpleNamespace(data=[{
            "arithmetic_audit_id": "82df56cc-9b4f-40ee-a237-ae88fb6ae93e",
            **self.payload,
        }])


class _AuditClient:
    def __init__(self):
        self.query = _InsertQuery([])
        self.table_name = None

    def table(self, name):
        self.table_name = name
        return self.query


def test_persist_audit_writes_hash_bound_append_only_receipt():
    client = _AuditClient()
    stored = persist_audit(
        client,
        prediction_id="80274a23-57fe-46d5-9269-d30414e07908",
        audit={
            "verdict": PASS,
            "provider": "WOLFRAM_ALPHA",
            "audit_required": True,
            "audited_at": "2026-09-02T12:00:00+00:00",
            "receipts": [{"claim_id": "pmf", "verdict": PASS}],
            "blocks_model_probability": False,
            "can_execute": False,
        },
    )
    assert client.table_name == "wow_wolfram_arithmetic_audits"
    assert stored["prediction_id"] == "80274a23-57fe-46d5-9269-d30414e07908"
    assert len(stored["audit_payload_hash"]) == 64
    assert stored["claim_count"] == 1
    assert stored["blocks_model_probability"] is False
    assert stored["can_execute"] is False
