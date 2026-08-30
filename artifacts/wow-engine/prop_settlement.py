"""Governed exact-line prop settlement and push/void money math.

Settlement is a downstream objective. It never creates or modifies the sporting
model probability and can never authorize execution. Missing rules fail closed
as a settlement hold while preserving any completed model verdict.

Fixed-odds refund semantics and lineup-repricing semantics are intentionally
separate. A provider such as PrizePicks may resolve win/loss/tie/void behavior
while still requiring full lineup context before any payout or EV claim.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Optional


SETTLEMENT_RULE_UNRESOLVED = "WOW_HOLD_SETTLEMENT_RULE_UNRESOLVED"
LINE_MISMATCH = "WOW_HOLD_LINE_MISMATCH"
NO_VIG_UNAVAILABLE = "WOW_HOLD_NO_VIG_UNAVAILABLE"
LINEUP_PAYOUT_CONTEXT_REQUIRED = "WOW_HOLD_LINEUP_PAYOUT_CONTEXT_REQUIRED"

_ALLOWED_BOUNDARIES = {"GT", "GE", "LT", "LE"}
_ALLOWED_EQUALITY = {"PUSH", "WIN", "LOSS"}
_ALLOWED_VOID = {"NONE", "RETURN_STAKE", "REMOVE_LEG_REPRICE"}
_ALLOWED_MONEY = {"FIXED_ODDS_RETURN_STAKE", "LINEUP_CONTEXT_REQUIRED"}


@dataclass(frozen=True)
class SettlementRule:
    settlement_basis: str
    boundary_operator: str
    equality_treatment: str
    void_treatment: str
    rule_version: str
    source: str
    void_probability_mass: float = 0.0
    money_semantics: str = "FIXED_ODDS_RETURN_STAKE"


@dataclass(frozen=True)
class SettlementResult:
    status: str
    blocker: Optional[str]
    p_win: Optional[float]
    p_loss: Optional[float]
    p_push: Optional[float]
    p_void: Optional[float]
    graded_probability: Optional[float]
    conditional_win_probability: Optional[float]
    american_odds: Optional[float]
    profit_multiple: Optional[float]
    break_even_unconditional: Optional[float]
    break_even_conditional_graded: Optional[float]
    expected_profit_per_unit_staked: Optional[float]
    rule_version: Optional[str]
    source: Optional[str]
    void_treatment: Optional[str] = None
    money_semantics: Optional[str] = None
    money_context_required: bool = False
    can_execute: bool = False


def _hold(blocker: str) -> SettlementResult:
    return SettlementResult(
        status="HOLD",
        blocker=blocker,
        p_win=None,
        p_loss=None,
        p_push=None,
        p_void=None,
        graded_probability=None,
        conditional_win_probability=None,
        american_odds=None,
        profit_multiple=None,
        break_even_unconditional=None,
        break_even_conditional_graded=None,
        expected_profit_per_unit_staked=None,
        rule_version=None,
        source=None,
        void_treatment=None,
        money_semantics=None,
        money_context_required=False,
        can_execute=False,
    )


def american_profit_multiple(odds: float) -> float:
    value = float(odds)
    if not isfinite(value) or value == 0:
        raise ValueError("american odds must be finite and non-zero")
    return value / 100.0 if value > 0 else 100.0 / abs(value)


def audit_exact_line(*, candidate_line: float, quote_line: float, tolerance: float = 0.0) -> bool:
    tol = max(float(tolerance), 1e-9)
    return abs(float(candidate_line) - float(quote_line)) <= tol


def settle_prop_probability(
    *,
    direction: str,
    probability_more: float,
    probability_less: float,
    equality_probability: float,
    rule: Optional[SettlementRule],
    american_odds: Optional[float],
) -> SettlementResult:
    """Map the direction-free PMF into win/loss/push/void settlement masses.

    The model supplies MORE, LESS and equality mass. The settlement rule alone
    decides what happens to equality; market/no-vig values never enter this
    probability mapping. If void mass is non-zero, the non-void model masses
    are scaled by (1 - void_mass), making the unconditional result normalize.

    LINEUP_CONTEXT_REQUIRED means the settlement state may be resolved, but no
    per-leg break-even or EV is fabricated because the provider reprices the
    surrounding lineup rather than refunding this leg at fixed odds.
    """
    if rule is None:
        return _hold(SETTLEMENT_RULE_UNRESOLVED)

    boundary = str(rule.boundary_operator or "").upper()
    equality = str(rule.equality_treatment or "").upper()
    void_treatment = str(rule.void_treatment or "").upper()
    money_semantics = str(rule.money_semantics or "").upper()
    direction = str(direction or "").upper()
    if (
        boundary not in _ALLOWED_BOUNDARIES
        or equality not in _ALLOWED_EQUALITY
        or void_treatment not in _ALLOWED_VOID
        or money_semantics not in _ALLOWED_MONEY
        or not str(rule.settlement_basis or "").strip()
        or not str(rule.rule_version or "").strip()
        or not str(rule.source or "").strip()
    ):
        return _hold(SETTLEMENT_RULE_UNRESOLVED)
    if direction == "MORE" and boundary not in {"GT", "GE"}:
        return _hold(SETTLEMENT_RULE_UNRESOLVED)
    if direction == "LESS" and boundary not in {"LT", "LE"}:
        return _hold(SETTLEMENT_RULE_UNRESOLVED)
    if direction not in {"MORE", "LESS"}:
        return _hold(SETTLEMENT_RULE_UNRESOLVED)
    if void_treatment == "REMOVE_LEG_REPRICE" and money_semantics != "LINEUP_CONTEXT_REQUIRED":
        return _hold(SETTLEMENT_RULE_UNRESOLVED)

    try:
        p_more = float(probability_more)
        p_less = float(probability_less)
        p_equal = float(equality_probability)
        p_void = float(rule.void_probability_mass)
    except (TypeError, ValueError):
        return _hold(SETTLEMENT_RULE_UNRESOLVED)
    if any(not isfinite(v) or v < 0 or v > 1 for v in (p_more, p_less, p_equal, p_void)):
        return _hold(SETTLEMENT_RULE_UNRESOLVED)
    if abs((p_more + p_less + p_equal) - 1.0) > 1e-9:
        return _hold(SETTLEMENT_RULE_UNRESOLVED)
    if p_void > 0 and void_treatment not in {"RETURN_STAKE", "REMOVE_LEG_REPRICE"}:
        return _hold(SETTLEMENT_RULE_UNRESOLVED)

    p_win = p_more if direction == "MORE" else p_less
    p_loss = p_less if direction == "MORE" else p_more
    p_push = 0.0
    if equality == "WIN":
        p_win += p_equal
    elif equality == "LOSS":
        p_loss += p_equal
    else:
        p_push = p_equal

    nonvoid = 1.0 - p_void
    p_win *= nonvoid
    p_loss *= nonvoid
    p_push *= nonvoid
    total = p_win + p_loss + p_push + p_void
    if abs(total - 1.0) > 1e-9:
        return _hold(SETTLEMENT_RULE_UNRESOLVED)

    graded = p_win + p_loss
    conditional = p_win / graded if graded > 0 else None
    profit_multiple = None
    be_unconditional = None
    be_conditional = None
    ev = None
    normalized_odds = None
    money_context_required = money_semantics == "LINEUP_CONTEXT_REQUIRED"

    if american_odds is not None:
        try:
            normalized_odds = float(american_odds)
            if not isfinite(normalized_odds) or normalized_odds == 0:
                normalized_odds = None
        except (TypeError, ValueError):
            normalized_odds = None

    if normalized_odds is not None and not money_context_required:
        try:
            profit_multiple = american_profit_multiple(normalized_odds)
        except (TypeError, ValueError):
            profit_multiple = None
        if profit_multiple is not None:
            # Fixed-odds PUSH/RETURN_STAKE semantics only: wins make profit,
            # losses lose stake, and push/void mass returns stake.
            ev = p_win * profit_multiple - p_loss
            refundable_mass = p_push + p_void
            be_unconditional = (1.0 - refundable_mass) / (1.0 + profit_multiple)
            be_conditional = 1.0 / (1.0 + profit_multiple)

    return SettlementResult(
        status="PASS",
        blocker=None,
        p_win=p_win,
        p_loss=p_loss,
        p_push=p_push,
        p_void=p_void,
        graded_probability=graded,
        conditional_win_probability=conditional,
        american_odds=normalized_odds,
        profit_multiple=profit_multiple,
        break_even_unconditional=be_unconditional,
        break_even_conditional_graded=be_conditional,
        expected_profit_per_unit_staked=ev,
        rule_version=rule.rule_version,
        source=rule.source,
        void_treatment=void_treatment,
        money_semantics=money_semantics,
        money_context_required=money_context_required,
        can_execute=False,
    )


def settlement_self_acceptance() -> bool:
    """Deterministic runtime proof for push/void/refund and lineup semantics."""
    standard = SettlementRule(
        settlement_basis="FULL_GAME_STAT",
        boundary_operator="GT",
        equality_treatment="PUSH",
        void_treatment="RETURN_STAKE",
        rule_version="SELF_ACCEPTANCE_V1",
        source="WOW_DETERMINISTIC_FIXTURE",
    )
    result = settle_prop_probability(
        direction="MORE",
        probability_more=0.55,
        probability_less=0.35,
        equality_probability=0.10,
        rule=standard,
        american_odds=-110,
    )
    if result.status != "PASS" or result.p_push != 0.10:
        return False
    expected_profit = 0.55 * (100.0 / 110.0) - 0.35
    if result.expected_profit_per_unit_staked is None or abs(result.expected_profit_per_unit_staked - expected_profit) > 1e-12:
        return False

    void_rule = SettlementRule(
        settlement_basis="FULL_GAME_STAT",
        boundary_operator="LT",
        equality_treatment="PUSH",
        void_treatment="RETURN_STAKE",
        rule_version="SELF_ACCEPTANCE_V1",
        source="WOW_DETERMINISTIC_FIXTURE",
        void_probability_mass=0.20,
    )
    void_result = settle_prop_probability(
        direction="LESS",
        probability_more=0.40,
        probability_less=0.60,
        equality_probability=0.0,
        rule=void_rule,
        american_odds=120,
    )
    if not (
        void_result.status == "PASS"
        and abs((void_result.p_win or 0) - 0.48) < 1e-12
        and abs((void_result.p_loss or 0) - 0.32) < 1e-12
        and abs((void_result.p_void or 0) - 0.20) < 1e-12
        and abs(sum(v or 0 for v in (void_result.p_win, void_result.p_loss, void_result.p_push, void_result.p_void)) - 1.0) < 1e-12
    ):
        return False

    lineup_rule = SettlementRule(
        settlement_basis="PRIZEPICKS_OFFICIAL_SCORING",
        boundary_operator="GT",
        equality_treatment="PUSH",
        void_treatment="REMOVE_LEG_REPRICE",
        rule_version="SELF_ACCEPTANCE_LINEUP_V1",
        source="WOW_DETERMINISTIC_FIXTURE",
        void_probability_mass=0.10,
        money_semantics="LINEUP_CONTEXT_REQUIRED",
    )
    lineup_result = settle_prop_probability(
        direction="MORE",
        probability_more=0.55,
        probability_less=0.40,
        equality_probability=0.05,
        rule=lineup_rule,
        american_odds=-110,
    )
    return (
        lineup_result.status == "PASS"
        and lineup_result.money_context_required is True
        and lineup_result.expected_profit_per_unit_staked is None
        and lineup_result.break_even_unconditional is None
        and abs(sum(v or 0 for v in (lineup_result.p_win, lineup_result.p_loss, lineup_result.p_push, lineup_result.p_void)) - 1.0) < 1e-12
    )
