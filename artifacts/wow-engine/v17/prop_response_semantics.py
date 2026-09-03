"""V17 prop probability/qualification architecture adapter.

Probability coverage is broad; recommendation qualification is selective; value
qualification is exact-price dependent; card qualification is portfolio dependent.
This adapter is installed only when the V17 package is composed into the runtime.
It never changes a fitted distribution, invents a probability, or authorizes execution.
"""
from __future__ import annotations

import math
import sys
from types import SimpleNamespace
from typing import Any

from qualification_policy_v2 import classify_prop_probability
from prop_terminal_reducer_v2 import reduce_prop_terminal


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
    ub = _finite_probability(prediction.get("calibrated_probability_upper_bound"))
    return bool(p is not None and lb is not None and ub is not None and lb <= p <= ub)


def _scoring_attempted(outcome: dict[str, Any]) -> bool:
    if outcome.get("model_evaluated") is True or isinstance(outcome.get("result"), dict):
        return True
    return str(outcome.get("terminal_label") or "").upper() in {"MODEL_SCORER_FAILED", "MODEL_OUTPUT_INVALID"}


def _rank_value(payload: dict[str, Any]) -> bool:
    if str(payload.get("terminal_label") or "").upper() == "RESEARCH_INTEREST":
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
        "model_qualified_rows": sum(1 for row in outcomes if row.get("model_qualified") is True),
        "probability_rank_eligible_rows": sum(1 for row in outcomes if row.get("probability_rank_eligible") is True),
        "input_or_identity_failures": sum(1 for row in outcomes if str(row.get("terminal_label") or "").upper() == "MODEL_INPUTS_INSUFFICIENT"),
        "model_capability_failures": sum(1 for row in outcomes if str(row.get("terminal_label") or "").upper() == "MODEL_UNAVAILABLE" and str(row.get("verdict_class") or "").upper() == "CAPABILITY_BLOCKED"),
        "scorer_failures": sum(1 for row in outcomes if str(row.get("terminal_label") or "").upper() == "MODEL_SCORER_FAILED"),
        "model_output_failures": sum(1 for row in outcomes if str(row.get("terminal_label") or "").upper() == "MODEL_OUTPUT_INVALID"),
        "low_probability_terminals": terminal_counts.get("NO_LOW_PROBABILITY", 0),
        "research_interest_terminals": terminal_counts.get("RESEARCH_INTEREST", 0),
        "market_or_money_blocked_completed_rows": sum(1 for row in outcomes if row.get("model_evaluated") is True and (str(row.get("verdict_class") or "").upper() == "MARKET_BLOCKED" or row.get("value_qualification_status") in {"PENDING_EXACT_PRICE", "PENDING_PAYOUT"})),
        "final_approved_rows": terminal_counts.get("FINAL_APPROVED", 0),
        "terminal_counts": terminal_counts,
        "dimensions_are_orthogonal_not_a_funnel": True,
        "can_execute": False,
    }


def _qualification_payload(row: Any, market_lane: dict[str, Any], money_lane: dict[str, Any], settlement_lane: dict[str, Any]) -> dict[str, Any]:
    qualification = classify_prop_probability(
        calibrated_probability=getattr(row, "calibrated_probability", None),
        calibrated_lower_bound=getattr(row, "calibrated_probability_lower_bound", None),
        calibrated_upper_bound=getattr(row, "calibrated_probability_upper_bound", None),
        calibration_status=getattr(row, "calibration_status", None),
        blockers=getattr(row, "data_gaps", None) or [],
        probability_publishable=bool(getattr(row, "probability_publishable", False)),
        model_quality_status="PASS",
        input_complete=True,
    )
    downstream_blockers = list(qualification.blockers)
    market_pass = market_lane.get("status") == "PASS"
    money_pass = money_lane.get("status") == "PASS"
    settlement_pass = settlement_lane.get("status") == "PASS"
    if not market_pass:
        downstream_blockers.append("MARKET_DATA_UNAVAILABLE")
    if not money_pass:
        downstream_blockers.append("PAYOUT_UNRESOLVED")
    if not settlement_pass:
        downstream_blockers.append("SETTLEMENT_RULE_UNRESOLVED")
    terminal = reduce_prop_terminal(proposed_label=qualification.terminal_label, blockers=downstream_blockers, model_evaluated=True)

    if not qualification.model_qualified:
        value_status = "NOT_ELIGIBLE_MODEL_NOT_QUALIFIED"
    elif not market_pass:
        value_status = "PENDING_EXACT_PRICE"
    elif not settlement_pass or not money_pass:
        value_status = "PENDING_PAYOUT"
    else:
        value_status = "READY_FOR_VALUE_EVALUATION"

    return {
        "terminal_label": terminal.terminal_label,
        "confidence_tier": qualification.confidence_tier,
        "model_qualification_status": qualification.model_qualification_status,
        "model_qualified": qualification.model_qualified,
        "qualification_policy_version": qualification.qualification_policy_version,
        "qualification_reasons": list(qualification.qualification_reasons),
        "uncertainty_width": qualification.uncertainty_width,
        "rank_eligible": qualification.rank_eligible,
        "probability_rank_eligible": qualification.rank_eligible,
        "model_supported": qualification.model_supported,
        "model_evaluated": terminal.model_evaluated,
        "pick_rejected": terminal.pick_rejected,
        "verdict_class": terminal.verdict_class,
        "infrastructure_blocked": terminal.infrastructure_blocked,
        "value_qualification_status": value_status,
        "card_qualification_status": "NOT_EVALUATED",
        "downstream_money_evaluation_allowed": qualification.model_qualified,
        "final_approved_allowed": False,
        "blockers": list(terminal.blockers),
        "can_execute": False,
    }


def _direction_assessment(direction: str, raw: float, calibration: Any, probability_publishable: bool) -> dict[str, Any]:
    qualification = classify_prop_probability(
        calibrated_probability=calibration.calibrated_probability,
        calibrated_lower_bound=calibration.lower_bound,
        calibrated_upper_bound=calibration.upper_bound,
        calibration_status=calibration.calibration_status,
        blockers=[],
        probability_publishable=probability_publishable,
        model_quality_status="PASS",
        input_complete=True,
    )
    return {
        "direction": direction,
        "raw_probability": raw,
        "calibrated_probability": calibration.calibrated_probability,
        "calibrated_lower_bound": calibration.lower_bound,
        "calibrated_upper_bound": calibration.upper_bound,
        "calibration_status": calibration.calibration_status,
        "confidence_tier": qualification.confidence_tier,
        "model_qualified": qualification.model_qualified,
        "model_qualification_status": qualification.model_qualification_status,
        "probability_rank_eligible": qualification.rank_eligible,
        "qualification_policy_version": qualification.qualification_policy_version,
        "uncertainty_width": qualification.uncertainty_width,
        "qualification_reasons": list(qualification.qualification_reasons),
        "value_qualification_status": "PENDING_EXACT_PRICE" if qualification.model_qualified else "NOT_ELIGIBLE_MODEL_NOT_QUALIFIED",
        "card_qualification_status": "NOT_EVALUATED",
        "can_execute": False,
    }


def install_prop_response_semantics() -> bool:
    market = sys.modules.get("api_prod_market")
    pick = sys.modules.get("pick_request_runtime")
    lane_patch = sys.modules.get("calibration_publication_api")
    changed = False

    if market is not None and not getattr(market, "_wow_v17_probability_architecture_installed", False):
        original_score_engine = getattr(market, "score_discrete_prop_end_to_end", None)
        if callable(original_score_engine):
            def score_engine(*args: Any, **kwargs: Any):
                result = original_score_engine(*args, **kwargs)
                import prop_discrete_engine as engine
                features = kwargs.get("features") or {}
                seed = int(kwargs.get("seed", 0))
                lp = result.line_probabilities
                more_cal = engine._calibrate(result.inference, float(lp.probability_more), lp, features, seed)
                less_cal = engine._calibrate(result.inference, float(lp.probability_less), lp, features, seed)
                return SimpleNamespace(row=result.row, inference=result.inference, line_probabilities=result.line_probabilities, calibration=result.calibration, directional_calibrations={"MORE": more_cal, "LESS": less_cal})
            market.score_discrete_prop_end_to_end = score_engine

        market._probability_qualification = _qualification_payload

        original_model_evidence = getattr(market, "_discrete_model_evidence", None)
        if callable(original_model_evidence):
            def model_evidence(result: Any) -> dict[str, Any]:
                payload = dict(original_model_evidence(result))
                calibrations = getattr(result, "directional_calibrations", {}) or {}
                lp = result.line_probabilities
                if "MORE" in calibrations and "LESS" in calibrations:
                    payload["directional_probability_assessments"] = {
                        "MORE": _direction_assessment("MORE", float(lp.probability_more), calibrations["MORE"], bool(getattr(result.row, "probability_publishable", False))),
                        "LESS": _direction_assessment("LESS", float(lp.probability_less), calibrations["LESS"], bool(getattr(result.row, "probability_publishable", False))),
                    }
                    payload["push_probability"] = float(lp.push_probability)
                    payload["directional_assessments_share_one_fitted_distribution"] = True
                return payload
            market._discrete_model_evidence = model_evidence

        market._wow_v17_probability_architecture_installed = True
        changed = True

    if lane_patch is not None and market is not None and not getattr(lane_patch, "_wow_v17_full_probability_under_publication_hold", False):
        def full_probability_under_publication_hold(market_api: Any, req: Any, *, model_identity: str, lane: dict[str, Any], preflight: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
            scored = dict(market_api.score_prop(req, model_identity))
            scored["governed_sporting_probability_completed"] = True
            scored["sporting_probability_publishable"] = True
            scored["official_publication_capability"] = preflight.get("governed_publication_capability") or "PHASE_A_HELD"
            scored["official_publication_blockers"] = list(blockers)
            scored["probability_publishable"] = True
            scored["governed_publishable"] = False
            scored["official_final_publishable"] = False
            scored["final_approved"] = False
            scored["can_execute"] = False
            return scored
        lane_patch._raw_specialist_research = full_probability_under_publication_hold
        lane_patch._wow_v17_full_probability_under_publication_hold = True
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
                rank = _rank_value({"terminal_label": outcome.get("terminal_label"), "probability_rank_eligible": qualification.get("probability_rank_eligible"), "rank_eligible": qualification.get("rank_eligible", outcome.get("rank_eligible"))})
                outcome["rank_eligible"] = rank
                outcome["probability_rank_eligible"] = rank
                outcome["model_qualified"] = bool(qualification.get("model_qualified"))
                outcome["value_qualification_status"] = qualification.get("value_qualification_status")
                outcome["card_qualification_status"] = qualification.get("card_qualification_status", "NOT_EVALUATED")
                outcome["can_execute"] = False
                return outcome
            pick._completed_scored_outcome = completed_scored_outcome
        if callable(original_terminal):
            def terminal(*args: Any, **kwargs: Any) -> dict[str, Any]:
                outcome = dict(original_terminal(*args, **kwargs))
                outcome["probability_rank_eligible"] = False
                outcome["model_qualified"] = False
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
