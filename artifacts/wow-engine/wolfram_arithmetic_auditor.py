"""Server-owned WolframAlpha arithmetic verification for WOW V17.

The fitted specialist remains the sole source of sporting probability.  This
module independently verifies deterministic probability transformations and
market/payout arithmetic.  Callers choose from a closed template registry;
arbitrary Wolfram expressions are never accepted from request payloads.

Provider failure is intentionally scoped to the downstream arithmetic claim.
It must never be rewritten as MODEL_UNAVAILABLE or erase a completed sporting
probability package.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
import json
import os
import re
from typing import Any, Mapping, Protocol, Sequence

import httpx


PROVIDER = "WOLFRAM_ALPHA"
PASS = "PASS"
NOT_REQUIRED = "NOT_REQUIRED"
DISABLED = "WOLFRAM_AUDIT_DISABLED"
INPUT_INVALID = "WOLFRAM_AUDIT_INPUT_INVALID"
UNAVAILABLE = "WOLFRAM_AUDIT_UNAVAILABLE"
OUTPUT_INVALID = "WOLFRAM_OUTPUT_INVALID"
CALCULATION_MISMATCH = "WOLFRAM_CALCULATION_MISMATCH"
LEDGER_WRITE_UNPROVEN = "WOLFRAM_AUDIT_LEDGER_WRITE_UNPROVEN"

PROBABILITY_TOLERANCE = Decimal("0.000001")
GENERAL_TOLERANCE = Decimal("0.000001")
CURRENCY_TOLERANCE = Decimal("0.01")

_ENDPOINT = "https://api.wolframalpha.com/v2/query"
_NUMERIC = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


class WolframAuditError(RuntimeError):
    code = UNAVAILABLE


class WolframUnavailable(WolframAuditError):
    code = UNAVAILABLE


class WolframOutputInvalid(WolframAuditError):
    code = OUTPUT_INVALID


class NumericProvider(Protocol):
    def evaluate(self, expression: str) -> Decimal: ...


def audit_enabled() -> bool:
    return os.getenv("WOW_WOLFRAM_ARITHMETIC_AUDIT_ENABLED", "0") == "1"


def readiness() -> dict[str, Any]:
    enabled = audit_enabled()
    configured = bool(os.getenv("WOLFRAM_ALPHA_APP_ID"))
    return {
        "provider": PROVIDER,
        "enabled": enabled,
        "configured": configured,
        "status": "READY" if enabled and configured else ("CREDENTIAL_MISSING" if enabled else "DISABLED"),
        "blocks_model_probability": False,
        "can_execute": False,
    }


@dataclass(frozen=True)
class WolframAlphaClient:
    app_id: str
    timeout_seconds: float = 5.0

    @classmethod
    def from_environment(cls) -> "WolframAlphaClient":
        app_id = os.getenv("WOLFRAM_ALPHA_APP_ID", "").strip()
        if not app_id:
            raise WolframUnavailable("WOLFRAM_ALPHA_APP_ID is not configured")
        try:
            timeout = float(os.getenv("WOW_WOLFRAM_TIMEOUT_SECONDS", "5"))
        except ValueError as exc:
            raise WolframUnavailable("WOW_WOLFRAM_TIMEOUT_SECONDS is invalid") from exc
        if timeout <= 0 or timeout > 30:
            raise WolframUnavailable("WOW_WOLFRAM_TIMEOUT_SECONDS is outside (0,30]")
        return cls(app_id=app_id, timeout_seconds=timeout)

    def evaluate(self, expression: str) -> Decimal:
        try:
            response = httpx.get(
                _ENDPOINT,
                params={
                    "appid": self.app_id,
                    "input": f"N[{expression},20]",
                    "output": "json",
                    "format": "plaintext",
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WolframUnavailable("WolframAlpha transport failed") from exc

        query = payload.get("queryresult") if isinstance(payload, dict) else None
        if not isinstance(query, dict) or query.get("success") is not True:
            raise WolframOutputInvalid("WolframAlpha did not return a successful query result")

        pods = query.get("pods")
        if not isinstance(pods, list):
            raise WolframOutputInvalid("WolframAlpha pods are missing")
        ordered = sorted(
            (pod for pod in pods if isinstance(pod, dict)),
            key=lambda pod: 0 if pod.get("primary") is True else 1,
        )
        for pod in ordered:
            subpods = pod.get("subpods")
            if not isinstance(subpods, list):
                continue
            for subpod in subpods:
                text = subpod.get("plaintext") if isinstance(subpod, dict) else None
                if isinstance(text, str) and _NUMERIC.fullmatch(text.strip().replace(",", "")):
                    try:
                        return Decimal(text.strip().replace(",", ""))
                    except InvalidOperation:
                        continue
        raise WolframOutputInvalid("WolframAlpha returned no unambiguous numeric result")


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


def _expr(value: Decimal) -> str:
    return format(value, "f")


def _sum_expression(values: Sequence[Decimal]) -> str:
    if not values:
        raise ValueError("at least one value is required")
    return "(" + "+".join(_expr(value) for value in values) + ")"


def _american_implied(odds: Decimal) -> tuple[Decimal, str]:
    if odds == 0:
        raise ValueError("american_odds must be non-zero")
    if odds > 0:
        return Decimal(100) / (odds + Decimal(100)), f"(100/({_expr(odds)}+100))"
    magnitude = abs(odds)
    return magnitude / (magnitude + Decimal(100)), f"({_expr(magnitude)}/({_expr(magnitude)}+100))"


def _calculate(template_id: str, inputs: Mapping[str, Any]) -> tuple[Decimal, str, Decimal]:
    template = str(template_id or "").strip().upper()
    with localcontext() as context:
        context.prec = 40

        if template == "PROBABILITY_TOTAL":
            values = [_probability(value, "probabilities[]") for value in inputs.get("probabilities", [])]
            return sum(values, Decimal(0)), _sum_expression(values), PROBABILITY_TOLERANCE

        if template == "PROBABILITY_COMPLEMENT":
            probability = _probability(inputs.get("probability"), "probability")
            return Decimal(1) - probability, f"(1-{_expr(probability)})", PROBABILITY_TOLERANCE

        if template == "AMERICAN_ODDS_IMPLIED_PROBABILITY":
            odds = _decimal(inputs.get("american_odds"), "american_odds")
            value, expression = _american_implied(odds)
            return value, expression, PROBABILITY_TOLERANCE

        if template == "DECIMAL_ODDS_IMPLIED_PROBABILITY":
            odds = _positive(inputs.get("decimal_odds"), "decimal_odds")
            if odds <= 1:
                raise ValueError("decimal_odds must exceed 1")
            return Decimal(1) / odds, f"(1/{_expr(odds)})", PROBABILITY_TOLERANCE

        if template == "MARKET_HOLD":
            side_a = _probability(inputs.get("q_a"), "q_a")
            side_b = _probability(inputs.get("q_b"), "q_b")
            return side_a + side_b - Decimal(1), f"({_expr(side_a)}+{_expr(side_b)}-1)", PROBABILITY_TOLERANCE

        if template == "TWO_WAY_NO_VIG":
            selected = _probability(inputs.get("q_selected"), "q_selected")
            opposing = _probability(inputs.get("q_opposing"), "q_opposing")
            denominator = selected + opposing
            if denominator <= 0:
                raise ValueError("two-way implied probabilities cannot both be zero")
            return selected / denominator, f"({_expr(selected)}/({_expr(selected)}+{_expr(opposing)}))", PROBABILITY_TOLERANCE

        if template == "PUSH_ADJUSTED_PROBABILITY":
            push = _probability(inputs.get("p_push"), "p_push")
            conditional = _probability(inputs.get("p_conditional"), "p_conditional")
            return (Decimal(1) - push) * conditional, f"((1-{_expr(push)})*{_expr(conditional)})", PROBABILITY_TOLERANCE

        if template == "POWER_JOINT_BREAK_EVEN":
            multiplier = _positive(inputs.get("gross_multiplier"), "gross_multiplier")
            return Decimal(1) / multiplier, f"(1/{_expr(multiplier)})", PROBABILITY_TOLERANCE

        if template == "EQUAL_LEG_BREAK_EVEN":
            multiplier = _positive(inputs.get("gross_multiplier"), "gross_multiplier")
            legs = _positive(inputs.get("legs"), "legs")
            if legs != legs.to_integral_value():
                raise ValueError("legs must be an integer")
            exponent = Decimal(1) / legs
            return context.power(Decimal(1) / multiplier, exponent), f"((1/{_expr(multiplier)})^(1/{_expr(legs)}))", PROBABILITY_TOLERANCE

        if template in {"GROSS_RETURN", "NET_PROFIT"}:
            stake = _positive(inputs.get("stake"), "stake")
            multiplier = _positive(inputs.get("gross_multiplier"), "gross_multiplier")
            if template == "GROSS_RETURN":
                return stake * multiplier, f"({_expr(stake)}*{_expr(multiplier)})", CURRENCY_TOLERANCE
            return stake * (multiplier - Decimal(1)), f"({_expr(stake)}*({_expr(multiplier)}-1))", CURRENCY_TOLERANCE

        if template in {"EXPECTED_GROSS_MULTIPLIER", "EXPECTED_VALUE_PER_DOLLAR"}:
            raw_states = inputs.get("states")
            if not isinstance(raw_states, list) or not raw_states:
                raise ValueError("states must be a non-empty list")
            terms: list[Decimal] = []
            expressions: list[str] = []
            probability_total = Decimal(0)
            for index, state in enumerate(raw_states):
                if not isinstance(state, Mapping):
                    raise ValueError(f"states[{index}] must be an object")
                probability = _probability(state.get("probability"), f"states[{index}].probability")
                payout = _decimal(state.get("payout_multiplier"), f"states[{index}].payout_multiplier")
                if payout < 0:
                    raise ValueError(f"states[{index}].payout_multiplier must be non-negative")
                probability_total += probability
                terms.append(probability * payout)
                expressions.append(f"({_expr(probability)}*{_expr(payout)})")
            if abs(probability_total - Decimal(1)) > PROBABILITY_TOLERANCE:
                raise ValueError("state probabilities must normalize to 1")
            expected = sum(terms, Decimal(0))
            expression = "(" + "+".join(expressions) + ")"
            if template == "EXPECTED_VALUE_PER_DOLLAR":
                return expected - Decimal(1), f"({expression}-1)", GENERAL_TOLERANCE
            return expected, expression, GENERAL_TOLERANCE

        if template == "FIXED_ODDS_EXPECTED_PROFIT":
            p_win = _probability(inputs.get("p_win"), "p_win")
            p_loss = _probability(inputs.get("p_loss"), "p_loss")
            profit_multiple = _positive(inputs.get("profit_multiple"), "profit_multiple")
            return p_win * profit_multiple - p_loss, f"({_expr(p_win)}*{_expr(profit_multiple)}-{_expr(p_loss)})", GENERAL_TOLERANCE

        if template == "FIXED_ODDS_BREAK_EVEN_UNCONDITIONAL":
            refundable = _probability(inputs.get("refundable_probability"), "refundable_probability")
            profit_multiple = _positive(inputs.get("profit_multiple"), "profit_multiple")
            return (Decimal(1) - refundable) / (Decimal(1) + profit_multiple), f"((1-{_expr(refundable)})/(1+{_expr(profit_multiple)}))", PROBABILITY_TOLERANCE

    raise ValueError(f"unsupported arithmetic template: {template}")


def _hash_inputs(template_id: str, inputs: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {"template_id": str(template_id).upper(), "inputs": inputs},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _audit_payload_hash(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(canonical.encode("utf-8")).hexdigest()


def persist_audit(client: Any, *, prediction_id: str, audit: Mapping[str, Any]) -> dict[str, Any]:
    """Append one Wolfram audit attempt to the immutable Supabase ledger.

    The caller owns failure scoping.  This function raises when the database
    does not prove the insert so a successful provider response can never be
    represented as durably audited when only the HTTP response contained it.
    """
    audit_core = {
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
        **audit_core,
        "claim_count": len(audit_core["receipts"]),
        "audit_payload_hash": _audit_payload_hash(audit_core),
    }
    result = client.table("wow_wolfram_arithmetic_audits").insert(payload).execute()
    rows = getattr(result, "data", None)
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise RuntimeError("Wolfram arithmetic audit ledger insert was not proven")
    stored = rows[0]
    if not stored.get("arithmetic_audit_id") or stored.get("audit_payload_hash") != payload["audit_payload_hash"]:
        raise RuntimeError("Wolfram arithmetic audit ledger receipt was invalid")
    return stored


def audit_claims(
    claims: Sequence[Mapping[str, Any]],
    *,
    provider: NumericProvider | None = None,
    required: bool = True,
) -> dict[str, Any]:
    """Verify server-owned arithmetic claims against local and Wolfram results."""
    if not required:
        return {
            "verdict": NOT_REQUIRED,
            "provider": PROVIDER,
            "audit_required": False,
            "receipts": [],
            "blocks_model_probability": False,
            "can_execute": False,
        }
    if not claims:
        return {
            "verdict": INPUT_INVALID,
            "provider": PROVIDER,
            "audit_required": True,
            "audited_at": datetime.now(timezone.utc).isoformat(),
            "receipts": [],
            "blocks_model_probability": False,
            "can_execute": False,
        }

    try:
        verifier = provider or WolframAlphaClient.from_environment()
    except WolframAuditError as exc:
        return {
            "verdict": exc.code,
            "provider": PROVIDER,
            "audit_required": True,
            "audited_at": datetime.now(timezone.utc).isoformat(),
            "receipts": [],
            "blocks_model_probability": False,
            "can_execute": False,
        }

    receipts: list[dict[str, Any]] = []
    batch_verdict = PASS
    for index, claim in enumerate(claims):
        try:
            if not isinstance(claim, Mapping):
                raise ValueError("claim must be an object")
            template_id = str(claim.get("template_id") or "").strip().upper()
            inputs = claim.get("inputs")
            if not template_id or not isinstance(inputs, Mapping) or "reported_result" not in claim:
                raise ValueError("template_id, inputs and reported_result are required")
            local_result, expression, default_tolerance = _calculate(template_id, inputs)
            reported_result = _decimal(claim.get("reported_result"), "reported_result")
            requested_tolerance = claim.get("tolerance")
            tolerance = default_tolerance
            if requested_tolerance is not None:
                requested = abs(_decimal(requested_tolerance, "tolerance"))
                tolerance = min(default_tolerance, requested)
            provider_result = verifier.evaluate(expression)
            provider_delta = abs(local_result - provider_result)
            reported_delta = abs(local_result - reported_result)
            verdict = PASS if provider_delta <= tolerance and reported_delta <= tolerance else CALCULATION_MISMATCH
            if verdict != PASS:
                batch_verdict = CALCULATION_MISMATCH
            receipts.append({
                "claim_id": str(claim.get("claim_id") or f"claim-{index + 1}"),
                "template_id": template_id,
                "input_payload_hash": _hash_inputs(template_id, inputs),
                "local_result": str(local_result),
                "provider_result": str(provider_result),
                "reported_result": str(reported_result),
                "provider_delta": str(provider_delta),
                "reported_delta": str(reported_delta),
                "tolerance": str(tolerance),
                "verdict": verdict,
            })
        except WolframAuditError as exc:
            batch_verdict = exc.code
            receipts.append({
                "claim_id": str(claim.get("claim_id") or f"claim-{index + 1}") if isinstance(claim, Mapping) else f"claim-{index + 1}",
                "template_id": str(claim.get("template_id") or "UNKNOWN").upper() if isinstance(claim, Mapping) else "UNKNOWN",
                "verdict": exc.code,
            })
            break
        except (ValueError, InvalidOperation, TypeError, KeyError, ZeroDivisionError) as exc:
            batch_verdict = INPUT_INVALID
            receipts.append({
                "claim_id": str(claim.get("claim_id") or f"claim-{index + 1}") if isinstance(claim, Mapping) else f"claim-{index + 1}",
                "template_id": str(claim.get("template_id") or "UNKNOWN").upper() if isinstance(claim, Mapping) else "UNKNOWN",
                "verdict": INPUT_INVALID,
                "error_type": type(exc).__name__,
            })
            break

    return {
        "verdict": batch_verdict,
        "provider": PROVIDER,
        "audit_required": True,
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "receipts": receipts,
        "blocks_model_probability": False,
        "can_execute": False,
    }
