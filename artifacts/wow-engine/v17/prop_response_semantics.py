"""V17 prop response semantic adapter.

This is presentation/reconciliation governance only. It does not score, calibrate,
change a probability, relax a threshold, or authorize execution.

It makes two contracts explicit at the Action boundary:
1. ``probability_rank_eligible`` is a dedicated field and is always false for
   ``RESEARCH_INTEREST`` regardless of any stale inner ``rank_eligible`` value.
2. Pick Request telemetry reports orthogonal dimensions (attempted/completed,
   valid packages, rank eligibility, input/capability/scorer/output failures,
   terminal classes, and downstream market/money holds) rather than a fake funnel.
"""
from __future__ import annotations

import math
import sys
from typing import Any


def _finite_probability(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or not 0.0 < parsed < 1.0:
        return None
    return parsed


def _valid_calibrated_package(outcome: dict[str, Any]) -> bool:
    result = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
    prediction = result.get("prediction") if isinstance(result.get("prediction"), dict) else {}
    p = _finite_probability(prediction.get("calibrated_probability"))
    lb = _finite_probability(prediction.get("calibrated_probability_lower_bound"))
    return bool(p is not None and lb is not None and lb <= p)


def _scoring_attempted(outcome: dict[str, Any]) -> bool:
    if outcome.get("model_evaluated") is True or isinstance(outcome.get("result"), dict):
        return True
    return str(outcome.get("terminal_label") or "").upper() in {
        "MODEL_SCORER_FAILED",
        "MODEL_OUTPUT_INVALID",
    }


def _rank_value(payload: dict[str, Any]) -> bool:
    terminal = str(payload.get("terminal_label") or "").upper()
    if terminal == "RESEARCH_INTEREST":
        return False
    return bool(payload.get("probability_rank_eligible", payload.get("rank_eligible", False)))


def _dimensioned_reconciliation(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    terminal_counts: dict[str, int] = {}
    for outcome in outcomes:
        terminal = str(outcome.get("terminal_label") or "UNKNOWN").upper()
        terminal_counts[terminal] = terminal_counts.get(terminal, 0) + 1

    return {
        "rows_in": len(outcomes),
        "scoring_attempted": sum(1 for row in outcomes if _scoring_attempted(row)),
        "scoring_completed": sum(1 for row in outcomes if row.get("model_evaluated") is True),
        "valid_probability_packages": sum(1 for row in outcomes if _valid_calibrated_package(row)),
        "probability_rank_eligible_rows": sum(1 for row in outcomes if row.get("probability_rank_eligible") is True),
        "input_or_identity_failures": sum(1 for row in outcomes if str(row.get("terminal_label") or "").upper() == "MODEL_INPUTS_INSUFFICIENT"),
        "model_capability_failures": sum(
            1
            for row in outcomes
            if str(row.get("terminal_label") or "").upper() == "MODEL_UNAVAILABLE"
            and str(row.get("verdict_class") or "").upper() == "CAPABILITY_BLOCKED"
        ),
        "scorer_failures": sum(1 for row in outcomes if str(row.get("terminal_label") or "").upper() == "MODEL_SCORER_FAILED"),
        "model_output_failures": sum(1 for row in outcomes if str(row.get("terminal_label") or "").upper() == "MODEL_OUTPUT_INVALID"),
        "low_probability_terminals": terminal_counts.get("NO_LOW_PROBABILITY", 0),
        "research_interest_terminals": terminal_counts.get("RESEARCH_INTEREST", 0),
        "market_or_money_blocked_completed_rows": sum(
            1
            for row in outcomes
            if row.get("model_evaluated") is True
            and (
                str(row.get("verdict_class") or "").upper() == "MARKET_BLOCKED"
                or row.get("downstream_money_evaluation_allowed") is False
            )
        ),
        "final_approved_rows": terminal_counts.get("FINAL_APPROVED", 0),
        "terminal_counts": terminal_counts,
        "dimensions_are_orthogonal_not_a_funnel": True,
        "can_execute": False,
    }


def install_prop_response_semantics() -> bool:
    """Patch already-loaded production modules idempotently at V17 composition."""
    market = sys.modules.get("api_prod_market")
    pick = sys.modules.get("pick_request_runtime")
    changed = False

    if market is not None and not getattr(market, "_wow_v17_probability_rank_semantics_installed", False):
        original_probability_qualification = getattr(market, "_probability_qualification", None)
        if callable(original_probability_qualification):
            def probability_qualification(*args: Any, **kwargs: Any) -> dict[str, Any]:
                payload = dict(original_probability_qualification(*args, **kwargs))
                rank = _rank_value(payload)
                payload["rank_eligible"] = rank
                payload["probability_rank_eligible"] = rank
                payload["can_execute"] = False
                return payload

            market._probability_qualification = probability_qualification
            market._wow_v17_probability_rank_semantics_installed = True
            changed = True

    if pick is not None and not getattr(pick, "_wow_v17_prop_response_semantics_installed", False):
        original_completed = getattr(pick, "_completed_scored_outcome", None)
        original_terminal = getattr(pick, "_terminal", None)
        original_telemetry = getattr(pick, "_telemetry", None)

        if callable(original_completed):
            def completed_scored_outcome(*args: Any, **kwargs: Any) -> dict[str, Any]:
                outcome = dict(original_completed(*args, **kwargs))
                result = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
                qualification = result.get("probability_qualification") if isinstance(result.get("probability_qualification"), dict) else {}
                rank = _rank_value({
                    "terminal_label": outcome.get("terminal_label"),
                    "probability_rank_eligible": qualification.get("probability_rank_eligible"),
                    "rank_eligible": qualification.get("rank_eligible", outcome.get("rank_eligible")),
                })
                outcome["rank_eligible"] = rank
                outcome["probability_rank_eligible"] = rank
                outcome["can_execute"] = False
                return outcome

            pick._completed_scored_outcome = completed_scored_outcome

        if callable(original_terminal):
            def terminal(*args: Any, **kwargs: Any) -> dict[str, Any]:
                outcome = dict(original_terminal(*args, **kwargs))
                outcome["probability_rank_eligible"] = False
                outcome["can_execute"] = False
                return outcome

            pick._terminal = terminal

        if callable(original_telemetry):
            def telemetry(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
                payload = dict(original_telemetry(outcomes))
                payload["probability_reconciliation"] = _dimensioned_reconciliation(outcomes)
                return payload

            pick._telemetry = telemetry

        pick._wow_v17_prop_response_semantics_installed = True
        changed = True

    return changed


__all__ = ["install_prop_response_semantics"]
