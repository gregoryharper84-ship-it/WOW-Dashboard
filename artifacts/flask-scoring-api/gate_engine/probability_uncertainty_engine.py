"""
probability_uncertainty_engine.py
WOW-PATCH-2026-08-08-FOUR-PILLAR-PROBABILITY-UNCERTAINTY-PAYOUT-STRUCTURE

Status: SHADOW_MODE — diagnostic outputs only.  Existing terminal labels remain
controlling.  New probability/uncertainty outputs run side-by-side with legacy
values and are logged for calibration comparison.  No terminal label may be
upgraded based solely on the outputs of this module until the patch is promoted
from SHADOW_MODE to ACTIVE after out-of-sample regression tests pass.

Doctrine (canonical, must not be edited):
  Probability tells us how likely we are to be right.
  Uncertainty tells us how wrong that probability might be.
  Payout tells us how right we need to be.
  Structure tells us whether combining those legs creates a sound card.

Technical invariant (must not be edited):
  Aleatoric risk belongs inside the event distribution.
  Epistemic risk belongs inside the distribution of plausible probabilities.
  Critical unknowns are blockers, not penalties.

Canonical probability hierarchy:
  p_structural  = P(hit | baseline state)
  p_scenario    = Σ_s P(s) · P(hit|s)                [scenario-integrated]
  p_calibrated  = C(p_scenario)                       [calibration function]
  p_true        = Median(p^(1), …, p^(M))             [posterior median]
  p_lb          = Q_{lower_quantile}(p^(1), …, p^(M)) [e.g. Q10]
  p_ub          = Q_{upper_quantile}(p^(1), …, p^(M)) [e.g. Q90]

Aleatoric uncertainty is the variance of outcomes within a single P(hit|s).
Epistemic uncertainty is the variance across plausible probability models.
Do not widen the epistemic posterior because outcomes are volatile — that
volatility is already inside P(hit|s).  Widen only for genuine uncertainty
about which probability model is correct.

can_execute: False (unconditional — this is a shadow/diagnostic module)
ENGINE_VERSION: v16.6-shadow
PATCH_ID: WOW-PATCH-2026-08-08-FOUR-PILLAR-PROBABILITY-UNCERTAINTY-PAYOUT-STRUCTURE
"""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

# ---------------------------------------------------------------------------
# Patch constants
# ---------------------------------------------------------------------------

PATCH_ID          = "WOW-PATCH-2026-08-08-FOUR-PILLAR-PROBABILITY-UNCERTAINTY-PAYOUT-STRUCTURE"
PATCH_STATUS      = "SHADOW_MODE"
ENGINE_VERSION    = "v16.6-shadow"
can_execute: bool = False   # unconditional; never changes


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class EffectMode(Enum):
    MODEL_SHIFT           = "MODEL_SHIFT"
    SCENARIO_MIXTURE      = "SCENARIO_MIXTURE"
    VARIANCE_INFLATION    = "VARIANCE_INFLATION"
    CALIBRATION_ADJUSTMENT = "CALIBRATION_ADJUSTMENT"
    FALLBACK_HAIRCUT      = "FALLBACK_HAIRCUT"
    HARD_BLOCK            = "HARD_BLOCK"


class RiskFamily(Enum):
    ROLE        = "ROLE"
    WORKLOAD    = "WORKLOAD"
    ENVIRONMENT = "ENVIRONMENT"
    MARKET      = "MARKET"
    MODEL       = "MODEL"
    DATA        = "DATA"


class UncertaintyMode(Enum):
    POSTERIOR       = "POSTERIOR"
    FALLBACK_HAIRCUT = "FALLBACK_HAIRCUT"
    BLOCKED         = "BLOCKED"


# ---------------------------------------------------------------------------
# Risk factor
# ---------------------------------------------------------------------------

@dataclass
class RiskFactor:
    """
    A single identified epistemic risk.

    FALLBACK_HAIRCUT mode: adds estimated_effect_mean to the lower-bound
    haircut when posterior samples are present.

    HARD_BLOCK mode: prevents probability publication where required.
    A HARD_BLOCK must never appear as merely one more risk factor — it stops
    the probability from being publishable at all.

    Do not combine FALLBACK_HAIRCUT risks across dependence groups without
    deduplication.  Pass only non-overlapping residual uncertainty.
    """
    risk_id:       str
    risk_family:   RiskFamily
    effect_mode:   EffectMode
    state:         str
    severity:      str

    dependence_group: Optional[str] = None

    resolved: bool  = False
    material: bool  = True

    source:             Optional[str]   = None
    retrieved_at:       Optional[str]   = None
    source_confidence:  Optional[float] = None

    # Only permitted in FALLBACK_HAIRCUT mode.
    estimated_effect_mean: Optional[float] = None
    estimated_effect_sd:   Optional[float] = None


# ---------------------------------------------------------------------------
# Posterior sample
# ---------------------------------------------------------------------------

@dataclass
class PosteriorSample:
    """
    One draw from the epistemic posterior — a plausible probability model.

    hit_probability: P(hit | epistemic world m)

    The aleatoric distribution (individual game outcomes) is already integrated
    inside hit_probability; this sample represents uncertainty about which
    probability model is correct, not game-to-game volatility.
    """
    hit_probability:     float

    scenario_id:         Optional[str]   = None
    role_state:          Optional[str]   = None
    workload_state:      Optional[str]   = None
    environment_state:   Optional[str]   = None
    calibration_draw:    Optional[float] = None


# ---------------------------------------------------------------------------
# Main output object
# ---------------------------------------------------------------------------

@dataclass
class WOWProbabilityOutputs:
    """
    Four-pillar probability output (SHADOW_MODE).

    Probability hierarchy:
      p_structural  → p_scenario → p_calibrated  [deterministic pipeline]
      p_true / p_lb / p_ub                        [from epistemic posterior]

    Payout and structure modules may READ p_true / p_lb but MUST NOT modify
    them.  They add their own fields downstream (required_probability, etc.).
    """
    p_structural: float
    p_scenario:   float
    p_calibrated: float

    posterior_samples: List[PosteriorSample] = field(default_factory=list)

    uncertainty_mode:          UncertaintyMode = UncertaintyMode.POSTERIOR
    uncertainty_model_version: str             = ENGINE_VERSION
    risks:                     List[RiskFactor] = field(default_factory=list)

    lower_quantile: float = 0.10
    upper_quantile: float = 0.90

    # Unconditional — this is a shadow/diagnostic module
    can_execute: bool = False

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _values(self) -> np.ndarray:
        return np.asarray(
            [s.hit_probability for s in self.posterior_samples],
            dtype=float,
        )

    # -----------------------------------------------------------------------
    # Derived properties
    # -----------------------------------------------------------------------

    @property
    def blocking_risks(self) -> List[RiskFactor]:
        return [
            r for r in self.risks
            if r.material and not r.resolved and r.effect_mode == EffectMode.HARD_BLOCK
        ]

    @property
    def publishable(self) -> bool:
        if self.blocking_risks:
            return False
        if self.uncertainty_mode == UncertaintyMode.POSTERIOR:
            return len(self.posterior_samples) > 0
        if self.uncertainty_mode == UncertaintyMode.FALLBACK_HAIRCUT:
            return True
        return False  # BLOCKED

    @property
    def p_true(self) -> Optional[float]:
        """
        Posterior central estimator — declared convention: median.

        This convention is versioned in uncertainty_model_version; do not
        change it silently between versions.
        """
        if not self.publishable:
            return None
        values = self._values()
        if len(values):
            return float(np.median(values))
        # Fallback mode only.
        return float(self.p_calibrated)

    @property
    def p_lb(self) -> Optional[float]:
        """
        Lower bound: Q_{lower_quantile} of the epistemic posterior, further
        reduced by any non-overlapping FALLBACK_HAIRCUT risk factors.

        FALLBACK_HAIRCUT risks represent residual epistemic uncertainty that
        cannot be captured by posterior sampling (e.g. a conflict between
        two minute-projection sources where neither can be verified).
        Do not double-count: aggregate by dependence group before passing.
        """
        if not self.publishable:
            return None
        values = self._values()
        if len(values):
            base_lb = float(
                np.quantile(values, self.lower_quantile, method="linear")
            )
            # Apply FALLBACK_HAIRCUT risk factors as additional epistemic widening
            haircut = sum(
                max(0.0, r.estimated_effect_mean)
                for r in self.risks
                if (
                    r.material
                    and not r.resolved
                    and r.effect_mode == EffectMode.FALLBACK_HAIRCUT
                    and r.estimated_effect_mean is not None
                )
            )
            return max(0.01, base_lb - haircut)
        return self._fallback_lower_bound()

    @property
    def p_ub(self) -> Optional[float]:
        """Upper bound: Q_{upper_quantile} of the epistemic posterior."""
        if not self.publishable:
            return None
        values = self._values()
        if len(values):
            return float(
                np.quantile(values, self.upper_quantile, method="linear")
            )
        return float(self.p_calibrated)

    @property
    def epistemic_width(self) -> Optional[float]:
        """U_epistemic = p_ub - p_lb.  Smaller = more knowable."""
        lb = self.p_lb
        ub = self.p_ub
        if lb is not None and ub is not None:
            return round(ub - lb, 4)
        return None

    @property
    def floor_distance(self) -> Optional[float]:
        """D_floor = p_true - p_lb.  Conservative downside risk."""
        true = self.p_true
        lb   = self.p_lb
        if true is not None and lb is not None:
            return round(true - lb, 4)
        return None

    # -----------------------------------------------------------------------
    # Fallback lower bound (FALLBACK_HAIRCUT mode only)
    # -----------------------------------------------------------------------

    def _fallback_lower_bound(self) -> float:
        """
        Used only when uncertainty_mode == FALLBACK_HAIRCUT and no posterior
        samples exist.

        Do NOT simply sum all penalties.  Combine by dependence group upstream
        and pass only non-overlapping residual uncertainty here.  Calling this
        in POSTERIOR mode without samples is a programming error and raises.
        """
        if self.uncertainty_mode != UncertaintyMode.FALLBACK_HAIRCUT:
            raise RuntimeError(
                "POSTERIOR mode requires posterior samples.  "
                "Silently falling back to p_calibrated would misrepresent "
                "the uncertainty state.  Check why posterior_samples is empty."
            )
        haircut = sum(
            max(0.0, r.estimated_effect_mean)
            for r in self.risks
            if (
                r.material
                and not r.resolved
                and r.effect_mode == EffectMode.FALLBACK_HAIRCUT
                and r.estimated_effect_mean is not None
            )
        )
        return max(0.0, self.p_calibrated - haircut)


# ---------------------------------------------------------------------------
# Constructor — composite joint model
# ---------------------------------------------------------------------------

def build_composite_probability_outputs(
    sim_result,
    conflict_penalty: float = 0.0,
    side: str = "more",
    n_posterior: int = 500,
    seed: int = 42,
) -> WOWProbabilityOutputs:
    """
    Build a WOWProbabilityOutputs from a CompositeSimResult.

    Separation of aleatoric and epistemic uncertainty
    -------------------------------------------------
    The composite simulator runs n_sims joint draws (aleatoric) and
    produces p_more = P(composite > line) and p_less = P(composite < line).
    The correct hit probability is selected by ``side``:
      - "more" / "over" → p_more  (default)
      - "less" / "under" → p_less

    Epistemic uncertainty comes from not knowing whether the hit rate is the
    true value.  A Bayesian Beta(n_hits+1, n_misses+1) posterior over the
    proportion naturally captures this sampling uncertainty.  We draw
    n_posterior samples from this Beta to form the epistemic posterior.

    Minutes source conflict (conflict_penalty > 0) adds a FALLBACK_HAIRCUT
    risk factor that further depresses p_lb, representing the additional
    epistemic uncertainty when two sources disagree about projected minutes.

    Market contradiction is NOT handled here — it belongs in a HARD_BLOCK or
    ceiling, never as a posterior random variable (per doctrine).

    Parameters
    ----------
    sim_result:        CompositeSimResult from run_composite_simulation()
    conflict_penalty:  float from QuorumResult.minutes_conflict_penalty (0–1)
    side:              "more"/"over" or "less"/"under" (default "more")
    n_posterior:       number of epistemic posterior draws (default 500)
    seed:              numpy RNG seed for reproducibility
    """
    rng = np.random.default_rng(seed)

    # Select hit probability based on row side
    _side_norm = (side or "more").lower().strip()
    if _side_norm in ("less", "under", "l", "u"):
        p_scenario = float(sim_result.p_less)
    else:
        p_scenario = float(sim_result.p_more)

    # No separate structural-vs-scenario split at this stage; set equal.
    # A future MLB pitcher specialist will provide regime-free p_structural.
    p_structural = p_scenario
    p_calibrated = p_scenario  # identity calibration for PROVISIONAL model

    # -------------------------------------------------------------------
    # Epistemic posterior via Beta distribution
    # -------------------------------------------------------------------
    # The composite simulator ran sim_result.n_sims draws.  Treat those as
    # Bernoulli trials with a uniform Beta(1,1) prior → Beta posterior.
    n_hits   = max(1, round(p_calibrated * sim_result.n_sims))
    n_misses = max(1, sim_result.n_sims - n_hits)

    raw_draws = rng.beta(n_hits + 1.0, n_misses + 1.0, size=n_posterior)

    samples = [
        PosteriorSample(
            hit_probability=float(d),
            scenario_id=f"beta_draw_{i}",
            workload_state=sim_result.prop_family,
        )
        for i, d in enumerate(raw_draws)
    ]

    # -------------------------------------------------------------------
    # Risk factors
    # -------------------------------------------------------------------
    risks: List[RiskFactor] = []

    if conflict_penalty > 0.0:
        # Minutes source conflict — two adapters disagree on projected minutes.
        # This is genuine epistemic uncertainty: we do not know the true
        # minutes projection, so p_more is less certain than sampling alone
        # would suggest.  Represented as FALLBACK_HAIRCUT on the lower bound.
        #
        # Scale: conflict_penalty=0.20 (two sources differ >15%) → 0.016
        # additional haircut on p_lb (i.e. ~1.6 percentage points).
        # This is conservative and proportional to the disagreement magnitude.
        haircut_mean = round(min(0.05, conflict_penalty * 0.08), 4)
        risks.append(
            RiskFactor(
                risk_id          = "MINUTES_SOURCE_CONFLICT",
                risk_family      = RiskFamily.DATA,
                effect_mode      = EffectMode.FALLBACK_HAIRCUT,
                state            = "CONFLICT",
                severity         = "HIGH" if conflict_penalty >= 0.3 else "MODERATE",
                dependence_group = "minutes_projection",
                resolved         = False,
                material         = True,
                source           = "quorum_resolver",
                estimated_effect_mean = haircut_mean,
                estimated_effect_sd   = round(haircut_mean * 0.5, 4),
            )
        )

    return WOWProbabilityOutputs(
        p_structural       = round(p_structural, 4),
        p_scenario         = round(p_scenario,   4),
        p_calibrated       = round(p_calibrated, 4),
        posterior_samples  = samples,
        uncertainty_mode   = UncertaintyMode.POSTERIOR,
        risks              = risks,
    )
