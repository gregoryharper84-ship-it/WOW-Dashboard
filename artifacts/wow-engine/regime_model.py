"""
regime_model.py
WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE v2, Section 8B.1

Empirical-Bayes Dirichlet-multinomial estimation of PRIMARY REGIME
probabilities for a pitcher/start. Mutually exclusive regimes only —
cause tags are separate, non-exclusive annotations attached after the
fact and never enter the probability sum.

This module does NOT invent probability adjustments from current-game
info (injury, rest, pitch cap, etc.) — see `apply_current_game_signal`,
which only ever widens uncertainty, changes eligibility, or blocks; it
never nudges a probability without a validated (fitted) coefficient.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import math


class PrimaryRegime(str, Enum):
    NORMAL_EFFECTIVE_OUTING = "R0_NORMAL_EFFECTIVE_OUTING"
    INEFFICIENT_SURVIVING_OUTING = "R1_INEFFICIENT_SURVIVING_OUTING"
    EARLY_EXIT_PERFORMANCE = "R2_EARLY_EXIT_PERFORMANCE"
    EARLY_EXIT_HEALTH_OR_WORKLOAD = "R3_EARLY_EXIT_HEALTH_OR_WORKLOAD"
    PLANNED_RESTRICTION_OR_SHORT_LEASH = "R4_PLANNED_RESTRICTION_OR_SHORT_LEASH"
    GAME_DISRUPTION = "R5_GAME_DISRUPTION"


class CauseTag(str, Enum):
    COMMAND_COLLAPSE = "COMMAND_COLLAPSE"
    OPPONENT_EXTENSION = "OPPONENT_EXTENSION"
    MANAGER_HOOK_PRESSURE = "MANAGER_HOOK_PRESSURE"
    PITCH_EFFICIENCY_FAILURE = "PITCH_EFFICIENCY_FAILURE"
    BULLPEN_READINESS_PRESSURE = "BULLPEN_READINESS_PRESSURE"
    VELOCITY_OR_HEALTH_WARNING = "VELOCITY_OR_HEALTH_WARNING"
    CONTACT_EXTENSION = "CONTACT_EXTENSION"
    PATIENCE_EXTENSION = "PATIENCE_EXTENSION"
    WEATHER_OR_DELAY = "WEATHER_OR_DELAY"
    DEFENSIVE_EXTENSION = "DEFENSIVE_EXTENSION"


REGIME_MODEL_VERSION = "REGIME_MODEL_V1_DIRICHLET_MULTINOMIAL"

KAPPA_MIN, KAPPA_MAX, KAPPA_FALLBACK = 5.0, 30.0, 12.0


@dataclass
class CohortCounts:
    """Regime counts for the matched cohort (league/role/era/handedness/
    workload band/market family), used to build the Dirichlet prior."""
    counts: dict[PrimaryRegime, int]

    def total(self) -> int:
        return sum(self.counts.values())

    def prior_probabilities(self) -> dict[PrimaryRegime, float]:
        total = self.total()
        if total <= 0:
            # Uninformative fallback: uniform prior over regimes.
            n = len(PrimaryRegime)
            return {r: 1.0 / n for r in PrimaryRegime}
        return {r: c / total for r, c in self.counts.items()}


@dataclass
class PitcherCounts:
    """Observed regime counts for this specific pitcher (small-N case is
    the normal case — that's why shrinkage exists)."""
    counts: dict[PrimaryRegime, int] = field(default_factory=dict)

    def n(self, r: PrimaryRegime) -> int:
        return self.counts.get(r, 0)

    def total(self) -> int:
        return sum(self.counts.values())


def estimate_kappa(marginal_likelihood_fn=None) -> float:
    """Prefer a marginal-likelihood kappa estimate from historical training
    data. `marginal_likelihood_fn`, if provided, is a callable that returns
    an optimized kappa; this function only enforces the bounds/fallback
    contract from 8B.1 — it does not itself define the optimization.
    """
    if marginal_likelihood_fn is None:
        return KAPPA_FALLBACK
    try:
        kappa = float(marginal_likelihood_fn())
    except Exception:
        return KAPPA_FALLBACK
    if not (KAPPA_MIN <= kappa <= KAPPA_MAX) or math.isnan(kappa):
        return KAPPA_FALLBACK
    return kappa


def dirichlet_multinomial_regime_probabilities(
    cohort: CohortCounts,
    pitcher: PitcherCounts,
    kappa: Optional[float] = None,
) -> dict[PrimaryRegime, float]:
    """
    P(r | pitcher) = (n_pitcher_r + alpha_r) / (N_pitcher + sum(alpha_r))
    alpha_r = kappa * P_cohort(r)
    """
    if kappa is None:
        kappa = estimate_kappa()

    p_cohort = cohort.prior_probabilities()
    alpha = {r: kappa * p_cohort[r] for r in PrimaryRegime}
    alpha_sum = sum(alpha.values())
    n_pitcher = pitcher.total()

    result = {}
    for r in PrimaryRegime:
        result[r] = (pitcher.n(r) + alpha[r]) / (n_pitcher + alpha_sum)

    # Numerical safety: renormalize to satisfy the 1e-6 sum constraint.
    total = sum(result.values())
    if total > 0:
        result = {r: v / total for r, v in result.items()}
    return result


@dataclass
class CurrentGameSignal:
    """Disclosed current-game info. Each field is either backed by a
    validated coefficient (rare, must be explicitly supplied) or is
    treated as an uncertainty/eligibility/block signal only."""
    injury_flag: bool = False
    post_il: bool = False
    rest_days: Optional[int] = None
    announced_pitch_cap: Optional[int] = None
    velocity_warning: bool = False
    confirmed_opener: bool = False
    weather_delay_risk: bool = False
    validated_coefficients: dict[str, float] = field(default_factory=dict)


class SignalAction(str, Enum):
    APPLY_LEARNED_ADJUSTMENT = "APPLY_LEARNED_ADJUSTMENT"
    WIDEN_UNCERTAINTY = "WIDEN_UNCERTAINTY"
    CHANGE_ELIGIBILITY = "CHANGE_ELIGIBILITY"
    HOLD = "HOLD"
    BLOCK = "BLOCK"


def apply_current_game_signal(
    regime_probs: dict[PrimaryRegime, float],
    signal: CurrentGameSignal,
) -> tuple[dict[PrimaryRegime, float], list[SignalAction], list[str]]:
    """
    Never invents a probability nudge. Per field:
      - a validated coefficient exists (in signal.validated_coefficients)
        -> apply it (log-odds shift on the affected regime, then
           renormalize)
      - no validated coefficient -> widen uncertainty / change
        eligibility / HOLD
      - a material contradiction (e.g. confirmed_opener contradicts a
        full-outing regime distribution) -> BLOCK
    Returns (possibly-adjusted regime_probs, actions_taken, notes).
    """
    actions: list[SignalAction] = []
    notes: list[str] = []
    probs = dict(regime_probs)

    def _apply_validated(field_name: str, target_regime: PrimaryRegime):
        coef = signal.validated_coefficients.get(field_name)
        if coef is None:
            actions.append(SignalAction.WIDEN_UNCERTAINTY)
            notes.append(f"{field_name}: no validated coefficient — widened uncertainty only")
            return
        # log-odds shift on the target regime, renormalize
        p = probs[target_regime]
        p = max(min(p, 1 - 1e-9), 1e-9)
        logit = math.log(p / (1 - p)) + coef
        new_p = 1 / (1 + math.exp(-logit))
        probs[target_regime] = new_p
        total = sum(probs.values())
        probs.update({r: v / total for r, v in probs.items()})
        actions.append(SignalAction.APPLY_LEARNED_ADJUSTMENT)
        notes.append(f"{field_name}: applied validated coefficient {coef}")

    if signal.injury_flag or signal.post_il:
        _apply_validated("injury_or_post_il", PrimaryRegime.EARLY_EXIT_HEALTH_OR_WORKLOAD)

    if signal.velocity_warning:
        _apply_validated("velocity_warning", PrimaryRegime.EARLY_EXIT_HEALTH_OR_WORKLOAD)

    if signal.announced_pitch_cap is not None:
        _apply_validated("announced_pitch_cap", PrimaryRegime.PLANNED_RESTRICTION_OR_SHORT_LEASH)

    if signal.weather_delay_risk:
        _apply_validated("weather_delay_risk", PrimaryRegime.GAME_DISRUPTION)

    if signal.confirmed_opener:
        # Material contradiction: an "opener" role is structurally
        # incompatible with a normal 5-6 inning regime distribution built
        # for a traditional starter cohort. Block rather than guess.
        actions.append(SignalAction.BLOCK)
        notes.append("confirmed_opener contradicts starter-cohort regime distribution — BLOCK")

    return probs, actions, notes


def regime_probability_sum_check(probs: dict[PrimaryRegime, float]) -> bool:
    return abs(sum(probs.values()) - 1.0) <= 1e-6


class ExitReason(str, Enum):
    """A single categorical field, assigned from box-score/play-by-play
    data at ingestion time (not invented per-candidate). Having exactly
    one exit_reason per start is what makes the if/elif classification
    below mutually exclusive by construction, rather than by assertion."""
    COMPLETED_NORMAL = "COMPLETED_NORMAL"
    PERFORMANCE_PULL = "PERFORMANCE_PULL"
    INJURY_OR_HEALTH = "INJURY_OR_HEALTH"
    PLANNED_RESTRICTION = "PLANNED_RESTRICTION"
    GAME_DISRUPTED = "GAME_DISRUPTED"


@dataclass
class StartObservation:
    innings_pitched: float
    pitch_count: int
    exit_reason: ExitReason
    efficient_pitch_threshold_per_inning: float = 16.0


def classify_historical_start(obs: StartObservation) -> PrimaryRegime:
    """
    Deterministic, priority-ordered, mutually-exclusive-by-construction
    classification of one observed start into exactly one PrimaryRegime.
    Priority order (highest first): disruption > health/workload exit >
    planned restriction > performance-based early exit > normal-length
    outing, where a normal-length outing is further split into
    NORMAL_EFFECTIVE_OUTING vs INEFFICIENT_SURVIVING_OUTING by pitch
    efficiency. Every branch returns exactly one regime; no branch can
    fall through to another, so double-counting is structurally
    impossible for this function.
    """
    if obs.exit_reason == ExitReason.GAME_DISRUPTED:
        return PrimaryRegime.GAME_DISRUPTION
    if obs.exit_reason == ExitReason.INJURY_OR_HEALTH:
        return PrimaryRegime.EARLY_EXIT_HEALTH_OR_WORKLOAD
    if obs.exit_reason == ExitReason.PLANNED_RESTRICTION:
        return PrimaryRegime.PLANNED_RESTRICTION_OR_SHORT_LEASH
    if obs.exit_reason == ExitReason.PERFORMANCE_PULL:
        return PrimaryRegime.EARLY_EXIT_PERFORMANCE

    # COMPLETED_NORMAL: pitcher went the expected outing length. Split by
    # efficiency only — this is the one place two regimes are reachable
    # from the same exit_reason, but the split itself is a single
    # if/else, so still exactly one output.
    avg_pitch_per_inning = obs.pitch_count / max(obs.innings_pitched, 1e-9)
    if avg_pitch_per_inning > obs.efficient_pitch_threshold_per_inning:
        return PrimaryRegime.INEFFICIENT_SURVIVING_OUTING
    return PrimaryRegime.NORMAL_EFFECTIVE_OUTING
