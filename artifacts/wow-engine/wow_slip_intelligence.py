"""
Slip-intelligence learning stream (separate from prediction intelligence).

Research-only diagnostics over paper-trade slips/grades. Never mutates
calibrated probabilities, never auto-promotes archetypes, and never sets
can_execute=true.

Prediction accuracy/calibration remains owned by the existing prediction
ledgers; this module only summarizes slip-construction performance.
"""
from __future__ import annotations

from math import prod
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from wow_slip_builder import SlipSnapshot
from wow_slip_grader import SlipGrade

STREAM: Literal["SLIP_INTELLIGENCE"] = "SLIP_INTELLIGENCE"
PREDICTION_STREAM: Literal["PREDICTION_INTELLIGENCE"] = "PREDICTION_INTELLIGENCE"

# Evidence floor before any promotion *review* is even considered.
MIN_DECIDED_LEGS_FOR_PROMOTION_REVIEW = 100
MIN_LEG_HIT_RATE_FOR_PROMOTION_REVIEW = 0.56


class SlipExposureReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stream: Literal["SLIP_INTELLIGENCE"] = STREAM
    slip_id: str
    archetype: str
    leg_count: int
    distinct_players: int
    distinct_games: int
    player_concentration_max: int
    game_concentration_max: int
    same_game_pairs: int
    independence_combined_probability: float | None = None
    independence_combined_lower_bound: float | None = None
    payout_multiplier: float
    can_execute: Literal[False] = False
    notes: list[str] = Field(default_factory=list)


class SlipLearningRecord(BaseModel):
    """One graded slip's contribution to the slip-intelligence stream."""

    model_config = ConfigDict(extra="forbid")

    stream: Literal["SLIP_INTELLIGENCE"] = STREAM
    slip_id: str
    archetype: str
    product: str
    leg_count: int
    wins: int
    losses: int
    pushes: int
    fragile_win_count: int
    effective_multiplier: float
    net_units: float
    failed_leg_ids: list[str] = Field(default_factory=list)
    fragile_leg_ids: list[str] = Field(default_factory=list)
    clears_promotion_gate: bool = False
    can_execute: Literal[False] = False


def assess_slip_exposure(snapshot: SlipSnapshot) -> SlipExposureReport:
    """Correlation/concentration diagnostics + independence joint probability."""
    players: dict[str, int] = {}
    games: dict[str, int] = {}
    for leg in snapshot.legs:
        players[leg.player_id] = players.get(leg.player_id, 0) + 1
        games[leg.game_id] = games.get(leg.game_id, 0) + 1

    same_game_pairs = sum(c * (c - 1) // 2 for c in games.values())
    notes: list[str] = []
    if any(c > 1 for c in players.values()):
        notes.append("PLAYER_DUPLICATE_PRESENT")
    if same_game_pairs > 0:
        notes.append("SAME_GAME_CORRELATION_EXPOSURE")
    notes.append("JOINT_PROBABILITY_ASSUMES_CONDITIONAL_INDEPENDENCE")
    notes.append("NOT_AN_AUTO_PROMOTION_SIGNAL")

    probs = [leg.calibrated_probability for leg in snapshot.legs]
    lowers = [leg.calibrated_lower_bound for leg in snapshot.legs]
    joint_p = float(prod(probs)) if probs else None
    joint_lb = float(prod(lowers)) if lowers else None

    return SlipExposureReport(
        slip_id=str(snapshot.slip_id),
        archetype=snapshot.archetype.value
        if hasattr(snapshot.archetype, "value")
        else str(snapshot.archetype),
        leg_count=len(snapshot.legs),
        distinct_players=len(players),
        distinct_games=len(games),
        player_concentration_max=max(players.values()) if players else 0,
        game_concentration_max=max(games.values()) if games else 0,
        same_game_pairs=same_game_pairs,
        independence_combined_probability=joint_p,
        independence_combined_lower_bound=joint_lb,
        payout_multiplier=float(snapshot.multiplier),
        can_execute=False,
        notes=notes,
    )


def record_slip_learning(grade: SlipGrade) -> SlipLearningRecord:
    decided = grade.wins + grade.losses
    hit_rate = (grade.wins / decided) if decided else 0.0
    clears = (
        decided >= MIN_DECIDED_LEGS_FOR_PROMOTION_REVIEW
        and hit_rate > MIN_LEG_HIT_RATE_FOR_PROMOTION_REVIEW
    )
    return SlipLearningRecord(
        slip_id=str(grade.slip_id),
        archetype=grade.archetype,
        product=grade.product,
        leg_count=grade.total_legs,
        wins=grade.wins,
        losses=grade.losses,
        pushes=grade.pushes,
        fragile_win_count=grade.fragile_win_count,
        effective_multiplier=grade.effective_multiplier,
        net_units=grade.net_units,
        failed_leg_ids=[
            g.leg_id for g in grade.graded_legs if g.result.value == "LOSS"
        ],
        fragile_leg_ids=[g.leg_id for g in grade.graded_legs if g.is_fragile_win],
        clears_promotion_gate=clears,
        can_execute=False,
    )


def streams_are_separated() -> dict[str, Any]:
    """Explicit contract: slip vs prediction learning stay independent."""
    return {
        "prediction_stream": PREDICTION_STREAM,
        "slip_stream": STREAM,
        "auto_promote_archetypes": False,
        "min_decided_legs_for_promotion_review": MIN_DECIDED_LEGS_FOR_PROMOTION_REVIEW,
        "min_leg_hit_rate_for_promotion_review": MIN_LEG_HIT_RATE_FOR_PROMOTION_REVIEW,
        "can_execute": False,
        "note": (
            "Slip ROI/hit-rate evidence must not rewrite individual calibrated "
            "probabilities; prediction calibration must not auto-edit slip archetypes."
        ),
    }


__all__ = [
    "MIN_DECIDED_LEGS_FOR_PROMOTION_REVIEW",
    "MIN_LEG_HIT_RATE_FOR_PROMOTION_REVIEW",
    "PREDICTION_STREAM",
    "STREAM",
    "SlipExposureReport",
    "SlipLearningRecord",
    "assess_slip_exposure",
    "record_slip_learning",
    "streams_are_separated",
]
