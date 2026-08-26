"""
simulation.py
WOW-PATCH-2026-08-26-FREE-HOST-PROBABILITY-ENGINE v2, Section 8B.1

Conditional prop simulation inside each primary regime, then combined:
    P(prop) = Sum [ P(regime) x P(prop | regime) ]

>= 50,000 Monte Carlo draws required for live scoring. Deterministic
seed is recorded in the ledger for reproducibility (deployment gate #4).

Per-regime rate parameters (innings/BF distribution, K-per-BF, pitch
count/leash) are NOT hardcoded here — they must be supplied by the
caller from real fitted data (see `RegimeConditionalParams`). This
module refuses to guess distribution shapes; that would reintroduce
the "manually invented coefficient" problem the patch exists to avoid.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import numpy as np

from regime_model import PrimaryRegime

MIN_SIMULATION_DRAWS = 50_000


@dataclass
class RegimeConditionalParams:
    """Fitted (not invented) per-regime distribution parameters for one
    prop type. batters_faced_dist and k_per_bf are numpy-samplable
    callables: f(rng, n) -> array of length n."""
    regime: PrimaryRegime
    batters_faced_sampler: callable          # f(rng, n_draws) -> array[int]
    stat_rate_sampler: callable              # f(rng, n_draws, batters_faced) -> array[float] (e.g. K rate per BF context)


@dataclass
class SimulationResult:
    primary_failure_path: PrimaryRegime | None
    regime_probabilities: dict[PrimaryRegime, float]
    p_prop_given_regime: dict[PrimaryRegime, float]
    p_prop_unconditional: float
    simulation_seed: int
    simulation_draws: int
    # Raw boolean hit draws per regime, underlying p_prop_given_regime.
    # Not part of the published ledger row -- exposed so the ratified
    # PREDICTIVE_BOUNDS_V1 amendment (calibration.compute_predictive_bounds)
    # can bootstrap a candidate raw-probability realization from the
    # actual simulation, via bootstrap_candidate_raw_probability_sampler()
    # below, instead of needing a second simulation pass.
    hits_by_regime: dict[PrimaryRegime, np.ndarray]


def simulate_prop_probability(
    regime_probs: dict[PrimaryRegime, float],
    regime_params: dict[PrimaryRegime, RegimeConditionalParams],
    line: float,
    direction: str,               # "MORE" or "LESS"
    seed: int,
    draws: int = MIN_SIMULATION_DRAWS,
) -> SimulationResult:
    if draws < MIN_SIMULATION_DRAWS:
        raise ValueError(
            f"simulation_draws must be >= {MIN_SIMULATION_DRAWS} for live scoring "
            f"(8B.2 hard constraint); got {draws}"
        )
    if direction not in ("MORE", "LESS"):
        raise ValueError("direction must be 'MORE' or 'LESS'")

    missing = set(regime_probs) - set(regime_params)
    if missing:
        # Section 8B.1: no regime may be silently treated as zero. If we
        # don't have conditional params for a regime with nonzero
        # probability, the caller must not proceed to publication.
        raise MissingRegimeDataError(
            f"Missing conditional simulation params for regimes: {missing}. "
            f"Per 8B.1, unconditional probability cannot be published."
        )

    rng = np.random.default_rng(seed)

    p_prop_given_regime: dict[PrimaryRegime, float] = {}
    hits_by_regime: dict[PrimaryRegime, np.ndarray] = {}
    weighted_sum = 0.0

    for regime, p_r in regime_probs.items():
        n_r = max(int(round(draws * p_r)), 1)
        params = regime_params[regime]

        bf = params.batters_faced_sampler(rng, n_r)
        stat_draws = params.stat_rate_sampler(rng, n_r, bf)

        if direction == "MORE":
            hits = stat_draws > line
        else:
            hits = stat_draws < line

        p_given_regime = float(np.mean(hits))
        p_prop_given_regime[regime] = p_given_regime
        hits_by_regime[regime] = hits
        weighted_sum += p_r * p_given_regime

    primary = max(regime_probs, key=regime_probs.get) if regime_probs else None

    return SimulationResult(
        primary_failure_path=primary,
        regime_probabilities=regime_probs,
        p_prop_given_regime=p_prop_given_regime,
        p_prop_unconditional=weighted_sum,
        simulation_seed=seed,
        simulation_draws=draws,
        hits_by_regime=hits_by_regime,
    )


def bootstrap_candidate_raw_probability_sampler(
    regime_probabilities: dict[PrimaryRegime, float],
    hits_by_regime: dict[PrimaryRegime, np.ndarray],
) -> Callable[[np.random.Generator], float]:
    """Returns a callable `rng -> float` that draws one bootstrap
    realization of THIS candidate's own raw (pre-calibration) probability
    -- resampling each regime's simulated hit draws with replacement and
    recombining with the same P(prop) = Sum[P(regime) x P(prop|regime)]
    weighting used for the point estimate. This is "the candidate raw-
    probability realization from the sport-specific simulation/bootstrap
    path" the ratified PREDICTIVE_BOUNDS_V1 amendment calls for."""
    def _sample(rng: np.random.Generator) -> float:
        total = 0.0
        for regime, p_r in regime_probabilities.items():
            hits = hits_by_regime[regime]
            resampled = rng.choice(hits, size=len(hits), replace=True)
            total += p_r * float(np.mean(resampled))
        return total
    return _sample


class MissingRegimeDataError(Exception):
    """Raised when a nonzero-probability regime lacks conditional
    simulation parameters. Per 8B.1, this blocks unconditional
    probability publication — callers must set
    probability_publishable = false and record the gap, not fall back to
    a partial sum."""
    pass
