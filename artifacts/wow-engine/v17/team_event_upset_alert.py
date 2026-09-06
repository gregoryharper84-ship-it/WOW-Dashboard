"""Cross-sport V17 favorite upset-alert interpretation layer.

This module never creates sporting probability. It consumes an already-governed
team/event probability package plus a separately verified market favorite
classification and emits an interpretive alert. Market prices/probabilities do
not enter the sporting model and do not change any probability, admission rule,
NO_PICK threshold, cash gate, portfolio rule, or terminal decision.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

CAN_EXECUTE = False
MUTATES_SPORTING_PROBABILITY = False
MUTATES_ADMISSION = False
MUTATES_CASH_GATE = False
AUTOMATIC_PICK_PROMOTION = False


@dataclass(frozen=True)
class GovernedOutcome:
    label: str
    calibrated_probability: float
    calibrated_lower_bound: float
    calibrated_upper_bound: float

    def validate(self) -> None:
        values = (
            self.calibrated_probability,
            self.calibrated_lower_bound,
            self.calibrated_upper_bound,
        )
        if any(value < 0.0 or value > 1.0 for value in values):
            raise ValueError(f"probability_out_of_range:{self.label}")
        if not (
            self.calibrated_lower_bound
            <= self.calibrated_probability
            <= self.calibrated_upper_bound
        ):
            raise ValueError(f"probability_outside_bounds:{self.label}")


@dataclass(frozen=True)
class UpsetAlert:
    status: str
    alert: bool
    severity: str
    sport: str
    market_favorite: str
    upset_candidate: str | None
    favorite_probability: float | None
    favorite_lower_bound: float | None
    favorite_upper_bound: float | None
    upset_candidate_probability: float | None
    upset_candidate_lower_bound: float | None
    upset_candidate_upper_bound: float | None
    probability_gap: float | None
    reason_codes: tuple[str, ...]
    favorite_failure_path_probability_if_modeled: float | None = None
    largest_favorite_loss_path: str | None = None
    underdog_upset_path: Any = None
    market_role_only: bool = True
    probability_mutated: bool = False
    admission_mutated: bool = False
    cash_gate_mutated: bool = False
    automatic_pick_promotion: bool = False
    can_execute: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce_outcome(value: GovernedOutcome | Mapping[str, Any]) -> GovernedOutcome:
    if isinstance(value, GovernedOutcome):
        outcome = value
    else:
        outcome = GovernedOutcome(
            label=str(value["label"]),
            calibrated_probability=float(value["calibrated_probability"]),
            calibrated_lower_bound=float(value["calibrated_lower_bound"]),
            calibrated_upper_bound=float(value["calibrated_upper_bound"]),
        )
    outcome.validate()
    return outcome


def evaluate_favorite_upset_alert(
    *,
    sport: str,
    market_favorite: str,
    governed_outcomes: Iterable[GovernedOutcome | Mapping[str, Any]],
    market_favorite_verified: bool,
    favorite_failure_path_probability_if_modeled: float | None = None,
    largest_favorite_loss_path: str | None = None,
    underdog_upset_path: Any = None,
) -> UpsetAlert:
    """Classify a market favorite using only governed sporting probabilities.

    Severity is structural rather than based on a universal sport-agnostic
    probability cutoff:

    * HIGH / UPSET_ALERT_MODEL_FLIP: another governed outcome has a higher
      calibrated probability than the market favorite.
    * ELEVATED / UPSET_ALERT_UNCERTAINTY_OVERLAP: the favorite remains the point
      estimate leader, but its calibrated lower bound does not clear the strongest
      alternative's calibrated upper bound.
    * NONE / FAVORITE_MODEL_CLEAR: the favorite is the point-estimate leader and
      its lower bound clears every alternative upper bound.

    This works for two-way and multi-outcome sports (for example soccer with a
    draw) without imposing a universal 50% threshold.
    """
    normalized_favorite = str(market_favorite or "").strip()
    if not market_favorite_verified or not normalized_favorite:
        return UpsetAlert(
            status="UPSET_ALERT_UNAVAILABLE",
            alert=False,
            severity="UNAVAILABLE",
            sport=str(sport or "").upper(),
            market_favorite=normalized_favorite,
            upset_candidate=None,
            favorite_probability=None,
            favorite_lower_bound=None,
            favorite_upper_bound=None,
            upset_candidate_probability=None,
            upset_candidate_lower_bound=None,
            upset_candidate_upper_bound=None,
            probability_gap=None,
            reason_codes=("MARKET_FAVORITE_CLASSIFICATION_UNVERIFIED",),
        )

    outcomes = tuple(_coerce_outcome(value) for value in governed_outcomes)
    if len(outcomes) < 2:
        raise ValueError("governed_outcome_space_insufficient")

    by_label = {outcome.label.casefold(): outcome for outcome in outcomes}
    if len(by_label) != len(outcomes):
        raise ValueError("duplicate_governed_outcome_label")
    favorite = by_label.get(normalized_favorite.casefold())
    if favorite is None:
        raise ValueError("market_favorite_not_in_governed_outcome_space")

    alternatives = [outcome for outcome in outcomes if outcome is not favorite]
    strongest = max(
        alternatives,
        key=lambda outcome: (
            outcome.calibrated_probability,
            outcome.calibrated_upper_bound,
            outcome.label,
        ),
    )
    probability_gap = favorite.calibrated_probability - strongest.calibrated_probability

    reasons: list[str] = []
    if strongest.calibrated_probability > favorite.calibrated_probability:
        status = "UPSET_ALERT_MODEL_FLIP"
        severity = "HIGH"
        alert = True
        reasons.append("GOVERNED_MODEL_PREFERS_NON_FAVORITE_OUTCOME")
    elif strongest.calibrated_upper_bound >= favorite.calibrated_lower_bound:
        status = "UPSET_ALERT_UNCERTAINTY_OVERLAP"
        severity = "ELEVATED"
        alert = True
        reasons.append("FAVORITE_LOWER_BOUND_OVERLAPS_ALTERNATIVE_UPPER_BOUND")
    else:
        status = "FAVORITE_MODEL_CLEAR"
        severity = "NONE"
        alert = False
        reasons.append("FAVORITE_LOWER_BOUND_CLEARS_ALL_ALTERNATIVE_UPPER_BOUNDS")

    if favorite_failure_path_probability_if_modeled is not None:
        if not 0.0 <= favorite_failure_path_probability_if_modeled <= 1.0:
            raise ValueError("favorite_failure_path_probability_out_of_range")
        reasons.append("MODELED_FAVORITE_FAILURE_PATH_AVAILABLE")
    if largest_favorite_loss_path:
        reasons.append("FAVORITE_LOSS_PATH_IDENTIFIED")
    if underdog_upset_path is not None:
        reasons.append("UNDERDOG_UPSET_PATH_IDENTIFIED")

    return UpsetAlert(
        status=status,
        alert=alert,
        severity=severity,
        sport=str(sport or "").upper(),
        market_favorite=normalized_favorite,
        upset_candidate=strongest.label,
        favorite_probability=favorite.calibrated_probability,
        favorite_lower_bound=favorite.calibrated_lower_bound,
        favorite_upper_bound=favorite.calibrated_upper_bound,
        upset_candidate_probability=strongest.calibrated_probability,
        upset_candidate_lower_bound=strongest.calibrated_lower_bound,
        upset_candidate_upper_bound=strongest.calibrated_upper_bound,
        probability_gap=probability_gap,
        reason_codes=tuple(reasons),
        favorite_failure_path_probability_if_modeled=favorite_failure_path_probability_if_modeled,
        largest_favorite_loss_path=largest_favorite_loss_path,
        underdog_upset_path=underdog_upset_path,
    )


__all__ = [
    "AUTOMATIC_PICK_PROMOTION",
    "CAN_EXECUTE",
    "GovernedOutcome",
    "MUTATES_ADMISSION",
    "MUTATES_CASH_GATE",
    "MUTATES_SPORTING_PROBABILITY",
    "UpsetAlert",
    "evaluate_favorite_upset_alert",
]
