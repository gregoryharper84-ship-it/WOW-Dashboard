"""
Post-slate EV grader for WOW paper-trade PrizePicks Power Play slips.

Push/DNP legs downgrade the effective Power multiplier; losses zero the slip.
Fragile wins are tracked when a WIN clears by margin <= 0.5.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from wow_slip_builder import Direction, SlipLeg, SlipSnapshot

POWER_DOWNGRADE_TABLE: dict[int, float] = {
    6: 37.5,
    5: 20.0,
    4: 10.0,
    3: 6.0,
    2: 3.0,
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


class SlipGrade(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    slip_id: UUID
    archetype: str
    product: str
    total_legs: int
    wins: int
    losses: int
    pushes: int
    active_legs: int
    original_multiplier: float
    effective_multiplier: float
    net_units: float
    graded_legs: list[GradedLeg]
    fragile_win_count: int = 0
    graded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    can_execute: Literal[False] = False

    @model_validator(mode="after")
    def _dry_run_only(self) -> SlipGrade:
        if self.can_execute is not False:
            raise ValueError("can_execute must be False")
        return self


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


def _effective_multiplier(*, total_legs: int, pushes: int, losses: int) -> tuple[float, float]:
    """
    PrizePicks Power Play push/DNP downgrade.

    Returns (effective_multiplier, net_units) for a 1-unit stake.
    """
    if losses > 0:
        return 0.0, -1.0

    active_legs = total_legs - pushes
    if active_legs < 2:
        # Refund path when too few active legs remain.
        return 1.0, 0.0

    eff = POWER_DOWNGRADE_TABLE.get(active_legs)
    if eff is None:
        # Should not happen for Power products (2-6), fail closed to loss.
        return 0.0, -1.0
    return float(eff), float(eff - 1.0)


def grade_slip(
    snapshot: SlipSnapshot,
    actuals: dict[str, float | None],
) -> SlipGrade:
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
    total = len(graded_legs)
    active = total - pushes
    eff_mult, net_units = _effective_multiplier(
        total_legs=total, pushes=pushes, losses=losses
    )
    fragile = sum(1 for g in graded_legs if g.is_fragile_win)

    return SlipGrade(
        slip_id=snapshot.slip_id,
        archetype=snapshot.archetype.value
        if hasattr(snapshot.archetype, "value")
        else str(snapshot.archetype),
        product=snapshot.product.value
        if hasattr(snapshot.product, "value")
        else str(snapshot.product),
        total_legs=total,
        wins=wins,
        losses=losses,
        pushes=pushes,
        active_legs=active,
        original_multiplier=float(snapshot.multiplier),
        effective_multiplier=eff_mult,
        net_units=net_units * float(snapshot.stake_units),
        graded_legs=graded_legs,
        fragile_win_count=fragile,
        can_execute=False,
    )


__all__ = [
    "FRAGILE_WIN_MARGIN",
    "GradedLeg",
    "LegResult",
    "POWER_DOWNGRADE_TABLE",
    "SlipGrade",
    "grade_slip",
]
