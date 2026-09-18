"""Challenger replay for WOW-MODEL-NFL-PLATT-PENALTY-HYPOTHESIS-009.

Research evidence only. Not imported by production. can_execute = false.

HYPOTHESIS
    nfl_event_model_v17.fit_candidate builds the Platt calibrator as
        LogisticRegression(solver="lbfgs", max_iter=2000, random_state=17, penalty=None)
    `penalty` is deprecated in sklearn 1.8 and removed in 1.10. The proposed
    CHALLENGER replaces penalty=None with C=np.inf, which expresses the same
    unregularized objective through the surviving kwarg.

WHAT THIS REPLAYS
    The real CHAMPION artifact's own base logits on the real governed 2024
    calibration cohort and the untouched real 2025 validation cohort (see
    replay_dataset.py). Champion and challenger Platt calibrators are fitted on
    identical inputs and compared on parameters, holdout metrics, every
    certification-check the fit enforces, and per-game published probability and
    lower bound.

WHAT THIS DOES NOT DO
    It does not promote anything, register an artifact, or change production.
    Class C promotion is the terminal reducer's decision under governed review.
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nfl_event_model_v17 import _ece, _sigmoid  # production metric code, reused verbatim
from replay_dataset import (
    CALIBRATION_2024,
    CHAMPION_PLATT_A,
    CHAMPION_PLATT_B,
    CHAMPION_UNCERTAINTY_MARGIN,
    CHAMPION_VALIDATION_METRICS,
    VALIDATION_2025,
)

# Thresholds WOW gates read a calibrated lower bound against. A counterexample
# is any game where champion and challenger land on opposite sides of one.
LOWER_BOUND_THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70)


def _arrays(pairs):
    y = np.asarray([p[0] for p in pairs], dtype=int)
    logits = np.asarray([p[1] for p in pairs], dtype=float)
    return y, logits


def fit_platt(cal_logits, y_cal, *, variant):
    """Fit the Platt stage exactly as fit_candidate does, per variant."""
    x = cal_logits.reshape(-1, 1)
    kwargs = dict(solver="lbfgs", max_iter=2000, random_state=17)
    if variant == "champion":
        kwargs["penalty"] = None
    elif variant == "challenger":
        kwargs["C"] = np.inf
    else:
        kwargs["C"] = float(variant)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model = LogisticRegression(**kwargs).fit(x, y_cal)
    warning_types = sorted({w.category.__name__ for w in caught})
    return float(model.coef_[0, 0]), float(model.intercept_[0]), warning_types


def evaluate(platt_a, platt_b, y_val, val_logits, y_cal):
    calibrated = _sigmoid(platt_a * val_logits + platt_b)
    raw = _sigmoid(val_logits)
    baseline_p = float(y_cal.mean())
    baseline = np.full(len(y_val), baseline_p, dtype=float)
    brier = float(brier_score_loss(y_val, calibrated))
    ll = float(log_loss(y_val, np.column_stack([1.0 - calibrated, calibrated]), labels=[0, 1]))
    auc = float(roc_auc_score(y_val, calibrated))
    ece = _ece(y_val, calibrated)
    baseline_brier = float(brier_score_loss(y_val, baseline))
    baseline_ll = float(log_loss(y_val, np.column_stack([1.0 - baseline, baseline]), labels=[0, 1]))
    margin = float(max(0.015, min(0.08, 1.96 * np.sqrt(max(brier, 1e-9) / len(y_val)))))
    return {
        "platt_a": platt_a,
        "platt_b": platt_b,
        "validation_brier_score": brier,
        "validation_log_loss": ll,
        "validation_auc": auc,
        "validation_ece_10": ece,
        "raw_validation_brier_score": float(brier_score_loss(y_val, raw)),
        "baseline_probability": baseline_p,
        "baseline_brier_score": baseline_brier,
        "baseline_log_loss": baseline_ll,
        "uncertainty_margin": margin,
        "certification_checks": {
            "beats_baseline_brier": bool(brier < baseline_brier),
            "beats_baseline_log_loss": bool(ll < baseline_ll),
            "auc_not_below_chance": bool(auc >= 0.50),
            "ece_within_limit": bool(ece <= 0.12),
            "platt_positive_slope": bool(platt_a > 0.0),
        },
        "_calibrated": calibrated,
    }


def counterexample_review(champ, chal, logits, margin_champ, margin_chal, label):
    p_champ = _sigmoid(champ["platt_a"] * logits + champ["platt_b"])
    p_chal = _sigmoid(chal["platt_a"] * logits + chal["platt_b"])
    lower_champ = np.maximum(0.001, p_champ - margin_champ)
    lower_chal = np.maximum(0.001, p_chal - margin_chal)
    flips = {}
    for t in LOWER_BOUND_THRESHOLDS:
        flips[f"lower_bound_crosses_{t:.2f}"] = int(np.sum((lower_champ >= t) != (lower_chal >= t)))
    worst = int(np.argmax(np.abs(p_champ - p_chal)))
    return {
        "cohort": label,
        "n": int(len(logits)),
        "max_abs_probability_delta": float(np.max(np.abs(p_champ - p_chal))),
        "mean_abs_probability_delta": float(np.mean(np.abs(p_champ - p_chal))),
        "max_abs_lower_bound_delta": float(np.max(np.abs(lower_champ - lower_chal))),
        "worst_case_game": {
            "index": worst,
            "base_logit": float(logits[worst]),
            "champion_probability": float(p_champ[worst]),
            "challenger_probability": float(p_chal[worst]),
        },
        "favourite_side_flips": int(np.sum((p_champ >= 0.5) != (p_chal >= 0.5))),
        "lower_bound_threshold_flips": flips,
    }


def bootstrap_stress(y_cal, cal_logits, y_val, val_logits, *, draws=200, seed=17):
    """Worst-case search: refit both variants on real-cohort resamples."""
    rng = np.random.default_rng(seed)
    worst = {"max_abs_param_delta": 0.0, "max_abs_probability_delta": 0.0, "failures": []}
    for _ in range(draws):
        idx = rng.integers(0, len(y_cal), len(y_cal))
        yb, lb = y_cal[idx], cal_logits[idx]
        if len(np.unique(yb)) < 2:
            continue
        try:
            a_c, b_c, _ = fit_platt(lb, yb, variant="champion")
            a_x, b_x, _ = fit_platt(lb, yb, variant="challenger")
        except Exception as exc:  # a divergence is itself evidence
            worst["failures"].append(type(exc).__name__)
            continue
        worst["max_abs_param_delta"] = max(
            worst["max_abs_param_delta"], abs(a_c - a_x), abs(b_c - b_x)
        )
        delta = np.max(np.abs(
            _sigmoid(a_c * val_logits + b_c) - _sigmoid(a_x * val_logits + b_x)
        ))
        worst["max_abs_probability_delta"] = max(worst["max_abs_probability_delta"], float(delta))
    worst["draws"] = draws
    return worst


def separability_probe():
    """Regression probe: the one regime where unregularized fits misbehave.

    penalty=None and C=inf are the same objective, so if separable calibration
    data breaks one it breaks the other. This records that they fail (or drift)
    together rather than the challenger introducing a new failure mode.
    """
    logits = np.array([-3.0, -2.0, -1.0, 1.0, 2.0, 3.0])
    y = np.array([0, 0, 0, 1, 1, 1])
    out = {}
    for variant in ("champion", "challenger"):
        a, b, warns = fit_platt(logits, y, variant=variant)
        out[variant] = {"platt_a": a, "platt_b": b, "warnings": warns}
    out["max_abs_param_delta"] = max(
        abs(out["champion"]["platt_a"] - out["challenger"]["platt_a"]),
        abs(out["champion"]["platt_b"] - out["challenger"]["platt_b"]),
    )
    return out


def main():
    y_cal, cal_logits = _arrays(CALIBRATION_2024)
    y_val, val_logits = _arrays(VALIDATION_2025)

    a_champ, b_champ, warn_champ = fit_platt(cal_logits, y_cal, variant="champion")
    a_chal, b_chal, warn_chal = fit_platt(cal_logits, y_cal, variant="challenger")

    champ = evaluate(a_champ, b_champ, y_val, val_logits, y_cal)
    chal = evaluate(a_chal, b_chal, y_val, val_logits, y_cal)

    report = {
        "ticket": "WOW-MODEL-NFL-PLATT-PENALTY-HYPOTHESIS-009",
        "change_class": "C",
        "can_execute": False,
        "probability_publishable": False,
        "sklearn_version": __import__("sklearn").__version__,
        "artifact_reproduction": {
            "registered_platt_a": CHAMPION_PLATT_A,
            "registered_platt_b": CHAMPION_PLATT_B,
            "replayed_platt_a": a_champ,
            "replayed_platt_b": b_champ,
            "abs_delta_a": abs(CHAMPION_PLATT_A - a_champ),
            "abs_delta_b": abs(CHAMPION_PLATT_B - b_champ),
        },
        "champion": {k: v for k, v in champ.items() if not k.startswith("_")},
        "challenger": {k: v for k, v in chal.items() if not k.startswith("_")},
        "champion_warnings": warn_champ,
        "challenger_warnings": warn_chal,
        "parameter_deltas": {
            "abs_delta_platt_a": abs(a_champ - a_chal),
            "abs_delta_platt_b": abs(b_champ - b_chal),
        },
        "metric_deltas": {
            key: chal[key] - champ[key]
            for key in (
                "validation_brier_score",
                "validation_log_loss",
                "validation_auc",
                "validation_ece_10",
                "uncertainty_margin",
            )
        },
        "certification_checks_identical": champ["certification_checks"] == chal["certification_checks"],
        "certification_checks_all_pass": all(chal["certification_checks"].values()),
        "counterexample_review": [
            counterexample_review(champ, chal, val_logits,
                                  champ["uncertainty_margin"], chal["uncertainty_margin"],
                                  "validation_2025"),
            counterexample_review(champ, chal, cal_logits,
                                  CHAMPION_UNCERTAINTY_MARGIN, CHAMPION_UNCERTAINTY_MARGIN,
                                  "calibration_2024"),
        ],
        "alternative_finite_C": {
            f"C={c:g}": dict(zip(("platt_a", "platt_b"), fit_platt(cal_logits, y_cal, variant=c)[:2]))
            for c in (1e6, 1e9, 1e12)
        },
        "bootstrap_stress": bootstrap_stress(y_cal, cal_logits, y_val, val_logits),
        "separability_probe": separability_probe(),
        "registered_artifact_metrics": CHAMPION_VALIDATION_METRICS,
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
