from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from typing import Any, Mapping, Sequence


# Kalshi's current fixed-point documentation separates model-fee precision from
# account balance precision. Trade fees are six-decimal dollar values; balance
# alignment is $0.0001 for direct members and $0.01 for non-direct members.
TRADE_FEE_QUANTUM = Decimal("0.000001")
DIRECT_BALANCE_QUANTUM = Decimal("0.0001")
NON_DIRECT_BALANCE_QUANTUM = Decimal("0.01")
SUPPORTED_BALANCE_QUANTA = {DIRECT_BALANCE_QUANTUM, NON_DIRECT_BALANCE_QUANTUM}
GENERAL_TAKER_COEFFICIENT = Decimal("0.07")
GENERAL_MAKER_COEFFICIENT = Decimal("0.0175")
SUPPORTED_FEE_TYPES = {"quadratic", "quadratic_with_maker_fees"}


class FeePolicyError(ValueError):
    def __init__(self, code: str, blockers: tuple[str, ...]):
        self.code = code
        self.blockers = blockers
        super().__init__(f"{code}: {', '.join(blockers)}")


@dataclass(frozen=True)
class FeePolicySnapshot:
    series_ticker: str
    event_ticker: str | None
    fee_type: str
    fee_multiplier: Decimal
    taker_coefficient: Decimal
    maker_coefficient: Decimal | None
    policy_source: str
    resolved_at: str
    next_scheduled_change_at: str | None = None
    fee_waiver_expiration_time: str | None = None
    can_execute: bool = False


@dataclass(frozen=True)
class TradeFeeQuote:
    fee_type: str
    liquidity_role: str
    price: Decimal
    quantity: Decimal
    raw_trade_fee: Decimal
    trade_fee: Decimal
    trade_fee_per_contract: Decimal
    pre_cash_rounding_break_even: Decimal
    cash_rounding_included: bool
    balance_quantum: Decimal | None = None
    rounding_fee: Decimal | None = None
    net_fee: Decimal | None = None
    effective_break_even: Decimal | None = None
    friction_model_verified: bool = False
    can_execute: bool = False


def resolve_fee_policy(
    *,
    series: Mapping[str, Any],
    event: Mapping[str, Any] | None,
    market: Mapping[str, Any] | None,
    fee_changes: Sequence[Mapping[str, Any]] = (),
    event_fee_changes: Sequence[Mapping[str, Any]] = (),
    resolved_at: str,
) -> FeePolicySnapshot:
    """Resolve current analytical fee policy from exact public Kalshi metadata.

    Series fee fields establish the current base policy. Event override fields
    layer on top of that base. Historical change feeds are reconciled using only
    the latest effective row at or before ``resolved_at``; older rows are valid
    audit history and must not make a newer current policy appear stale.

    Future Series/Event rows contribute the earliest known next transition. An
    active fee-waiver marker remains fail-closed because its exact fee semantics
    are not encoded by that timestamp alone. This module is read-only analytical
    code and has no order capability.
    """
    series_ticker = _required_text(series, "ticker", "FEE_SERIES_TICKER_MISSING").upper()
    as_of = _parse_timestamp(resolved_at, "FEE_RESOLVED_AT_INVALID")

    event_obj = event or {}
    market_obj = market or {}
    event_series = _optional_text(event_obj.get("series_ticker"))
    if event_series and event_series.upper() != series_ticker:
        raise FeePolicyError("FEE_POLICY_IDENTITY_MISMATCH", ("EVENT_SERIES_TICKER_MISMATCH",))

    event_ticker = _optional_text(event_obj.get("event_ticker"))
    market_event = _optional_text(market_obj.get("event_ticker"))
    if market_event and event_ticker and market_event.upper() != event_ticker.upper():
        raise FeePolicyError("FEE_POLICY_IDENTITY_MISMATCH", ("MARKET_EVENT_TICKER_MISMATCH",))
    normalized_event_ticker = event_ticker.upper() if event_ticker else None

    base_type = _required_text(series, "fee_type", "SERIES_FEE_TYPE_MISSING").lower()
    base_multiplier = _decimal(series.get("fee_multiplier"), "SERIES_FEE_MULTIPLIER_INVALID")

    override_type = _optional_text(event_obj.get("fee_type_override"))
    override_multiplier_raw = event_obj.get("fee_multiplier_override")
    override_multiplier = None
    if override_multiplier_raw not in (None, ""):
        override_multiplier = _decimal(override_multiplier_raw, "EVENT_FEE_MULTIPLIER_OVERRIDE_INVALID")

    fee_type = override_type.lower() if override_type else base_type
    fee_multiplier = override_multiplier if override_multiplier is not None else base_multiplier
    _validate_supported_policy(fee_type, fee_multiplier)

    waiver_text = _optional_text(market_obj.get("fee_waiver_expiration_time"))
    if waiver_text:
        waiver_at = _parse_timestamp(waiver_text, "FEE_WAIVER_TIMESTAMP_INVALID")
        if as_of < waiver_at:
            raise FeePolicyError(
                "FEE_POLICY_UNRESOLVED",
                ("ACTIVE_FEE_WAIVER_REQUIRES_EXACT_WAIVER_SEMANTICS",),
            )

    next_change: datetime | None = None
    stale_change_blockers: list[str] = []
    latest_effective_series: tuple[datetime, Mapping[str, Any]] | None = None
    latest_effective_event: tuple[datetime, Mapping[str, Any]] | None = None

    for change in fee_changes:
        if not isinstance(change, Mapping):
            stale_change_blockers.append("SERIES_FEE_CHANGE_ROW_INVALID")
            continue
        change_ticker = _optional_text(change.get("series_ticker"))
        if not change_ticker or change_ticker.upper() != series_ticker:
            stale_change_blockers.append("SERIES_FEE_CHANGE_SERIES_TICKER_MISMATCH")
            continue
        scheduled_text = _optional_text(change.get("scheduled_ts"))
        if not scheduled_text:
            stale_change_blockers.append("SERIES_FEE_CHANGE_TIMESTAMP_MISSING")
            continue
        scheduled = _parse_timestamp(scheduled_text, "SERIES_FEE_CHANGE_TIMESTAMP_INVALID")
        if scheduled <= as_of:
            if latest_effective_series is None or scheduled > latest_effective_series[0]:
                latest_effective_series = (scheduled, change)
        else:
            next_change = _earlier(next_change, scheduled)

    if latest_effective_series is not None:
        _, change = latest_effective_series
        change_type = _optional_text(change.get("fee_type"))
        change_multiplier_raw = change.get("fee_multiplier")
        if not change_type and change_multiplier_raw in (None, ""):
            stale_change_blockers.append("SERIES_FEE_CHANGE_POLICY_FIELDS_MISSING")
        if change_type and change_type.lower() != base_type:
            stale_change_blockers.append("CURRENT_FEE_POLICY_CONTRADICTS_EFFECTIVE_SERIES_CHANGE")
        if change_multiplier_raw not in (None, ""):
            change_multiplier = _decimal(
                change_multiplier_raw, "SERIES_FEE_CHANGE_MULTIPLIER_INVALID"
            )
            if change_multiplier != base_multiplier:
                stale_change_blockers.append("CURRENT_FEE_POLICY_CONTRADICTS_EFFECTIVE_SERIES_CHANGE")

    if event_fee_changes and not normalized_event_ticker:
        stale_change_blockers.append("EVENT_TICKER_REQUIRED_FOR_EVENT_FEE_CHANGES")

    for change in event_fee_changes:
        if not isinstance(change, Mapping):
            stale_change_blockers.append("EVENT_FEE_CHANGE_ROW_INVALID")
            continue
        change_event = _optional_text(change.get("event_ticker"))
        change_series = _optional_text(change.get("series_ticker"))
        if not change_event or (
            normalized_event_ticker and change_event.upper() != normalized_event_ticker
        ):
            stale_change_blockers.append("EVENT_FEE_CHANGE_EVENT_TICKER_MISMATCH")
            continue
        if change_series and change_series.upper() != series_ticker:
            stale_change_blockers.append("EVENT_FEE_CHANGE_SERIES_TICKER_MISMATCH")
            continue
        scheduled_text = _optional_text(change.get("scheduled_ts"))
        if not scheduled_text:
            stale_change_blockers.append("EVENT_FEE_CHANGE_TIMESTAMP_MISSING")
            continue
        scheduled = _parse_timestamp(scheduled_text, "EVENT_FEE_CHANGE_TIMESTAMP_INVALID")
        if scheduled <= as_of:
            if latest_effective_event is None or scheduled > latest_effective_event[0]:
                latest_effective_event = (scheduled, change)
        else:
            next_change = _earlier(next_change, scheduled)

    if latest_effective_event is not None:
        _, change = latest_effective_event
        latest_type_raw = _optional_text(change.get("fee_type_override"))
        latest_multiplier_raw = change.get("fee_multiplier_override")
        latest_type = latest_type_raw.lower() if latest_type_raw else None
        current_type = override_type.lower() if override_type else None
        latest_multiplier = None
        if latest_multiplier_raw not in (None, ""):
            latest_multiplier = _decimal(
                latest_multiplier_raw, "EVENT_FEE_CHANGE_MULTIPLIER_OVERRIDE_INVALID"
            )

        # Event change rows describe override state, including null as a clear.
        # Compare that state directly to the current Event object rather than to
        # the final fee after Series fallback; otherwise a cleared override can
        # be mistaken for a contradictory fee policy.
        if latest_type != current_type or latest_multiplier != override_multiplier:
            stale_change_blockers.append("CURRENT_FEE_POLICY_CONTRADICTS_EFFECTIVE_EVENT_CHANGE")

    if stale_change_blockers:
        raise FeePolicyError("FEE_POLICY_STALE", tuple(dict.fromkeys(stale_change_blockers)))

    policy_source = "EVENT_OVERRIDE" if override_type or override_multiplier is not None else "SERIES"
    maker_coefficient = (
        GENERAL_MAKER_COEFFICIENT * fee_multiplier
        if fee_type == "quadratic_with_maker_fees"
        else None
    )
    return FeePolicySnapshot(
        series_ticker=series_ticker,
        event_ticker=normalized_event_ticker,
        fee_type=fee_type,
        fee_multiplier=fee_multiplier,
        taker_coefficient=GENERAL_TAKER_COEFFICIENT * fee_multiplier,
        maker_coefficient=maker_coefficient,
        policy_source=policy_source,
        resolved_at=resolved_at,
        next_scheduled_change_at=_iso(next_change) if next_change else None,
        fee_waiver_expiration_time=waiver_text,
        can_execute=False,
    )


def quote_trade_fee(
    policy: FeePolicySnapshot,
    *,
    price: Any,
    quantity: Any,
    liquidity_role: str = "TAKER",
) -> TradeFeeQuote:
    """Quote model trade fee only, excluding account-balance alignment."""
    p = _decimal(price, "FEE_PRICE_INVALID")
    c = _decimal(quantity, "FEE_QUANTITY_INVALID")
    if not (Decimal("0") <= p <= Decimal("1")):
        raise FeePolicyError("FEE_QUOTE_INVALID", ("PRICE_OUT_OF_RANGE",))
    if c <= 0:
        raise FeePolicyError("FEE_QUOTE_INVALID", ("QUANTITY_NOT_POSITIVE",))

    role = liquidity_role.strip().upper()
    if role == "TAKER":
        coefficient = policy.taker_coefficient
    elif role == "MAKER":
        if policy.fee_type == "quadratic":
            coefficient = Decimal("0")
        elif policy.maker_coefficient is not None:
            coefficient = policy.maker_coefficient
        else:
            raise FeePolicyError("FEE_POLICY_UNRESOLVED", ("MAKER_FEE_COEFFICIENT_MISSING",))
    else:
        raise FeePolicyError("FEE_QUOTE_INVALID", (f"LIQUIDITY_ROLE_UNSUPPORTED:{role}",))

    raw = coefficient * c * p * (Decimal("1") - p)
    trade_fee = _ceil_quantum(raw, TRADE_FEE_QUANTUM)
    per_contract = trade_fee / c
    break_even = p + per_contract

    return TradeFeeQuote(
        fee_type=policy.fee_type,
        liquidity_role=role,
        price=p,
        quantity=c,
        raw_trade_fee=raw,
        trade_fee=trade_fee,
        trade_fee_per_contract=per_contract,
        pre_cash_rounding_break_even=break_even,
        cash_rounding_included=False,
        friction_model_verified=False,
        can_execute=False,
    )


def quote_new_order_single_fill_cash_fee(
    policy: FeePolicySnapshot,
    *,
    price: Any,
    quantity: Any,
    balance_quantum: Any,
    liquidity_role: str = "TAKER",
) -> TradeFeeQuote:
    """Calculate one hypothetical fresh-order, one-fill cash debit exactly.

    The caller must supply the user's actual target balance precision. Current
    documented values are $0.0001 for direct members and $0.01 for non-direct
    members. No account type is guessed here. Multi-fill orders remain outside
    this helper because their rounding accumulator/rebate state is fill-path
    dependent.
    """
    quantum = _decimal(balance_quantum, "BALANCE_QUANTUM_INVALID")
    if quantum not in SUPPORTED_BALANCE_QUANTA:
        raise FeePolicyError(
            "FEE_POLICY_UNRESOLVED",
            (f"BALANCE_QUANTUM_UNSUPPORTED:{quantum}",),
        )

    base = quote_trade_fee(policy, price=price, quantity=quantity, liquidity_role=liquidity_role)
    revenue = -(base.price * base.quantity)
    balance_change = revenue - base.trade_fee
    posted_balance_change = _floor_quantum(balance_change, quantum)
    rounding_fee = balance_change - posted_balance_change
    if not (Decimal("0") <= rounding_fee < quantum):
        raise FeePolicyError("FEE_ROUNDING_INVARIANT_FAILED", ("ROUNDING_FEE_OUT_OF_RANGE",))

    net_fee = base.trade_fee + rounding_fee
    effective_break_even = base.price + (net_fee / base.quantity)
    return TradeFeeQuote(
        fee_type=base.fee_type,
        liquidity_role=base.liquidity_role,
        price=base.price,
        quantity=base.quantity,
        raw_trade_fee=base.raw_trade_fee,
        trade_fee=base.trade_fee,
        trade_fee_per_contract=base.trade_fee_per_contract,
        pre_cash_rounding_break_even=base.pre_cash_rounding_break_even,
        cash_rounding_included=True,
        balance_quantum=quantum,
        rounding_fee=rounding_fee,
        net_fee=net_fee,
        effective_break_even=effective_break_even,
        friction_model_verified=True,
        can_execute=False,
    )


def _validate_supported_policy(fee_type: str, fee_multiplier: Decimal) -> None:
    if fee_multiplier < 0:
        raise FeePolicyError("FEE_POLICY_INVALID", ("FEE_MULTIPLIER_NEGATIVE",))
    if fee_type == "flat":
        raise FeePolicyError(
            "FEE_POLICY_UNRESOLVED",
            ("FLAT_FEE_AMOUNT_NOT_EXPOSED_BY_SERIES_POLICY",),
        )
    if fee_type not in SUPPORTED_FEE_TYPES:
        raise FeePolicyError("FEE_POLICY_UNRESOLVED", (f"FEE_TYPE_UNSUPPORTED:{fee_type}",))


def _earlier(current: datetime | None, candidate: datetime) -> datetime:
    return candidate if current is None or candidate < current else current


def _ceil_quantum(value: Decimal, quantum: Decimal) -> Decimal:
    if value == 0:
        return Decimal("0")
    units = (value / quantum).to_integral_value(rounding=ROUND_CEILING)
    return units * quantum


def _floor_quantum(value: Decimal, quantum: Decimal) -> Decimal:
    units = (value / quantum).to_integral_value(rounding=ROUND_FLOOR)
    return units * quantum


def _decimal(value: Any, blocker: str) -> Decimal:
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise FeePolicyError("FEE_POLICY_INVALID", (blocker,)) from exc
    if not out.is_finite():
        raise FeePolicyError("FEE_POLICY_INVALID", (blocker,))
    return out


def _required_text(obj: Mapping[str, Any], field: str, blocker: str) -> str:
    value = _optional_text(obj.get(field))
    if not value:
        raise FeePolicyError("FEE_POLICY_INVALID", (blocker,))
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_timestamp(value: str, blocker: str) -> datetime:
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise FeePolicyError("FEE_POLICY_INVALID", (blocker,)) from exc
    if parsed.tzinfo is None:
        raise FeePolicyError("FEE_POLICY_INVALID", (blocker,))
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
