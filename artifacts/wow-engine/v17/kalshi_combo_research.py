"""Offline Kalshi combo-history audit and frozen-quote research diagnostics.

RESEARCH ONLY: no trading, approvals, portfolio mutation, stake sizing, or live
market access. The active WOW Kalshi recovery authority blocks all combos.
Numeric values supplied here are retrospective inputs, never authenticated
model evidence or a route to a governed terminal approval.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Iterable, Mapping

CAN_EXECUTE = False
CAN_ALLOCATE_CAPITAL = False
KALSHI_COMBOS_ALLOWED = False
RESEARCH_ONLY_STATUS = "RESEARCH_HOLD_KALSHI_RECOVERY_ACTIVE"


@dataclass(frozen=True)
class SettledCombo:
    identity: str
    leg_count: int
    total_cost: Decimal
    total_payout: Decimal


@dataclass(frozen=True)
class Audit:
    positions: int
    winning_positions: int
    total_cost: Decimal
    total_payout: Decimal
    net: Decimal
    roi: Decimal | None
    by_leg_count: Mapping[int, Mapping[str, object]]
    dates_verified: bool = False
    leg_failure_causes_verified: bool = False


def _money(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("money cannot be boolean")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid money") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("money must be finite and nonnegative")
    if amount.as_tuple().exponent < -2:
        raise ValueError("currency precision must be cents or coarser")
    return amount


def audit_settled(positions: Iterable[SettledCombo]) -> Audit:
    records = list(positions)
    ids: set[str] = set()
    summaries: dict[int, dict[str, object]] = {}
    total_cost = Decimal("0.00")
    total_payout = Decimal("0.00")
    winners = 0
    for r in records:
        if not r.identity or r.identity in ids:
            raise ValueError("missing or duplicate combo identity")
        ids.add(r.identity)
        if isinstance(r.leg_count, bool) or not isinstance(r.leg_count, int) or r.leg_count < 1:
            raise ValueError("leg_count must be positive integer")
        cost = _money(r.total_cost)
        payout = _money(r.total_payout)
        if cost == 0:
            raise ValueError("settled cost must be positive")
        total_cost += cost
        total_payout += payout
        winners += payout > cost
        group = summaries.setdefault(r.leg_count, {"count": 0, "wins": 0, "cost": Decimal("0"), "payout": Decimal("0")})
        group["count"] += 1
        group["wins"] += payout > cost
        group["cost"] += cost
        group["payout"] += payout
    groups = {k: {**v, "net": v["payout"] - v["cost"]} for k, v in sorted(summaries.items())}
    net = total_payout - total_cost
    return Audit(len(records), winners, total_cost, total_payout, net,
                 net / total_cost if total_cost else None, groups)


@dataclass(frozen=True)
class FrozenLeg:
    receipt_id: str
    exact_market_identity: str
    event_id: str
    event_start_utc: datetime
    model_asof_utc: datetime
    calibrated_probability: float
    calibrated_lower_bound: float
    calibration_status: str


@dataclass(frozen=True)
class FrozenComboQuote:
    combo_id: str
    legs: tuple[FrozenLeg, ...]
    joint_probability: float | None
    joint_lower_bound: float | None
    joint_receipt_id: str | None
    joint_calibration_status: str
    dependency_method: str | None
    quote_id: str | None
    quote_source: str
    quote_at_utc: datetime
    evaluated_at_utc: datetime
    total_cost_including_fees: Decimal
    maximum_payout: Decimal


@dataclass(frozen=True)
class ResearchVerdict:
    status: str
    blockers: tuple[str, ...]
    can_execute: bool
    joint_probability: float | None = None
    conservative_joint_probability: float | None = None
    break_even_probability: float | None = None
    conservative_return_diagnostic: Decimal | None = None


def _utc(t: datetime) -> datetime:
    if not isinstance(t, datetime) or t.tzinfo is None or t.utcoffset() is None:
        raise ValueError("timestamp must be timezone aware")
    return t.astimezone(timezone.utc)


def _prob(p: float | None) -> bool:
    return p is not None and isinstance(p, (float, int)) and not isinstance(p, bool) and isfinite(p) and 0 < p < 1


def evaluate_frozen_quote(q: FrozenComboQuote, quote_max_age_seconds: int = 180) -> ResearchVerdict:
    """Calculate only non-authoritative pretrade diagnostics on frozen evidence.

    Requires an externally produced, calibrated JOINT receipt: never multiplies
    marginal probabilities or infers RFQ prices. Even a valid calculation is
    blocked from live issuance by active Kalshi recovery governance.
    """
    blockers: list[str] = []
    if not q.combo_id or len(q.legs) < 2:
        blockers.append("INVALID_COMBO_IDENTITY_OR_SIZE")
    if len(q.legs) > 2:
        blockers.append("COMBO_SIZE_EXCEEDS_RESEARCH_PAIR_DEFAULT")
    if len({leg.receipt_id for leg in q.legs}) != len(q.legs) or len({leg.exact_market_identity for leg in q.legs}) != len(q.legs):
        blockers.append("DUPLICATE_LEG_OR_RECEIPT")
    if not q.joint_receipt_id or not q.dependency_method or q.joint_calibration_status != "PASS":
        blockers.append("JOINT_MODEL_OR_DEPENDENCE_UNVERIFIED")
    if not _prob(q.joint_probability) or not _prob(q.joint_lower_bound):
        blockers.append("JOINT_PROBABILITY_MISSING_OR_INVALID")
    elif q.joint_lower_bound > q.joint_probability:
        blockers.append("JOINT_LOWER_EXCEEDS_POINT")
    if not q.quote_id or q.quote_source != "KALSHI_RFQ":
        blockers.append("EXACT_RFQ_QUOTE_UNVERIFIED")
    try:
        cost, payout = _money(q.total_cost_including_fees), _money(q.maximum_payout)
    except ValueError:
        blockers.append("QUOTE_AMOUNT_INVALID")
        cost = payout = Decimal("0")
    if cost <= 0 or payout <= cost:
        blockers.append("QUOTE_ECONOMICS_INVALID")
    try:
        quote_at, evaluated_at = _utc(q.quote_at_utc), _utc(q.evaluated_at_utc)
        if not 0 <= (evaluated_at - quote_at).total_seconds() <= quote_max_age_seconds:
            blockers.append("RFQ_QUOTE_STALE_OR_FUTURE")
        for leg in q.legs:
            start, model_at = _utc(leg.event_start_utc), _utc(leg.model_asof_utc)
            if quote_at >= start or evaluated_at >= start or model_at > quote_at:
                blockers.append("PREGAME_CHRONOLOGY_INVALID")
            if leg.calibration_status != "PASS" or not _prob(leg.calibrated_probability) or not _prob(leg.calibrated_lower_bound):
                blockers.append("LEG_CALIBRATION_INVALID")
            elif leg.calibrated_lower_bound > leg.calibrated_probability:
                blockers.append("LEG_LOWER_EXCEEDS_POINT")
        if not blockers and q.joint_probability is not None and q.joint_lower_bound is not None:
            ps = [l.calibrated_probability for l in q.legs]
            if not max(0.0, sum(ps) - len(ps) + 1) - 1e-10 <= q.joint_probability <= min(ps) + 1e-10:
                blockers.append("JOINT_OUTSIDE_FRECHET_BOUNDS")
            if q.joint_lower_bound > min(l.calibrated_lower_bound for l in q.legs) + 1e-10:
                blockers.append("JOINT_LOWER_BOUND_INCOHERENT")
    except (ValueError, TypeError):
        blockers.append("TIMESTAMP_INVALID")
    if blockers:
        return ResearchVerdict("RESEARCH_HOLD_INSUFFICIENT_EVIDENCE", tuple(dict.fromkeys(blockers)), False)
    breakeven = float(cost / payout)
    lower_net = Decimal(str(q.joint_lower_bound)) * payout - cost
    return ResearchVerdict(RESEARCH_ONLY_STATUS, ("KALSHI_RECOVERY_COMBOS_DISABLED",), False,
                           q.joint_probability, q.joint_lower_bound, breakeven, lower_net)
