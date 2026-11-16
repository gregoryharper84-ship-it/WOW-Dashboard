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
    # All fields below are mandatory for any *new* research diagnostic. Defaults
    # intentionally hold historical/legacy callers rather than trust missing data.
    market_family: str = ""
    model_market_family: str = ""
    model_class: str = ""
    contract_settlement_rule: str = ""
    market_line: Decimal | None = None
    event_status: str = ""
    event_status_source: str = ""
    event_status_receipt_id: str = ""
    event_status_at_utc: datetime | None = None


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
    # Frozen and immutable producer-side joint receipt timestamp (never inferred).
    joint_receipt_asof_utc: datetime | None = None


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


def _lower_bound(value: float | None) -> bool:
    return value is not None and isinstance(value, (float, int)) and not isinstance(value, bool) and isfinite(value) and 0 <= value < 1


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
    if any(
        not isinstance(value, str) or not value.strip()
        for leg in q.legs
        for value in (leg.receipt_id, leg.exact_market_identity, leg.event_id)
    ):
        blockers.append("LEG_RECEIPT_EVENT_IDENTITY_MISSING")
    if "LEG_RECEIPT_EVENT_IDENTITY_MISSING" not in blockers and (
        len({leg.receipt_id for leg in q.legs}) != len(q.legs)
        or len({leg.exact_market_identity for leg in q.legs}) != len(q.legs)
    ):
        blockers.append("DUPLICATE_LEG_OR_RECEIPT")
    if not q.joint_receipt_id or not q.dependency_method or q.joint_calibration_status != "PASS":
        blockers.append("JOINT_MODEL_OR_DEPENDENCE_UNVERIFIED")
    if not _prob(q.joint_probability) or not _lower_bound(q.joint_lower_bound):
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
        if q.joint_receipt_asof_utc is None:
            blockers.append("JOINT_RECEIPT_ASOF_UNVERIFIED")
        else:
            joint_at = _utc(q.joint_receipt_asof_utc)
            if joint_at > quote_at or any(joint_at >= _utc(leg.event_start_utc) for leg in q.legs):
                blockers.append("JOINT_RECEIPT_LOOKAHEAD_INVALID")
        if not 0 <= (evaluated_at - quote_at).total_seconds() <= quote_max_age_seconds:
            blockers.append("RFQ_QUOTE_STALE_OR_FUTURE")
        if len({leg.event_id for leg in q.legs if isinstance(leg.event_id, str)}) < len(q.legs):
            blockers.append("SAME_EVENT_CROSS_MARKET_DEPENDENCY_UNVERIFIED")
        for leg in q.legs:
            start, model_at = _utc(leg.event_start_utc), _utc(leg.model_asof_utc)
            family = leg.market_family if isinstance(leg.market_family, str) else ""
            if family not in {"MONEYLINE", "SPREAD", "TOTAL", "PLAYER_PROP", "OUTRIGHT"}:
                blockers.append("MARKET_FAMILY_UNVERIFIED")
            if leg.model_market_family != family or leg.model_class != "CERTIFIED_FITTED":
                blockers.append("MARKET_SPECIFIC_FITTED_MODEL_UNVERIFIED")
            if not isinstance(leg.contract_settlement_rule, str) or not leg.contract_settlement_rule.strip():
                blockers.append("EXACT_CONTRACT_SETTLEMENT_UNVERIFIED")
            if family in {"SPREAD", "TOTAL"}:
                try:
                    line = Decimal(str(leg.market_line))
                    if not line.is_finite() or abs(line) > Decimal("10000"):
                        raise ValueError("invalid line")
                except (TypeError, InvalidOperation, ValueError):
                    blockers.append("MARKET_SPECIFIC_EXACT_LINE_UNVERIFIED")
            if leg.event_status not in {"SCHEDULED", "PRE_GAME"}:
                blockers.append("OFFICIAL_PREGAME_STATUS_INVALID")
            if leg.event_status_source != "OFFICIAL_SCHEDULE" or not isinstance(leg.event_status_receipt_id, str) or not leg.event_status_receipt_id.strip():
                blockers.append("OFFICIAL_STATUS_RECEIPT_UNVERIFIED")
            if leg.event_status_at_utc is None:
                blockers.append("PREGAME_STATUS_SNAPSHOT_MISSING")
            else:
                observed = _utc(leg.event_status_at_utc)
                if not 0 <= (quote_at - observed).total_seconds() <= 1800:
                    blockers.append("PREGAME_STATUS_STALE_OR_FUTURE")
            if quote_at >= start or evaluated_at >= start or model_at > quote_at:
                blockers.append("PREGAME_CHRONOLOGY_INVALID")
            if leg.calibration_status != "PASS" or not _prob(leg.calibrated_probability) or not _lower_bound(leg.calibrated_lower_bound):
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
    if lower_net <= 0:
        return ResearchVerdict(
            "RESEARCH_HOLD_NO_CONSERVATIVE_EDGE",
            ("NO_CONSERVATIVE_ECONOMIC_EDGE", "KALSHI_RECOVERY_COMBOS_DISABLED"),
            False, q.joint_probability, q.joint_lower_bound, breakeven, lower_net,
        )
    return ResearchVerdict(RESEARCH_ONLY_STATUS, ("KALSHI_RECOVERY_COMBOS_DISABLED",), False,
                           q.joint_probability, q.joint_lower_bound, breakeven, lower_net)


@dataclass(frozen=True)
class ResearchLegSettlement:
    """Frozen chat/research leg and separately sourced official result; not a trade."""
    event_id: str
    market_identity: str
    pregame_receipt_id: str
    pregame_locked_at_utc: datetime
    event_start_utc: datetime
    official_outcome: str | None = None
    official_result_receipt_id: str | None = None
    official_result_at_utc: datetime | None = None
    exact_contract_settlement_rule: str | None = None


@dataclass(frozen=True)
class ResearchSlipGrade:
    status: str
    wins: int
    losses: int
    pending: int
    settlement_dependent: int
    blockers: tuple[str, ...]
    official_outcomes_attested: bool = False
    trade_submission_verified: bool = False
    realized_pnl_verified: bool = False
    can_execute: bool = False


def grade_research_slip(legs: Iterable[ResearchLegSettlement]) -> ResearchSlipGrade:
    """Grade frozen leg labels without inferring a live Kalshi market settlement.

    Independent official-result receipt references are *claimed pointers*, not
    cryptographic attestations or actual Kalshi fills. Push, void, postponed,
    and pending contracts remain unresolved until exact contract rules resolve.
    """
    rows = list(legs)
    blockers: list[str] = []
    if not 2 <= len(rows) <= 20:
        blockers.append("INVALID_RESEARCH_SLIP_SIZE")
    keys: set[tuple[str, str]] = set()
    receipt_ids: set[str] = set()
    wins = losses = pending = settlement_dependent = 0
    for leg in rows:
        if not all(isinstance(x, str) and x.strip() for x in (leg.event_id, leg.market_identity, leg.pregame_receipt_id)):
            blockers.append("FROZEN_LEG_IDENTITY_MISSING")
            pending += 1
            continue
        key = (leg.event_id, leg.market_identity)
        if key in keys or leg.pregame_receipt_id in receipt_ids:
            blockers.append("DUPLICATE_FROZEN_LEG")
        keys.add(key)
        receipt_ids.add(leg.pregame_receipt_id)
        try:
            start = _utc(leg.event_start_utc)
            locked = _utc(leg.pregame_locked_at_utc)
            if locked >= start:
                blockers.append("PREGAME_RECEIPT_LOOKAHEAD_INVALID")
        except (TypeError, ValueError):
            blockers.append("FROZEN_CHRONOLOGY_INVALID")
            pending += 1
            continue
        outcome = leg.official_outcome
        if outcome is None or outcome == "PENDING":
            pending += 1
        elif outcome in {"PUSH", "VOID", "POSTPONED"}:
            settlement_dependent += 1
        elif outcome in {"WIN", "LOSS"}:
            try:
                if (not isinstance(leg.official_result_receipt_id, str)
                    or not leg.official_result_receipt_id.strip()
                    or not leg.exact_contract_settlement_rule
                    or leg.official_result_at_utc is None
                    or _utc(leg.official_result_at_utc) < start):
                    raise ValueError("unverified result")
            except (TypeError, ValueError):
                blockers.append("INDEPENDENT_RESULT_OR_SETTLEMENT_UNVERIFIED")
                pending += 1
                continue
            wins += outcome == "WIN"
            losses += outcome == "LOSS"
        else:
            blockers.append("UNRECOGNIZED_OFFICIAL_OUTCOME")
            pending += 1
    if blockers:
        status = "RESEARCH_GRADE_HELD"
    elif losses:
        status = "RESEARCH_SLIP_LOST"
    elif pending or settlement_dependent:
        status = "RESEARCH_SLIP_UNRESOLVED"
    else:
        status = "RESEARCH_ALL_LEGS_WON_UNVERIFIED_TRADE"
    return ResearchSlipGrade(
        status, wins, losses, pending, settlement_dependent,
        tuple(dict.fromkeys(blockers)),
    )
