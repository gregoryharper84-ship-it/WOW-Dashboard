"""
composite_simulator.py — Correlated joint PTS/REB/AST simulation.

All three components share the same per-simulation minutes draw, which
naturally induces positive Pearson r between PTS, REB, and AST.
Replaces the independent-sum path in component_composite.py when
joint_model_provided=True and an OpportunityState is present.

Entry point: run_composite_simulation(opportunity_state, prop_family, line, n_sims, seed)
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

from .types import OpportunityState, MinutesDistribution, PropFamily


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class CompositeSimResult:
    """Bidirectional hit probability + component diagnostics."""
    prop_family:    str
    line:           float
    n_sims:         int

    p_more:         float   # P(composite > line)
    p_less:         float   # P(composite < line)
    p_push:         float   # P(composite == line exactly; rare for fractional lines)

    # Component means (diagnostics only — not used for qualification)
    mean_pts:       float | None = None
    mean_reb:       float | None = None
    mean_ast:       float | None = None
    mean_composite: float        = 0.0

    # Correlation metrics
    pearson_r_pts_reb: float | None = None
    pearson_r_pts_ast: float | None = None

    # Regime distribution
    regime_distribution: dict[str, float] = field(default_factory=dict)

    notes: list[str] = field(default_factory=list)
    can_execute: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "can_execute":         False,
            "prop_family":         self.prop_family,
            "line":                self.line,
            "n_sims":              self.n_sims,
            "p_more":              round(self.p_more, 4),
            "p_less":              round(self.p_less, 4),
            "p_push":              round(self.p_push, 4),
            "mean_pts":            self.mean_pts,
            "mean_reb":            self.mean_reb,
            "mean_ast":            self.mean_ast,
            "mean_composite":      round(self.mean_composite, 3),
            "pearson_r_pts_reb":   self.pearson_r_pts_reb,
            "pearson_r_pts_ast":   self.pearson_r_pts_ast,
            "regime_distribution": self.regime_distribution,
            "notes":               self.notes,
        }


# ---------------------------------------------------------------------------
# Default per-minute rates (league averages; overridden by OpportunityState)
# ---------------------------------------------------------------------------
_DEFAULT_RATES = {
    "points":   1.40,   # ~28 pts / 20 min
    "rebounds": 0.45,   # ~9 reb / 20 min
    "assists":  0.35,   # ~7 ast / 20 min
}

# Pace / game-script regime weights
_REGIME_WEIGHTS = {
    "normal":  0.65,
    "blowout": 0.20,
    "foul":    0.15,
}

# Per-regime rate multipliers
_REGIME_MULTIPLIERS: dict[str, dict[str, float]] = {
    "normal":  {"points": 1.00, "rebounds": 1.00, "assists": 1.00},
    "blowout": {"points": 0.72, "rebounds": 0.75, "assists": 0.70},   # DNP risk
    "foul":    {"points": 1.15, "rebounds": 0.90, "assists": 0.95},   # extra FTs
}

# Components for each composite family
_FAMILY_COMPONENTS: dict[str, list[str]] = {
    "pra":      ["points", "rebounds", "assists"],
    "p+r":      ["points", "rebounds"],
    "r+a":      ["rebounds", "assists"],
    "p+a":      ["points", "assists"],
    "points":   ["points"],
    "rebounds": ["rebounds"],
    "assists":  ["assists"],
}

# Alias map: every accepted display form → simulator canonical key.
# Must cover every alias accepted by is_composite_prop_row() so the
# simulator never silently falls through to a points-only composite.
def canonicalize_prop_family(raw_prop_type: str) -> str:
    """
    Normalize a raw prop_type string to a simulator-canonical composite
    family key using the shared gate_engine.component_composite.STAT_FAMILY_ALIASES
    registry.

    This is the single authoritative canonicalization path for all composite
    prop alias handling.  is_composite_prop_row(), the pipeline, and this
    module all go through the same shared table so they stay in sync.

    Raises ValueError for families not in _FAMILY_COMPONENTS so callers
    catch routing errors early instead of silently getting wrong components.
    """
    from gate_engine.component_composite import STAT_FAMILY_ALIASES  # shared registry
    key = raw_prop_type.lower().replace(" ", "")
    canonical = STAT_FAMILY_ALIASES.get(key, key)
    if canonical not in _FAMILY_COMPONENTS:
        raise ValueError(
            f"Unsupported composite prop family: '{canonical}' "
            f"(normalized from raw: '{raw_prop_type}'). "
            f"Supported families: {sorted(_FAMILY_COMPONENTS.keys())}"
        )
    return canonical


# ---------------------------------------------------------------------------
# Main simulator
# ---------------------------------------------------------------------------

def run_composite_simulation(
    opportunity_state: OpportunityState,
    prop_family: str,
    line: float,
    n_sims: int = 5000,
    seed: int | None = None,
) -> CompositeSimResult:
    """
    Correlated joint simulation for composite props.

    All component stats (PTS, REB, AST) share the same per-draw minutes value,
    which induces positive Pearson r — the key architectural requirement.
    """
    rng = random.Random(seed)

    family_key = prop_family.lower().strip()
    components = _FAMILY_COMPONENTS.get(family_key)
    if components is None:
        raise ValueError(
            f"run_composite_simulation: unsupported prop family '{family_key}'. "
            f"Call canonicalize_prop_family() first. "
            f"Supported: {sorted(_FAMILY_COMPONENTS.keys())}"
        )
    notes: list[str] = []

    # -----------------------------------------------------------------------
    # Resolve per-minute rates from OpportunityState (or use defaults)
    # -----------------------------------------------------------------------
    cor = opportunity_state.component_opportunity
    rates = {
        "points":   (cor.scoring_per_min    if cor and cor.scoring_per_min    is not None else _DEFAULT_RATES["points"]),
        "rebounds": (cor.rebounding_per_min if cor and cor.rebounding_per_min is not None else _DEFAULT_RATES["rebounds"]),
        "assists":  (cor.assisting_per_min  if cor and cor.assisting_per_min  is not None else _DEFAULT_RATES["assists"]),
    }
    if cor is None:
        notes.append("RATES_DEFAULT: using league-average rates (no OpportunityState component_opportunity)")

    # -----------------------------------------------------------------------
    # Resolve minutes distribution
    # -----------------------------------------------------------------------
    md = opportunity_state.minutes_distribution
    if md is None:
        # Fallback: triangular distribution ~25 min typical starter
        md = MinutesDistribution(low=15.0, mode=25.0, high=35.0, confidence=0.40)
        notes.append("MINUTES_DEFAULT: no distribution in OpportunityState; using 15/25/35 fallback")

    # -----------------------------------------------------------------------
    # Blowout risk for regime sampling
    # -----------------------------------------------------------------------
    blowout_risk = opportunity_state.blowout_risk or 0.20
    regime_weights = {
        "normal":  max(0.0, 1.0 - blowout_risk - 0.15),
        "blowout": blowout_risk,
        "foul":    0.15,
    }
    # Normalize
    total_w = sum(regime_weights.values())
    regime_weights = {k: v / total_w for k, v in regime_weights.items()}

    # -----------------------------------------------------------------------
    # Run simulation
    # -----------------------------------------------------------------------
    composite_samples: list[float] = []
    pts_samples: list[float] = []
    reb_samples: list[float] = []
    ast_samples: list[float] = []
    regime_counts: dict[str, int] = {"normal": 0, "blowout": 0, "foul": 0}

    for _ in range(n_sims):
        # Shared minutes draw (triangular distribution)
        minutes = _triangular(rng, md.low, md.mode, md.high)
        minutes = max(0.0, minutes)

        # Regime draw
        regime = _sample_regime(rng, regime_weights)
        regime_counts[regime] += 1
        mults = _REGIME_MULTIPLIERS[regime]

        # Joint component simulation — all conditioned on shared minutes
        pts_raw = minutes * rates["points"]  * mults["points"]
        reb_raw = minutes * rates["rebounds"] * mults["rebounds"]
        ast_raw = minutes * rates["assists"]  * mults["assists"]

        # Add Poisson noise (overdispersed via negative binomial proxy)
        pts = _overdispersed_draw(rng, pts_raw)
        reb = _overdispersed_draw(rng, reb_raw)
        ast = _overdispersed_draw(rng, ast_raw)

        pts_samples.append(pts)
        reb_samples.append(reb)
        ast_samples.append(ast)

        # Compute composite
        composite = _compute_composite(family_key, pts, reb, ast)
        composite_samples.append(composite)

    # -----------------------------------------------------------------------
    # Aggregate
    # -----------------------------------------------------------------------
    n = len(composite_samples)
    p_more = sum(1 for v in composite_samples if v > line) / n
    p_less = sum(1 for v in composite_samples if v < line) / n
    p_push = 1.0 - p_more - p_less

    mean_composite = sum(composite_samples) / n
    mean_pts = sum(pts_samples) / n if "points"  in components else None
    mean_reb = sum(reb_samples) / n if "rebounds" in components else None
    mean_ast = sum(ast_samples) / n if "assists"  in components else None

    pearson_pts_reb = _pearson(pts_samples, reb_samples) if n > 1 else None
    pearson_pts_ast = _pearson(pts_samples, ast_samples) if n > 1 else None

    regime_dist = {k: round(v / n_sims, 4) for k, v in regime_counts.items()}

    return CompositeSimResult(
        prop_family=family_key,
        line=line,
        n_sims=n_sims,
        p_more=round(p_more, 4),
        p_less=round(p_less, 4),
        p_push=round(max(0.0, p_push), 4),
        mean_pts=round(mean_pts, 3) if mean_pts is not None else None,
        mean_reb=round(mean_reb, 3) if mean_reb is not None else None,
        mean_ast=round(mean_ast, 3) if mean_ast is not None else None,
        mean_composite=round(mean_composite, 3),
        pearson_r_pts_reb=round(pearson_pts_reb, 4) if pearson_pts_reb is not None else None,
        pearson_r_pts_ast=round(pearson_pts_ast, 4) if pearson_pts_ast is not None else None,
        regime_distribution=regime_dist,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _triangular(rng: random.Random, low: float, mode: float, high: float) -> float:
    """Sample from a triangular distribution."""
    if high <= low:
        return mode
    # Python's triangular: lower, upper, mode
    return rng.triangular(low, max(high, low + 0.01), max(low, min(high, mode)))


def _sample_regime(rng: random.Random, weights: dict[str, float]) -> str:
    keys   = list(weights.keys())
    probs  = [weights[k] for k in keys]
    cumulative = 0.0
    r = rng.random()
    for k, p in zip(keys, probs):
        cumulative += p
        if r <= cumulative:
            return k
    return keys[-1]


def _overdispersed_draw(rng: random.Random, mean: float) -> float:
    """
    Overdispersed count draw: Poisson-like with extra variance.
    Uses gamma-Poisson (negative binomial) approximation:
    shape = mean / overdispersion; overdispersion = 1.5
    """
    if mean <= 0:
        return 0.0
    overdispersion = 1.5
    shape = mean / overdispersion
    rate  = 1.0 / overdispersion
    # Gamma draw for the Poisson rate
    try:
        lambda_draw = rng.gammavariate(shape, 1.0 / rate)
    except Exception:
        lambda_draw = mean
    # Poisson draw (approximate with floor(gamma + noise))
    count = 0
    exp_lambda = math.exp(-lambda_draw)
    p = 1.0
    while p > exp_lambda:
        p *= rng.random()
        count += 1
    return float(max(0, count - 1))


def _compute_composite(family: str, pts: float, reb: float, ast: float) -> float:
    if family == "pra":
        return pts + reb + ast
    elif family == "p+r":
        return pts + reb
    elif family == "r+a":
        return reb + ast
    elif family == "p+a":
        return pts + ast
    elif family == "points":
        return pts
    elif family == "rebounds":
        return reb
    elif family == "assists":
        return ast
    return pts + reb + ast


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation coefficient."""
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num    = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den_x  = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    den_y  = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    if den_x < 1e-9 or den_y < 1e-9:
        return None
    return num / (den_x * den_y)
