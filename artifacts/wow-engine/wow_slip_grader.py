"""
Post-slate EV grader for WOW paper-trade slips (PrizePicks + Kalshi).

Supports Power/Flex/DNP downgrade tables, fragile-win detection
(margin <= 0.5), and Kalshi counterfactual exit_alpha_units.
Never enables live execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from wow_slip_builder import Direction, Modifier, SlipLeg, SlipSnapshot, SlipType

POWER_DOWNGRADE_TABLE: dict[int, float] = {
    6: 37.5,
    5: 20.0,
    4: 10.0,
    3: 6.0,
    2: 3.0,
}

FLEX_TABLES: dict[int, dict[int, float]] = {
    6: {6: 25.0, 5: 2.0, 4: 0.4},
    5: {5: 10.0, 4: 2.0, 3: 0.4},
    4: {4: 5.0, 3: 1.5},
}

FRAGILE_WIN_MARGIN = 0.5


class LegResult(str, Enum):
    WIN = "WIN"
    LOSS = "LOSS"
    PUSH_OR_DNP = "PUSH_OR_DNP"


class GradedLeg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    leg_id: str
    direction: Direction
    line: float
    actual: float | None
    result: LegResult
    signed_margin: float
    is_fragile_win: bool = False


class GradedSlip(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    slip_id: UUID
    platform: str
    archetype: str
    slip_type: str
    legs_won: int
    legs_lost: int
    legs_pushed: int
    effective_multiplier: float
    realized_net_units: float
    hold_to_settlement_net_units: float
    exit_alpha_units: float
    exit_action: Optional[str] = None
    fragile_win_count: int = 0
    graded_legs: list[GradedLeg]
    graded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    can_execute: Literal[False] = False
    # Compat aliases for slip-intelligence / older callers.
    product: Optional[str] = None
    total_legs: Optional[int] = None
    wins: Optional[int] = None
    losses: Optional[int] = None
    pushes: Optional[int] = None
    active_legs: Optional[int] = None
    original_multiplier: Optional[float] = None
    net_units: Optional[float] = None

    @model_validator(mode="after")
    def _dry_run_and_aliases(self) -> GradedSlip:
        if self.can_execute is not False:
            raise ValueError("can_execute must be False")
        total = self.legs_won + self.legs_lost + self.legs_pushed
        active = total - self.legs_pushed
        object.__setattr__(self, "product", self.slip_type)
        object.__setattr__(self, "total_legs", total)
        object.__setattr__(self, "wins", self.legs_won)
        object.__setattr__(self, "losses", self.legs_lost)
        object.__setattr__(self, "pushes", self.legs_pushed)
        object.__setattr__(self, "active_legs", active)
        object.__setattr__(self, "net_units", self.realized_net_units)
        return self


# Backward-compatible alias.
SlipGrade = GradedSlip


def _grade_leg(leg: SlipLeg, actual: float | None) -> GradedLeg:
    if actual is None or actual == leg.line:
        return GradedLeg(
            leg_id=leg.leg_id,
            direction=leg.direction,
            line=leg.line,
            actual=actual,
            result=LegResult.PUSH_OR_DNP,
            signed_margin=0.0,
            is_fragile_win=False,
        )

    if leg.direction == Direction.MORE:
        margin = actual - leg.line
    else:
        margin = leg.line - actual

    if margin > 0:
        result = LegResult.WIN
        fragile = margin <= FRAGILE_WIN_MARGIN
    elif margin < 0:
        result = LegResult.LOSS
        fragile = False
    else:
        result = LegResult.PUSH_OR_DNP
        fragile = False
        margin = 0.0

    return GradedLeg(
        leg_id=leg.leg_id,
        direction=leg.direction,
        line=leg.line,
        actual=actual,
        result=result,
        signed_margin=float(margin),
        is_fragile_win=fragile,
    )


def _flex_multiplier(*, active_legs: int, wins: int) -> float:
    if active_legs <= 3:
        if active_legs == 3 and wins == 3:
            return 3.0
        if active_legs == 2 and wins == 2:
            return 1.5
        if active_legs < 2:
            return 1.0
        return 0.0
    table = FLEX_TABLES.get(active_legs, {})
    return float(table.get(wins, 0.0))


def _power_multiplier(
    *,
    active_legs: int,
    losses: int,
    goblin_active: bool,
) -> float:
    if losses > 0:
        return 0.0
    if active_legs < 2:
        return 1.0  # refund
    if active_legs == 2 and goblin_active:
        return 2.0
    return float(POWER_DOWNGRADE_TABLE.get(active_legs, 0.0))


def _hold_net_units(
    snapshot: SlipSnapshot,
    *,
    wins: int,
    losses: int,
    pushes: int,
    stake: float,
) -> tuple[float, float]:
    """Return (effective_multiplier, hold_to_settlement_net_units)."""
    total = len(snapshot.legs)
    active = total - pushes
    slip_type = (
        snapshot.slip_type
        if isinstance(snapshot.slip_type, SlipType)
        else SlipType(str(snapshot.slip_type))
    )

    if slip_type == SlipType.SIX_PICK_FLEX:
        eff = _flex_multiplier(active_legs=active, wins=wins)
        return eff, (eff - 1.0) * stake

    if slip_type in {
        SlipType.TWO_PICK_POWER,
        SlipType.THREE_PICK_POWER,
        SlipType.FOUR_PICK_POWER,
        SlipType.FIVE_PICK_POWER,
        SlipType.SIX_PICK_POWER,
    }:
        goblin_active = any(
            leg.modifier == Modifier.GOBLIN for leg in snapshot.legs
        ) and losses == 0
        # Only count goblin for 2-pick payout when that goblin remains active.
        active_goblin = goblin_active and active >= 2 and slip_type == SlipType.TWO_PICK_POWER
        # If the goblin leg pushed/DNP'd, treat as standard power downgrade.
        if goblin_active:
            goblin_legs = [
                leg for leg in snapshot.legs if leg.modifier == Modifier.GOBLIN
            ]
            # Goblin is "active" only if it was not a push — approximated by
            # active_legs still including it when losses==0; for 2-pick with
            # one goblin push, active becomes 1 → refund path.
            active_goblin = active_goblin and len(goblin_legs) == 1
        eff = _power_multiplier(
            active_legs=active, losses=losses, goblin_active=active_goblin
        )
        if losses > 0:
            return 0.0, -1.0 * stake
        if active < 2:
            return 1.0, 0.0
        return eff, (eff - 1.0) * stake

    if slip_type in {SlipType.KALSHI_SGP_2, SlipType.KALSHI_SGP_3}:
        if losses > 0:
            return 0.0, -1.0 * stake
        if active >= 1 and losses == 0:
            eff = float(snapshot.payout_multiplier)
            return eff, (eff - 1.0) * stake
        return 1.0, 0.0

    # Unknown slip type — fail closed to full loss semantics.
    return 0.0, -1.0 * stake


def grade_slip(
    snapshot: SlipSnapshot,
    actuals: dict[str, float | None],
    *,
    stake: float = 1.0,
    early_exit_payout: Optional[float] = None,
    exit_action: Optional[str] = None,
) -> GradedSlip:
    """
    Grade each slip leg against post-slate actuals keyed by leg_id.

    Missing keys are treated as DNP (None).
    """
    graded_legs: list[GradedLeg] = []
    for leg in snapshot.legs:
        actual = actuals[leg.leg_id] if leg.leg_id in actuals else None
        graded_legs.append(_grade_leg(leg, actual))

    wins = sum(1 for g in graded_legs if g.result == LegResult.WIN)
    losses = sum(1 for g in graded_legs if g.result == LegResult.LOSS)
    pushes = sum(1 for g in graded_legs if g.result == LegResult.PUSH_OR_DNP)
    fragile = sum(1 for g in graded_legs if g.is_fragile_win)

    eff_mult, hold_net = _hold_net_units(
        snapshot,
        wins=wins,
        losses=losses,
        pushes=pushes,
        stake=float(stake),
    )

    if early_exit_payout is not None:
        realized = float(early_exit_payout) - float(stake)
    else:
        realized = hold_net
    exit_alpha = round(realized - hold_net, 4)

    archetype = (
        snapshot.archetype.value
        if hasattr(snapshot.archetype, "value")
        else str(snapshot.archetype)
    )
    slip_type = (
        snapshot.slip_type.value
        if hasattr(snapshot.slip_type, "value")
        else str(snapshot.slip_type)
    )

    return GradedSlip(
        slip_id=snapshot.slip_id,
        platform=snapshot.platform,
        archetype=archetype,
        slip_type=slip_type,
        legs_won=wins,
        legs_lost=losses,
        legs_pushed=pushes,
        effective_multiplier=float(eff_mult),
        realized_net_units=float(realized),
        hold_to_settlement_net_units=float(hold_net),
        exit_alpha_units=float(exit_alpha),
        exit_action=exit_action,
        fragile_win_count=fragile,
        graded_legs=graded_legs,
        original_multiplier=float(snapshot.payout_multiplier),
        can_execute=False,
    )


__all__ = [
    "FLEX_TABLES",
    "FRAGILE_WIN_MARGIN",
    "GradedLeg",
    "GradedSlip",
    "LegResult",
    "POWER_DOWNGRADE_TABLE",
    "SlipGrade",
    "grade_slip",
]
