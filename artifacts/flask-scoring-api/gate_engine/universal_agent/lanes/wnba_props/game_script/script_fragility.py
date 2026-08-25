"""
gate_engine/universal_agent/lanes/wnba_props/game_script/script_fragility.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-GAMESCRIPT

Script Dependency / Fragility Analysis.

Measures how sensitive the final unconditional hit probability is to
the realized game script. High fragility means the pick outcome depends
heavily on which script plays out — low fragility means the pick is
robust across game contexts.

Outputs
-------
  fragility_range        max(P|s) − min(P|s) across available scripts
  dominant_script        argmax P(s) * P(hit|s)  (highest weighted contribution)
  sensitivity_scores     {script: partial derivative approximation}
  fragility_label        LOW / MEDIUM / HIGH
  available              False if fewer than 2 scripts have data

Thresholds
----------
  fragility_range < 0.10  → LOW
  fragility_range < 0.25  → MEDIUM
  fragility_range ≥ 0.25  → HIGH

can_execute = False
"""
from __future__ import annotations

from dataclasses import dataclass

from gate_engine.universal_agent.lanes.wnba_props.game_script.unconditional_aggregator import (
    UnconditionalResult,
)
from gate_engine.universal_agent.lanes.wnba_props.game_script.game_environment import (
    ScriptPriors,
)

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

_THRESHOLD_LOW    = 0.10
_THRESHOLD_MEDIUM = 0.25


@dataclass(frozen=True)
class FragilityReport:
    """
    Script fragility / sensitivity report.

    available:          False if fewer than 2 scripts contributed.
    fragility_range:    max_conditional − min_conditional probability.
    fragility_label:    "LOW" | "MEDIUM" | "HIGH".
    dominant_script:    Script with highest weighted contribution to P(hit).
    min_script:         Script with lowest conditional P(hit|s).
    max_script:         Script with highest conditional P(hit|s).
    sensitivity_scores: {script: effective_prior × conditional_prob}.
    scripts_count:      Number of scripts contributing.
    """
    available:          bool
    fragility_range:    float | None
    fragility_label:    str | None
    dominant_script:    str | None
    min_script:         str | None
    max_script:         str | None
    sensitivity_scores: dict[str, float]
    scripts_count:      int


def compute_fragility(
    unconditional: UnconditionalResult,
    priors:        ScriptPriors,
) -> FragilityReport:
    """
    Compute script fragility from an UnconditionalResult.

    Parameters
    ----------
    unconditional   Output of aggregate_unconditional_probability().
    priors          ScriptPriors (used for sensitivity scores).
    """
    if not unconditional.available or len(unconditional.conditional_probs) < 2:
        return FragilityReport(
            available=False,
            fragility_range=None,
            fragility_label=None,
            dominant_script=None,
            min_script=None,
            max_script=None,
            sensitivity_scores={},
            scripts_count=len(unconditional.conditional_probs),
        )

    cond_probs     = unconditional.conditional_probs  # {script: prob}
    eff_priors     = unconditional.effective_priors   # {script: weight}

    min_script = min(cond_probs, key=lambda s: cond_probs[s])
    max_script = max(cond_probs, key=lambda s: cond_probs[s])
    frag_range = cond_probs[max_script] - cond_probs[min_script]

    # Sensitivity = effective_prior × conditional_prob (contribution weight)
    sensitivity: dict[str, float] = {
        s: round(eff_priors.get(s, 0.0) * cond_probs[s], 6)
        for s in cond_probs
    }
    dominant_script = max(sensitivity, key=lambda s: sensitivity[s])

    if frag_range < _THRESHOLD_LOW:
        label = "LOW"
    elif frag_range < _THRESHOLD_MEDIUM:
        label = "MEDIUM"
    else:
        label = "HIGH"

    return FragilityReport(
        available=True,
        fragility_range=round(frag_range, 6),
        fragility_label=label,
        dominant_script=dominant_script,
        min_script=min_script,
        max_script=max_script,
        sensitivity_scores=sensitivity,
        scripts_count=len(cond_probs),
    )
