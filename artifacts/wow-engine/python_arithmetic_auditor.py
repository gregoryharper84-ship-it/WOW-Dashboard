"""Deterministic local arithmetic verification for WOW V17.

Replaces the legacy external WolframAlpha transport dependency. This module
verifies only deterministic probability/market/payout transformations. It never
creates sporting probability, never selects a model, and never authorizes wager
execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
import json
import os
from typing import Any, Mapping, Sequence

PROVIDER = "PYTHON_PRIMARY"
PASS = "PASS"
NOT_REQUIRED = "NOT_REQUIRED"
INPUT_INVALID = "PYTHON_ARITHMETIC_AUDIT_INPUT_INVALID"
OUTPUT_INVALID = "PYTHON_ARITHMETIC_OUTPUT_INVALID"
CALCULATION_MISMATCH = "COMPUTATION_VERIFICATION_CONFLICT"
LEDGER_WRITE_UNPROVEN = "PYTHON_ARITHMETIC_AUDIT_LEDGER_WRITE_UNPROVEN"

PROBABILITY_TOLERANCE = Decimal("0.000001")
GENERAL_TOLERANCE = Decimal("0.000001")
CURRENCY_TOLERANCE = Decimal("0.01")


def audit_enabled() -> bool:
    """Legacy market-boundary audit is opt-in; no external provider is required."""
    return os.getenv("WOW_PYTHON_ARITHMETIC_AUDIT_ENABLED", "0") == "1"


def readiness() -> dict[str, Any]:
    enabled = audit_enabled()
    return {
        "provider": PROVIDER,
        "enabled": enabled,
        "configured": True,
        "status": "READY" if enabled else "DISABLED",
        "external_transport_required": False,
        "blocks_model_probability": False,
        "can_execute": False,
    }


def _decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{name} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _positive(value: Any, name: str) -> Decimal:
    result = _decimal(value, name)
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def _probability(value: Any, name: str) -> Decimal:
    result = _decimal(value, name)
    if result < 0 or result > 1:
        raise ValueError(f"{name} must be inside [0,1]")
    return result


def _american_implied(odds: Decimal) -> Decimal:
    if odds == 0:
        raise ValueError("american_odds must be non-zero")
    if odds > 0:
        return Decimal(100) / (odds + Decimal(100))
    magnitude = abs(odds)
    return magnitude / (magnitude + Decimal(100))


def _calculate(template_id: str, inputs: Mapping[str, Any]) -> tuple[Decimal, Decimal]:
    template = str(template_id or "").strip().upper()
    with localcontext() as context:
        context.prec = 40
        if template == "PROBABILITY_TOTAL":
            values = [_probability(v, "probabilities[]") for v in inputs.get("probabilities", [])]
            if not values:
                raise ValueError("probabilities must be non-empty")
            return sum(values, Decimal(0)), PROBABILITY_TOLERANCE
        if template == "PROBABILITY_COMPLEMENT":
            return Decimal(1) - _probability(inputs.get("probability"), "probability"), PROBABILITY_TOLERANCE
        if template == "AMERICAN_ODDS_IMPLIED_PROBABILITY":
            return _american_implied(_decimal(inputs.get("american_odds"), "american_odds")), PROBABILITY_TOLERANCE
        if template == "DECIMAL_ODDS_IMPLIED_PROBABILITY":
            odds = _positive(inputs.get("decimal_odds"), "decimal_odds")
            if odds <= 1:
                raise ValueError("decimal_odds must exceed 1")
            return Decimal(1) / odds, PROBABILITY_TOLERANCE
        if template == "MARKET_HOLD":
            a = _probability(inputs.get("q_a"), "q_a")
            b = _probability(inputs.get("q_b"), "q_b")
            return a + b - Decimal(1), PROBABILITY_TOLERANCE
        if template == "TWO_WAY_NO_VIG":
            selected = _probability(inputs.get("q_selected"), "q_selected")
            opposing = _probability(inputs.get("q_opposing"), "q_opposing")
            denominator = selected + opposing
            if denominator <= 0:
                raise ValueError("two-way implied probabilities cannot both be zero")
            return selected / denominator, PROBABILITY_TOLERANCE
        if template == "PUSH_ADJUSTED_PROBABILITY":
            push = _probability(inputs.get("p_push"), "p_push")
            conditional = _probability(inputs.get("p_conditional"), "p_conditional")
            return (Decimal(1) - push) * conditional, PROBABILITY_TOLERANCE
        if template == "POWER_JOINT_BREAK_EVEN":
            return Decimal(1) / _positive(inputs.get("gross_multiplier"), "gross_multiplier"), PROBABILITY_TOLERANCE
        if template == "EQUAL_LEG_BREAK_EVEN":
            multiplier = _positive(inputs.get("gross_multiplier"), "gross_multiplier")
            legs = _positive(inputs.get("legs"), "legs")
            if legs != legs.to_integral_value():
                raise ValueError("legs must be an integer")
            return context.power(Decimal(1) / multiplier, Decimal(1) / legs), PROBABILITY_TOLERANCE
        if template in {"GROSS_RETURN", "NET_PROFIT"}:
            stake = _positive(inputs.get("stake"), "stake")
            multiplier = _positive(inputs.get("gross_multiplier"), "gross_multiplier")
            value = stake * multiplier if template == "GROSS_RETURN" else stake * (multiplier - Decimal(1))
            return value, CURRENCY_TOLERANCE
        if template in {"EXPECTED_GROSS_MULTIPLIER", "EXPECTED_VALUE_PER_DOLLAR"}:
            states = inputs.get("states")
            if not isinstance(states, list) or not states:
                raise ValueError("states must be a non-empty list")
            total_p = Decimal(0)
            expected = Decimal(0)
            for index, state in enumerate(states):
                if not isinstance(state, Mapping):
                    raise ValueError(f"states[{index}] must be an object")
                p = _probability(state.get("probability"), f"states[{index}].probability")
                payout = _decimal(state.get("payout_multiplier"), f"states[{index}].payout_multiplier")
                if payout < 0:
                    raise ValueError("payout_multiplier must be non-negative")
                total_p += p
                expected += p * payout
            if abs(total_p - Decimal(1)) > PROBABILITY_TOLERANCE:
                raise ValueError("state probabilities must normalize to 1")
            return (expected - Decimal(1) if template == "EXPECTED_VALUE_PER_DOLLAR" else expected), GENERAL_TOLERANCE
        if template == "FIXED_ODDS_EXPECTED_PROFIT":
            p_win = _probability(inputs.get("p_win"), "p_win")
            p_loss = _probability(inputs.get("p_loss"), "p_loss")
            profit = _positive(inputs.get("profit_multiple"), "profit_multiple")
            return p_win * profit - p_loss, GENERAL_TOLERANCE
        if template == "FIXED_ODDS_BREAK_EVEN_UNCONDITIONAL":
            refundable = _probability(inputs.get("refundable_probability"), "refundable_probability")
            profit = _positive(inputs.get("profit_multiple"), "profit_multiple")
            return (Decimal(1) - refundable) / (Decimal(1) + profit), PROBABILITY_TOLERANCE
    raise ValueError(f"unsupported arithmetic template: {template}")


def _hash_inputs(template_id: str, inputs: Mapping[str, Any]) -> str:
    payload = json.dumps({"template_id": str(template_id).upper(), "inputs": inputs}, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


def _audit_payload_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(canonical.encode("utf-8")).hexdigest()


def audit_claims(claims: Sequence[Mapping[str, Any]], *, provider: Any | None = None, required: bool = True) -> dict[str, Any]:
    """Verify server-owned arithmetic claims locally with Decimal precision."""
    if not required:
        return {"verdict": NOT_REQUIRED, "provider": PROVIDER, "audit_required": False, "receipts": [], "blocks_model_probability": False, "can_execute": False}
    if not claims:
        return {"verdict": INPUT_INVALID, "provider": PROVIDER, "audit_required": True, "receipts": [], "blocks_model_probability": False, "can_execute": False}

    receipts: list[dict[str, Any]] = []
    verdict = PASS
    for claim in claims:
        template_id = str(claim.get("template_id") or "").upper()
        inputs = claim.get("inputs")
        if not isinstance(inputs, Mapping):
            verdict = INPUT_INVALID
            receipts.append({"claim_id": claim.get("claim_id"), "template_id": template_id, "status": INPUT_INVALID})
            continue
        try:
            expected, tolerance = _calculate(template_id, inputs)
            reported = _decimal(claim.get("reported_result"), "reported_result")
            delta = abs(expected - reported)
            status = PASS if delta <= tolerance else CALCULATION_MISMATCH
        except Exception as exc:
            expected = reported = delta = tolerance = None
            status = INPUT_INVALID if isinstance(exc, ValueError) else OUTPUT_INVALID
        if status != PASS and verdict == PASS:
            verdict = status
        receipts.append({
            "claim_id": claim.get("claim_id"),
            "template_id": template_id,
            "input_hash": _hash_inputs(template_id, inputs),
            "expected_result": str(expected) if expected is not None else None,
            "reported_result": str(reported) if reported is not None else None,
            "delta": str(delta) if delta is not None else None,
            "tolerance": str(tolerance) if tolerance is not None else None,
            "status": status,
        })
    return {
        "verdict": verdict,
        "provider": PROVIDER,
        "audit_required": True,
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "receipts": receipts,
        "external_transport_used": False,
        "blocks_model_probability": False,
        "can_execute": False,
    }


def persist_audit(client: Any, *, prediction_id: str, audit: Mapping[str, Any]) -> dict[str, Any]:
    """Persist audit if the legacy ledger accepts it; failure remains downstream-only."""
    core = {
        "provider": str(audit.get("provider") or PROVIDER),
        "verdict": str(audit.get("verdict") or OUTPUT_INVALID),
        "audit_required": bool(audit.get("audit_required")),
        "audited_at": str(audit.get("audited_at") or datetime.now(timezone.utc).isoformat()),
        "receipts": list(audit.get("receipts") or []),
        "blocks_model_probability": False,
        "can_execute": False,
    }
    payload = {
        "prediction_id": str(prediction_id),
        **core,
        "claim_count": len(core["receipts"]),
        "audit_payload_hash": _audit_payload_hash(core),
    }
    result = client.table("wow_wolfram_arithmetic_audits").insert(payload).execute()
    rows = getattr(result, "data", None)
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise RuntimeError("Python arithmetic audit ledger insert was not proven")
    stored = rows[0]
    if not stored.get("arithmetic_audit_id") or stored.get("audit_payload_hash") != payload["audit_payload_hash"]:
        raise RuntimeError("Python arithmetic audit ledger receipt was invalid")
    return stored


__all__ = [
    "PASS", "NOT_REQUIRED", "INPUT_INVALID", "OUTPUT_INVALID",
    "CALCULATION_MISMATCH", "LEDGER_WRITE_UNPROVEN", "PROVIDER",
    "audit_enabled", "audit_claims", "persist_audit", "readiness",
]
