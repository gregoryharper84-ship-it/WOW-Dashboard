"""Read-only postmortem of a user-reported Kalshi five-market NBA winner.

This is NOT the production combo evaluator. The active Recovery Mode/size
governors remain the only admission gates. This module cannot certify a model,
infer entry-time prices, approve a combo or create an order.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

CASE_STATUS = "POSTMORTEM_RESEARCH_ONLY"
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
_CENT = Decimal("0.01")


def _cents(value: Any, *, positive: bool = False) -> int:
    """Reject booleans, NaN/inf, negative values and sub-cent inputs."""
    if value is None or isinstance(value, bool):
        raise ValueError("amount is missing or invalid")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount != amount.quantize(_CENT):
            raise ValueError("amount must be finite and specified to cents")
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("amount must be finite and specified to cents") from exc
    if amount < 0 or (positive and amount == 0):
        raise ValueError("amount must be positive" if positive else "amount cannot be negative")
    return int(amount * 100)


def diagnostic_condition_from_final_scores(leg: Mapping[str, Any], scores: Mapping[str, int]) -> bool:
    """Check the screenshot's mathematical proposition, NOT official Kalshi settlement.

    An exchange contract may carry additional rules (postponement/void, OT,
    resolution source). Official contract terms and settlement must be checked
    separately before any independent-grade claim.
    """
    kind = leg.get("market_type")
    teams = leg.get("teams")
    if not isinstance(teams, list) or len(teams) != 2 or len(set(teams)) != 2:
        raise ValueError("two distinct canonical teams required")
    if set(scores) != set(teams) or any(type(x) is not int or x < 0 for x in scores.values()):
        raise ValueError("final scores must be nonnegative integers for both exact teams")
    if scores[teams[0]] == scores[teams[1]]:
        raise ValueError("unresolved final tie")
    if kind == "MONEYLINE":
        selected = leg.get("selection")
        if leg.get("side") != "YES" or selected not in teams:
            raise ValueError("moneyline requires explicit YES and selected team")
        return scores[selected] > scores[next(team for team in teams if team != selected)]
    if kind == "WIN_MARGIN_GREATER_THAN_NO":
        proposition_team = leg.get("proposition_team")
        if leg.get("side") != "NO" or proposition_team not in teams:
            raise ValueError("margin NO requires exact team and NO side")
        try:
            threshold = Decimal(str(leg.get("margin_threshold")))
        except (TypeError, InvalidOperation) as exc:
            raise ValueError("invalid margin threshold") from exc
        if not threshold.is_finite() or threshold < 0:
            raise ValueError("invalid margin threshold")
        opponent = next(team for team in teams if team != proposition_team)
        return Decimal(scores[proposition_team] - scores[opponent]) <= threshold
    raise ValueError("unsupported market type")


def audit_user_reported_case(case: Mapping[str, Any]) -> dict[str, Any]:
    """Review historical arithmetic; exclude outcome from forward calibration.

    This deliberately never accepts a model probability from the screenshot,
    calculates an EV edge, or makes a production qualification decision.
    """
    economics = case.get("economics")
    legs = case.get("legs")
    if not isinstance(economics, Mapping) or not isinstance(legs, list) or len(legs) != 5:
        raise ValueError("five exact legs and economics are required")
    if case.get("evidence_type") != "USER_SUPPLIED_CLOSED_POSITION_SCREENSHOT":
        raise ValueError("unsupported provenance")
    if len({str(leg.get("matchup_id")) for leg in legs if isinstance(leg, dict)}) != 5:
        raise ValueError("five distinct exact matchup labels required")
    if any(not isinstance(leg, dict) or leg.get("reported_result") != "WIN" for leg in legs):
        raise ValueError("this case only records five screenshot-reported winning legs")
    if sum(leg.get("market_type") == "MONEYLINE" and leg.get("side") == "YES" for leg in legs) != 4:
        raise ValueError("expected four YES moneylines")
    if sum(leg.get("market_type") == "WIN_MARGIN_GREATER_THAN_NO" and leg.get("side") == "NO" for leg in legs) != 1:
        raise ValueError("expected one exact margin NO proposition")
    cost = _cents(economics.get("cost_usd"), positive=True)
    payout = _cents(economics.get("payout_usd"), positive=True)
    claimed_profit = _cents(economics.get("profit_usd"))
    if payout - cost != claimed_profit:
        raise ValueError("payout minus cost does not match claimed profit")
    if claimed_profit <= 0:
        raise ValueError("case is not a reported net win")
    expense, returned = Decimal(cost), Decimal(payout)
    return {
        "case_id": case.get("case_id"),
        "status": CASE_STATUS,
        "evidence_grade": "USER_SCREENSHOT_ONLY_NOT_INDEPENDENTLY_VERIFIED",
        "reported_winning_legs": 5,
        "cost_usd": f"{Decimal(cost) / 100:.2f}",
        "payout_usd": f"{Decimal(payout) / 100:.2f}",
        "net_profit_usd": f"{Decimal(claimed_profit) / 100:.2f}",
        "payout_multiple": str((returned / expense).quantize(Decimal("0.000001"))),
        "profit_percent": str(((Decimal(claimed_profit) / expense) * 100).quantize(Decimal("0.01"))),
        "combo_break_even_percent": str(((expense / returned) * 100).quantize(Decimal("0.01"))),
        "combo_true_probability": None,
        "fitted_model_probability": None,
        "net_expected_value": None,
        "entry_time_leg_quotes": None,
        "independent_settlement_verified": False,
        "immutable_pregame_model_receipts_present": False,
        "model_calibration_eligible": False,
        "promotion_allowed": False,
        "combo_structure_gate": "UNCHANGED_FOUR_PLUS_HARD_REJECT",
        "can_execute": False,
        "dry_run_only": True,
        "execution_rule": EXECUTION_RULE,
        "missing_evidence": [
            "EXACT_KALSHI_CONTRACT_TICKERS_AND_RULES",
            "EXECUTED_COMBO_PRICE_AND_FEES_BREAKDOWN",
            "PREGAME_TIMESTAMPED_MODEL_AND_MARKET_SNAPSHOTS",
            "DEPENDENCY_AWARE_JOINT_DISTRIBUTION_AND_LOWER_BOUND",
            "INDEPENDENT_OFFICIAL_MARKET_SETTLEMENT_RECEIPTS",
            "UNTOUCHED_FORWARD_LOSERS_AND_WINNERS",
        ],
    }
