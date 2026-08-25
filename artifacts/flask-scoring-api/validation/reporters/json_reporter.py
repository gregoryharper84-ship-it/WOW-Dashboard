"""
validation/reporters/json_reporter.py

Emit machine-readable JSON comparison report for the validation harness.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from validation import HARNESS_VERSION, FROZEN_COMMIT


def build_report(
    *,
    split_manifest: dict,
    model_results:    dict,          # {"train": eval_dict, "validation": eval_dict, "holdout": eval_dict | None}
    baseline_results: dict,          # {baseline_id: {"train": ..., "validation": ..., "holdout": ...}}
    ablation_results: dict,          # {feature_id: ablation_entry}
    eval_rules: dict,                # loaded from eval_rules.yaml
    holdout_evaluated: bool = False, # must be False during tuning
    notes: str = "",
) -> dict:
    """
    Build a fully structured JSON report.

    The report includes:
    - Metadata (harness version, frozen commit, timestamp)
    - Split manifest
    - Evaluation rules used (predeclared)
    - Per-split metrics for each model and baseline
    - Baseline comparison table
    - Ablation table
    - Gate verdicts (pass/warn/fail per threshold)
    - Limitations section
    """
    now = datetime.now(timezone.utc).isoformat()

    # ── Gate verdicts ─────────────────────────────────────────────────────
    def _verdict(score: Optional[float], warn: float, fail: float, lower_is_better: bool) -> str:
        if score is None:
            return "INSUFFICIENT_DATA"
        if lower_is_better:
            if score <= warn:   return "PASS"
            if score <= fail:   return "WARN"
            return "FAIL"
        else:
            if score >= warn:   return "PASS"
            if score >= fail:   return "WARN"
            return "FAIL"

    primary_cfg   = eval_rules.get("primary_threshold", {})
    secondary_cfg = eval_rules.get("secondary_threshold", {})

    val_brier  = (model_results.get("validation") or {}).get("brier", {}).get("score")
    val_logloss = (model_results.get("validation") or {}).get("log_loss", {}).get("score")
    ece_val    = (model_results.get("validation") or {}).get("calibration", {}).get("ece")

    verdicts = {
        "primary_brier": {
            "split":     "validation",
            "score":     val_brier,
            "threshold": primary_cfg,
            "verdict":   _verdict(val_brier,
                                  primary_cfg.get("warn_above", 0.25),
                                  primary_cfg.get("fail_above", 0.30),
                                  True),
        },
        "secondary_log_loss": {
            "split":     "validation",
            "score":     val_logloss,
            "threshold": secondary_cfg,
            "verdict":   _verdict(val_logloss,
                                  secondary_cfg.get("warn_above", 0.693),
                                  secondary_cfg.get("fail_above", 0.750),
                                  True),
        },
        "calibration_ece": {
            "split":   "validation",
            "ece":     ece_val,
            "max_ece": (eval_rules.get("calibration") or {}).get("max_calibration_error", 0.15),
            "verdict": _verdict(ece_val,
                                (eval_rules.get("calibration") or {}).get("max_calibration_error", 0.15),
                                (eval_rules.get("calibration") or {}).get("max_calibration_error", 0.15),
                                True) if ece_val is not None else "INSUFFICIENT_DATA",
        },
    }

    # ── Baseline comparison ───────────────────────────────────────────────
    baseline_comparison = []
    for bid, splits in (baseline_results or {}).items():
        val_b = (splits.get("validation") or {}).get("brier", {}).get("score")
        baseline_comparison.append({
            "baseline_id": bid,
            "brier_validation": val_b,
            "beats_model": (
                (val_b > val_brier)
                if (val_b is not None and val_brier is not None)
                else None
            ),
        })

    return {
        "report_type":       "WOW_VALIDATION_HARNESS_REPORT",
        "harness_version":   HARNESS_VERSION,
        "frozen_commit":     FROZEN_COMMIT,
        "generated_at":      now,
        "holdout_evaluated": holdout_evaluated,
        "notes":             notes,
        "eval_rules":        eval_rules,
        "split_manifest":    split_manifest,
        "verdicts":          verdicts,
        "model_results":     model_results,
        "baseline_results":  baseline_results,
        "baseline_comparison": baseline_comparison,
        "ablation":          ablation_results,
        "limitations": [
            "Historical Savant data may have gaps for recently promoted pitchers",
            "ppb genre defaults (mean=4.2, std=1.1) not yet validated against 2024 Statcast population",
            "Market prior (no-vig implied prob) unavailable historically — cannot assess calibration vs. market",
            "Holdout set not yet evaluated (holdout_evaluated=False); do not use for decisions",
            "Ablation uses Baseline C (stat_model) not full event-tree — gap is a lower bound",
            "Handedness, health/workload, catcher, weather, opponent adjustments all UNAVAILABLE",
        ],
    }


def write_json_report(report: dict, path: str) -> str:
    """Write report to path and return the path."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    return path
