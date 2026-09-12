"""V17 card-admission guard for exact immutable governed prediction receipts.

This module is construction-only. It never scores a sporting event, changes a
probability, upgrades an upstream terminal result, or authorizes execution.

Patch: V17-CARD-IMMUTABLE-RECEIPT-GATE (2026-09-11)
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False

@dataclass(frozen=True)
class CardLegAdmission:
    eligible: bool
    blockers: tuple[str, ...]
    governed_prediction_id: str | None
    governed_prediction_table: str | None
    terminal_label: str | None
    can_execute: bool = False

@dataclass(frozen=True)
class GovernedCardGateResult:
    requested_leg_count: int
    submitted_leg_count: int
    admitted_leg_count: int
    admitted_indices: tuple[int, ...]
    rejected_indices: tuple[int, ...]
    admissions: tuple[CardLegAdmission, ...]
    shrunk: bool
    portfolio_status: str
    blockers: tuple[str, ...]
    can_execute: bool = False

_BASE_IDENTITY_ALIASES = {
    "event_id": ("official_event_id", "event_id", "event_key"),
    "participant": ("participant", "player", "selected_participant", "team"),
    "market_stat": ("stat_type", "market", "market_family"),
}
_OPTIONAL_IDENTITY_ALIASES = {
    "period": ("period",),
    "exact_line": ("exact_line", "line"),
    "direction_or_side": ("direction_or_side", "direction", "side"),
    "settlement_basis": ("settlement_basis", "settlement_operator", "settlement_rule"),
}

def _first(mapping: Mapping[str, Any], aliases: tuple[str, ...]) -> Any:
    for key in aliases:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None

def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text.casefold() if text else None

def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None

def _probability(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None

def _compare_identity(leg: Mapping[str, Any], receipt: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    for dimension, aliases in _BASE_IDENTITY_ALIASES.items():
        left = _text(_first(leg, aliases))
        right = _text(_first(receipt, aliases))
        if left is None or right is None:
            blockers.append(f"CARD_IDENTITY_INCOMPLETE:{dimension}")
        elif left != right:
            blockers.append(f"CARD_EXACT_IDENTITY_MISMATCH:{dimension}")
    for dimension, aliases in _OPTIONAL_IDENTITY_ALIASES.items():
        left_raw = _first(leg, aliases)
        right_raw = _first(receipt, aliases)
        if left_raw is None and right_raw is None:
            continue
        if left_raw is None or right_raw is None:
            blockers.append(f"CARD_EXACT_IDENTITY_MISMATCH:{dimension}")
            continue
        if dimension == "exact_line":
            left = _decimal(left_raw)
            right = _decimal(right_raw)
            if left is None or right is None or left != right:
                blockers.append("CARD_EXACT_IDENTITY_MISMATCH:exact_line")
        elif _text(left_raw) != _text(right_raw):
            blockers.append(f"CARD_EXACT_IDENTITY_MISMATCH:{dimension}")
    return blockers

def evaluate_card_leg_admission(leg: Mapping[str, Any], receipt: Mapping[str, Any] | None) -> CardLegAdmission:
    blockers: list[str] = []
    if receipt is None:
        return CardLegAdmission(False, ("CARD_NO_EXACT_GOVERNED_PREGAME_RECEIPT",), None, None, None, False)
    governed_prediction_id = _first(receipt, ("governed_prediction_id", "prediction_id", "event_prediction_id"))
    governed_prediction_table = _first(receipt, ("governed_prediction_table",))
    terminal_label = _first(receipt, ("terminal_label",))
    if not _text(governed_prediction_id): blockers.append("CARD_GOVERNED_PREDICTION_ID_MISSING")
    if not _text(governed_prediction_table): blockers.append("CARD_GOVERNED_PREDICTION_TABLE_MISSING")
    blockers.extend(_compare_identity(leg, receipt))
    if receipt.get("is_immutable_pregame") is not True: blockers.append("CARD_IMMUTABLE_PREGAME_RECEIPT_NOT_VERIFIED")
    if receipt.get("probability_publishable") is not True: blockers.append("CARD_SPORTING_PROBABILITY_NOT_PUBLISHABLE")
    if receipt.get("lane_card_eligible") is not True: blockers.append("CARD_UPSTREAM_ELIGIBILITY_NOT_VERIFIED")
    if receipt.get("terminal_blocked") is True: blockers.append("CARD_UPSTREAM_TERMINAL_BLOCKER")
    calibrated = _probability(receipt.get("calibrated_probability"))
    lower_bound = _probability(receipt.get("calibrated_lower_bound", receipt.get("calibrated_probability_lower_bound")))
    if calibrated is None or lower_bound is None or not (0.0 < calibrated < 1.0) or not (0.0 <= lower_bound <= calibrated):
        blockers.append("CARD_GOVERNED_PROBABILITY_PACKAGE_INVALID")
    if receipt.get("can_execute") is not False: blockers.append("CARD_CAN_EXECUTE_INVARIANT_UNVERIFIED")
    return CardLegAdmission(not blockers, tuple(dict.fromkeys(blockers)), str(governed_prediction_id) if governed_prediction_id is not None else None, str(governed_prediction_table) if governed_prediction_table is not None else None, str(terminal_label) if terminal_label is not None else None, False)

def gate_proposed_card(proposed_legs: Sequence[tuple[Mapping[str, Any], Mapping[str, Any] | None]], *, requested_leg_count: int | None = None, minimum_leg_count: int = 1) -> GovernedCardGateResult:
    if minimum_leg_count < 1: raise ValueError("minimum_leg_count must be >= 1")
    requested = len(proposed_legs) if requested_leg_count is None else requested_leg_count
    if requested < 0: raise ValueError("requested_leg_count must be >= 0")
    admissions = tuple(evaluate_card_leg_admission(leg, receipt) for leg, receipt in proposed_legs)
    admitted_indices = tuple(i for i, result in enumerate(admissions) if result.eligible)
    rejected_indices = tuple(i for i, result in enumerate(admissions) if not result.eligible)
    admitted_count = len(admitted_indices)
    shrunk = admitted_count < len(proposed_legs) or admitted_count < requested
    blockers: list[str] = []
    for idx in rejected_indices: blockers.extend(admissions[idx].blockers)
    if admitted_count < minimum_leg_count:
        portfolio_status = "HELD"
        blockers.append("INSUFFICIENT_LEGS_AFTER_MANDATORY_SHRINK")
    elif shrunk:
        portfolio_status = "SHRUNK"
    else:
        portfolio_status = "PASS"
    return GovernedCardGateResult(requested, len(proposed_legs), admitted_count, admitted_indices, rejected_indices, admissions, shrunk, portfolio_status, tuple(dict.fromkeys(blockers)), False)
