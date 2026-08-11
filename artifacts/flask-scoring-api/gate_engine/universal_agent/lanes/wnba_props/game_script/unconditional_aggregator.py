"""
gate_engine/universal_agent/lanes/wnba_props/game_script/unconditional_aggregator.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-GAMESCRIPT

Weighted Unconditional Hit Probability Aggregator.

Computes:
  P(hit) = Σ_s  P(script=s) * P(hit | script=s)

where the sum is over all 5 game scripts. Scripts with unavailable
conditional probabilities are excluded from the weighted sum and their
prior weight is redistributed proportionally across available scripts.

Fail-closed: if NO scripts have available conditional probabilities,
returns UnconditionalResult with available=False and probability=None.

can_execute = False
"""
from __future__ import annotations

from dataclasses import dataclass

from gate_engine.universal_agent.lanes.wnba_props.game_script.game_environment import (
    ScriptPriors, ALL_SCRIPTS,
)
from gate_engine.universal_agent.lanes.wnba_props.game_script.conditional_hit_prob import (
    ConditionalHitResult,
)

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"


@dataclass(frozen=True)
class UnconditionalResult:
    """
    Weighted unconditional hit probability.

    available:          False if no scripts had available conditional probs.
    probability:        P(hit) ∈ [0, 1] or None.
    scripts_used:       Number of scripts contributing to the weighted sum.
    scripts_available:  Total scripts with available conditional probs.
    effective_priors:   Renormalized prior weights actually used.
    conditional_probs:  Per-script {script_id: P(hit|script)} (available only).
    stat_key:           Canonical stat key.
    line:               Prop line used in computation.
    """
    available:         bool
    probability:       float | None
    scripts_used:      int
    scripts_available: int
    effective_priors:  dict[str, float]
    conditional_probs: dict[str, float]
    stat_key:          str
    line:              float


def aggregate_unconditional_probability(
    priors:      ScriptPriors,
    conditionals: dict[str, ConditionalHitResult],
    stat_key:    str,
    line:        float,
) -> UnconditionalResult:
    """
    Compute P(hit) = Σ_s P(s) * P(hit|s) over available scripts.

    Unavailable scripts have their prior weight redistributed proportionally
    to the available scripts. If no scripts are available, returns
    UnconditionalResult with available=False.

    Parameters
    ----------
    priors        ScriptPriors from derive_script_priors().
    conditionals  {script_id: ConditionalHitResult} from compute_conditional_hit_probs().
    stat_key      Canonical stat key (for labelling).
    line          Prop line (for labelling).
    """
    prior_map = priors.as_dict()

    # Collect available (script, prior_weight, conditional_prob) triples
    available_scripts: list[tuple[str, float, float]] = []
    for script in ALL_SCRIPTS:
        cond = conditionals.get(script)
        if cond is not None and cond.available and cond.probability is not None:
            available_scripts.append((script, prior_map.get(script, 0.0), cond.probability))

    n_available = len(available_scripts)
    if n_available == 0:
        return UnconditionalResult(
            available=False,
            probability=None,
            scripts_used=0,
            scripts_available=0,
            effective_priors={},
            conditional_probs={},
            stat_key=stat_key,
            line=line,
        )

    # Renormalize prior weights over available scripts
    weight_sum = sum(w for _, w, _ in available_scripts)
    if weight_sum <= 0:
        # Equal weights fallback
        weight_sum = float(n_available)
        available_scripts = [(s, 1.0, p) for s, _, p in available_scripts]

    effective_priors: dict[str, float] = {}
    conditional_probs: dict[str, float] = {}
    weighted_sum = 0.0

    for script, raw_weight, cond_prob in available_scripts:
        eff_w = raw_weight / weight_sum
        effective_priors[script]  = round(eff_w, 8)
        conditional_probs[script] = cond_prob
        weighted_sum += eff_w * cond_prob

    probability = max(0.0, min(1.0, weighted_sum))

    return UnconditionalResult(
        available=True,
        probability=round(probability, 6),
        scripts_used=n_available,
        scripts_available=n_available,
        effective_priors=effective_priors,
        conditional_probs=conditional_probs,
        stat_key=stat_key,
        line=line,
    )
