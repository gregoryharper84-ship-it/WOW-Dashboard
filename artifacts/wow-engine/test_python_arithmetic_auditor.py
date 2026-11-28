# CI resync marker: validate the Python-only verifier against the latest V17 main.
from decimal import Decimal
from types import SimpleNamespace

import pytest

import python_arithmetic_auditor as auditor
import wolfram_arithmetic_auditor as compatibility


def test_python_arithmetic_auditor_requires_no_external_transport(monkeypatch):
    monkeypatch.delenv("WOW_PYTHON_ARITHMETIC_AUDIT_ENABLED", raising=False)
    state = auditor.readiness()
    assert state["provider"] == "PYTHON_PRIMARY"
    assert state["status"] == "DISABLED"
    assert state["external_transport_required"] is False
    assert state["can_execute"] is False


def test_python_arithmetic_auditor_can_be_explicitly_enabled_without_external_provider(monkeypatch):
    monkeypatch.setenv("WOW_PYTHON_ARITHMETIC_AUDIT_ENABLED", "1")
    state = auditor.readiness()
    assert state["provider"] == "PYTHON_PRIMARY"
    assert state["status"] == "READY"
    assert state["configured"] is True
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


class _LedgerClient:
    """Supabase-shaped ledger: insert acknowledges, select proves durability."""

    def __init__(self, *, drop_readback: bool = False, tamper: dict | None = None):
        self.rows: list[dict] = []
        self.drop_readback = drop_readback
        self.tamper = tamper or {}
        self.mode = "insert"
        self.filter: tuple[str, object] | None = None

    def table(self, name):
        assert name == "wow_wolfram_arithmetic_audits"
        return self

    def insert(self, payload):
        row = {**payload, "arithmetic_audit_id": "audit-1"}
        self.rows.append(row)
        self._last = row
        self.mode = "insert"
        return self

    def select(self, *_args, **_kwargs):
        self.mode = "select"
        return self

    def eq(self, key, value):
        self.filter = (key, value)
        return self

    def limit(self, _value):
        return self

    def execute(self):
        if self.mode == "select":
            key, value = self.filter
            found = [dict(row, **self.tamper) for row in self.rows if row.get(key) == value]
            return SimpleNamespace(data=[] if self.drop_readback else found)
        return SimpleNamespace(data=[self._last])


def _audit(**overrides):
    audit = {
        "provider": auditor.PROVIDER,
        "verdict": auditor.PASS,
        "audit_required": True,
        "audited_at": "2026-11-02T12:00:00+00:00",
        "receipts": [{"claim_id": "c1"}, {"claim_id": "c2"}],
    }
    audit.update(overrides)
    return audit


def test_persist_audit_proves_readback_before_returning():
    stored = auditor.persist_audit(_LedgerClient(), prediction_id="pred-1", audit=_audit())

    assert stored["arithmetic_audit_id"] == "audit-1"
    assert stored["ledger_readback"] == "PASS"
    assert stored["ledger_readback_at"]
    assert stored["can_execute"] is False


def test_persist_audit_rejects_unproven_readback():
    client = _LedgerClient(drop_readback=True)
    with pytest.raises(RuntimeError, match="readback"):
        auditor.persist_audit(client, prediction_id="pred-1", audit=_audit())


def test_persist_audit_rejects_readback_that_does_not_match():
    client = _LedgerClient(tamper={"audit_payload_hash": "tampered"})
    with pytest.raises(RuntimeError, match="readback"):
        auditor.persist_audit(client, prediction_id="pred-1", audit=_audit())
