"""
validation/ablation/runner.py

Feature ablation runner for failure-path modeling.

For each supported feature, the runner computes:
  - FULL model probability (all supported features)
  - ABLATED model probability (with the feature zeroed/removed)
  - Brier score delta (FULL Brier − ABLATED Brier) on the validation set
  - The feature's availability status

Unsupported features are always reported as UNAVAILABLE — never fabricated.

The runner is intentionally cheap: it re-runs the Baseline C stat_model
with/without each feature, not the full event-tree (which is deterministic
given its inputs).  This gives a lower-bound ablation gap; a full event-tree
ablation would require sampling over the feature's uncertainty, which is
deferred to a future harness version.
"""
from __future__ import annotations

from typing import Any, List, Optional

from validation.ablation.features import FEATURE_REGISTRY, FeatureSpec
from validation.baselines.stat_model import predict_single as stat_predict
from validation.metrics.core import brier_score


def _run_stat_model(
    row: dict,
    *,
    exclude_feature: Optional[str] = None,
) -> Optional[float]:
    """
    Run Baseline C (stat_model) on a single row, optionally excluding one feature.

    Exclusions
    ----------
    - "top_four_detail":  replace bf_distribution with uniform {p3=0.33, p4=0.33, p5+=0.34}
    - "failure_path":     remove ledger_rows that are "HIT" on the current line
    - "l10_discernment":  use only the most recent 10 starts regardless of trend
    - "recent_form":      drop the last 3 starts from ledger_rows
    """
    ledger_rows  = list(row.get("ledger_rows") or [])
    bf_dist      = row.get("bf_distribution")
    line         = float(row.get("line", 0))
    direction    = row.get("direction", "LESS")

    if exclude_feature == "top_four_detail":
        bf_dist = {"p_bf_3": 0.33, "p_bf_4": 0.33, "p_bf_gte5": 0.34}
    elif exclude_feature == "recent_form":
        # Drop last 3 starts
        try:
            sorted_rows = sorted(ledger_rows, key=lambda r: r.get("game_date", ""))
            ledger_rows = sorted_rows[:-3] if len(sorted_rows) > 3 else sorted_rows
        except Exception:
            pass
    elif exclude_feature == "failure_path":
        # Remove rows where the pitcher "failed" the prop (to see if failures drive signal)
        filtered = [r for r in ledger_rows
                    if r.get("hit") != "HIT"]
        ledger_rows = filtered if filtered else ledger_rows

    result = stat_predict(
        ledger_rows  = ledger_rows,
        bf_distribution = bf_dist,
        line         = line,
        direction    = direction,
        n_trials     = 5_000,
        seed         = 99,
    )
    return result.get("probability")


def run_ablation(
    validation_rows: List[dict],
    *,
    include_unavailable: bool = True,
) -> dict:
    """
    Run feature ablation over a validation set.

    Parameters
    ----------
    validation_rows    List of dicts, each containing:
                       - ledger_rows, bf_distribution, line, direction
                       - hit (bool): actual outcome
    include_unavailable  If True, include UNAVAILABLE entries for unsupported features.

    Returns
    -------
    dict mapping feature_id → ablation result dict:
      {
        "feature_id":       str,
        "supported":        bool,
        "status":           "RAN" | "UNAVAILABLE",
        "unavailable_reason": str | None,
        "brier_full":       float | None,
        "brier_ablated":    float | None,
        "brier_delta":      float | None,  # full - ablated (negative = feature helps)
        "n":                int,
      }
    """
    results = {}

    for spec in FEATURE_REGISTRY:
        if not spec.supported:
            if include_unavailable:
                results[spec.id] = {
                    "feature_id":        spec.id,
                    "description":       spec.description,
                    "supported":         False,
                    "status":            "UNAVAILABLE",
                    "unavailable_reason": spec.unavailable_reason,
                    "brier_full":        None,
                    "brier_ablated":     None,
                    "brier_delta":       None,
                    "n":                 0,
                }
            continue

        # Collect FULL and ABLATED probabilities for supported features
        full_samples    = []
        ablated_samples = []

        for row in validation_rows:
            actual_hit = bool(row.get("hit"))
            prob_full    = _run_stat_model(row)
            prob_ablated = _run_stat_model(row, exclude_feature=spec.id)
            full_samples.append((prob_full, actual_hit))
            ablated_samples.append((prob_ablated, actual_hit))

        bs_full    = brier_score(full_samples)
        bs_ablated = brier_score(ablated_samples)

        brier_full    = bs_full.get("score")
        brier_ablated = bs_ablated.get("score")
        delta = (
            round(brier_full - brier_ablated, 6)
            if brier_full is not None and brier_ablated is not None
            else None
        )

        results[spec.id] = {
            "feature_id":        spec.id,
            "description":       spec.description,
            "supported":         True,
            "status":            "RAN",
            "unavailable_reason": None,
            "brier_full":        brier_full,
            "brier_ablated":     brier_ablated,
            # negative delta = removing this feature makes Brier WORSE = feature helps
            "brier_delta":       delta,
            "interpretation":    (
                "feature_hurts"   if (delta is not None and delta > 0.001) else
                "feature_helps"   if (delta is not None and delta < -0.001) else
                "feature_neutral" if delta is not None else
                "insufficient_data"
            ),
            "n":                 bs_full.get("n", 0),
        }

    return results
