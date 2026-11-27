"""
Kalshi live secondary-market exit decision engine (paper-trade only).

Evaluates whether a live Kalshi SGP position should TAKE_PROFIT, CUT_LOSS,
or HOLD. Never places, modifies, or cancels live market orders.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LiveSlipState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slip_id: str
    cost: float
    current_bid: float
    combo_live_prob: float
    legs_settled_won: int
    legs_remaining: int
    remaining_leg_live_prob: float
    remaining_leg_market: Optional[str] = None
    quarter_or_period: int
    seconds_remaining: int
    score_diff: int
    has_injury_or_ejection: bool = False
    has_blowout_benching: bool = False

    @model_validator(mode="after")
    def _non_negative(self) -> LiveSlipState:
        if self.cost < 0 or self.current_bid < 0:
            raise ValueError("cost and current_bid must be >= 0")
        if not (0.0 <= self.combo_live_prob <= 1.0):
            raise ValueError("combo_live_prob must be in [0, 1]")
        if not (0.0 <= self.remaining_leg_live_prob <= 1.0):
            raise ValueError("remaining_leg_live_prob must be in [0, 1]")
        return self


class ExitDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["TAKE_PROFIT", "CUT_LOSS", "HOLD"]
    reason: str
    projected_return_pct: float
    can_execute: Literal[False] = False

    @model_validator(mode="after")
    def _dry_run(self) -> ExitDecision:
        if self.can_execute is not False:
            raise ValueError("can_execute must be False")
        return self


def evaluate_kalshi_exit(state: LiveSlipState) -> ExitDecision:
    """
    Paper-trade exit policy for Kalshi SGPs.

    TAKE_PROFIT locks fragile final-leg equity or near-certain live combo prob.
    CUT_LOSS only on structural kill switches (injury/blowout/late collapse).
    HOLD is the anti-panic default on non-structural mid-game drawdowns.
    """
    cost = float(state.cost) if state.cost else 0.0
    if cost <= 0:
        projected = 0.0
    else:
        projected = ((float(state.current_bid) / cost) - 1.0) * 100.0

    # 1) TAKE_PROFIT
    near_certain = state.combo_live_prob >= 0.96 and state.current_bid > state.cost
    fragile_final = (
        state.current_bid >= state.cost * 1.15
        and state.legs_remaining == 1
        and (
            state.remaining_leg_live_prob < 0.65
            or state.remaining_leg_market == "nfl_rush_yards"
        )
    )
    if near_certain:
        return ExitDecision(
            action="TAKE_PROFIT",
            reason="combo_live_prob>=0.96 with bid above cost",
            projected_return_pct=projected,
            can_execute=False,
        )
    if fragile_final:
        return ExitDecision(
            action="TAKE_PROFIT",
            reason="fragile final-leg profit lock (>=15% bid, remaining risk)",
            projected_return_pct=projected,
            can_execute=False,
        )

    # 2) CUT_LOSS — structural only
    underwater = state.current_bid < state.cost
    late_collapse = (
        state.combo_live_prob < 0.22
        and state.quarter_or_period >= 4
        and state.seconds_remaining <= 360
    )
    if underwater and (
        state.has_injury_or_ejection
        or state.has_blowout_benching
        or late_collapse
    ):
        if state.has_injury_or_ejection:
            reason = "injury/ejection kill switch while underwater"
        elif state.has_blowout_benching:
            reason = "blowout benching kill switch while underwater"
        else:
            reason = "4Q collapse (live_prob<0.22, <=360s) while underwater"
        return ExitDecision(
            action="CUT_LOSS",
            reason=reason,
            projected_return_pct=projected,
            can_execute=False,
        )

    # 3) HOLD — anti-panic default
    reason = "anti-panic hold"
    if underwater:
        reason = "anti-panic hold on non-structural mid-game drawdown"
    elif state.current_bid >= state.cost:
        reason = "hold while bid covers cost without take-profit trigger"
    return ExitDecision(
        action="HOLD",
        reason=reason,
        projected_return_pct=projected,
        can_execute=False,
    )


__all__ = [
    "ExitDecision",
    "LiveSlipState",
    "evaluate_kalshi_exit",
]
