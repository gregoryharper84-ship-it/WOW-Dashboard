"""
gate_engine/universal_agent/model_validation/drift_monitor.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-MODELVAL

Model / Data Drift Monitor.

Computes feature drift and prediction drift between a reference distribution
(training snapshot) and a current distribution (recent inference).

Uses Jensen-Shannon divergence (symmetric, bounded [0, 1]) in pure Python.
No scipy / numpy required.

Drift status thresholds:
  JS < 0.05  → NOMINAL
  JS < 0.15  → WARNING
  JS ≥ 0.15  → ALERT

Outputs are advisory only. No automatic retraining. No model switching.
can_execute = False
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"

_THRESHOLD_WARNING = 0.05
_THRESHOLD_ALERT   = 0.15


class DriftStatus:
    NOMINAL  = "NOMINAL"
    WARNING  = "WARNING"
    ALERT    = "ALERT"
    UNKNOWN  = "UNKNOWN"    # insufficient data


@dataclass(frozen=True)
class FeatureDriftReport:
    """Drift report for one feature."""
    feature_name: str
    js_divergence: float | None
    status:        str
    ref_n:         int
    cur_n:         int


@dataclass(frozen=True)
class DriftSummary:
    """Aggregated drift summary across all monitored features."""
    model_id:       str
    feature_reports: list[FeatureDriftReport]
    max_js:         float | None
    overall_status: str
    alert_features: list[str]
    warning_features: list[str]


class DriftMonitor:
    """
    Advisory-only drift monitor. No automatic retraining or rollback.
    can_execute = False.
    """

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self._reference: dict[str, list[float]] = {}   # feature -> values at training
        self._current:   dict[str, list[float]] = {}   # feature -> recent inference values

    def add_reference_values(self, feature: str, values: list[float]) -> None:
        """Add training-time distribution samples for a feature."""
        self._reference.setdefault(feature, []).extend(values)

    def add_current_values(self, feature: str, values: list[float]) -> None:
        """Add inference-time distribution samples for a feature."""
        self._current.setdefault(feature, []).extend(values)

    def compute_drift(self, features: list[str] | None = None) -> DriftSummary:
        """
        Compute drift for each monitored feature.

        Parameters
        ----------
        features   Subset of feature names to check. None = all monitored features.
        """
        keys = features if features is not None else list(
            set(self._reference) | set(self._current)
        )

        reports: list[FeatureDriftReport] = []
        for feat in sorted(keys):
            ref = self._reference.get(feat, [])
            cur = self._current.get(feat, [])
            if len(ref) < 2 or len(cur) < 2:
                reports.append(FeatureDriftReport(
                    feature_name=feat,
                    js_divergence=None,
                    status=DriftStatus.UNKNOWN,
                    ref_n=len(ref),
                    cur_n=len(cur),
                ))
                continue

            js = _js_divergence_from_samples(ref, cur)
            status = _js_to_status(js)
            reports.append(FeatureDriftReport(
                feature_name=feat,
                js_divergence=round(js, 6),
                status=status,
                ref_n=len(ref),
                cur_n=len(cur),
            ))

        available_js = [r.js_divergence for r in reports if r.js_divergence is not None]
        max_js = max(available_js) if available_js else None
        overall = _js_to_status(max_js) if max_js is not None else DriftStatus.UNKNOWN

        return DriftSummary(
            model_id=self.model_id,
            feature_reports=reports,
            max_js=round(max_js, 6) if max_js is not None else None,
            overall_status=overall,
            alert_features=[r.feature_name for r in reports if r.status == DriftStatus.ALERT],
            warning_features=[r.feature_name for r in reports if r.status == DriftStatus.WARNING],
        )


# ── Pure-Python JS divergence helpers ────────────────────────────────────────

def _histogram_shared(
    values: list[float], lo: float, hi: float, n_bins: int = 20
) -> list[float]:
    """
    Build a normalised histogram for `values` using a shared [lo, hi] range.
    Using a shared range is essential so that two non-overlapping distributions
    land in different bins rather than each normalising to its own range.
    """
    if lo == hi:
        # All values identical across both samples → point mass at bin 0
        counts = [0.0] * n_bins
        counts[0] = 1.0
        return counts
    bin_width = (hi - lo) / n_bins
    counts = [0] * n_bins
    for v in values:
        idx = min(int((v - lo) / bin_width), n_bins - 1)
        counts[idx] += 1
    total = sum(counts)
    return [c / total for c in counts] if total > 0 else [1.0 / n_bins] * n_bins


def _kl_divergence(p: list[float], q: list[float], eps: float = 1e-10) -> float:
    """KL(P||Q) with epsilon smoothing. Assumes equal-length distributions."""
    return sum(
        pi * math.log((pi + eps) / (qi + eps))
        for pi, qi in zip(p, q)
        if pi > 0
    )


def _js_divergence_from_samples(ref: list[float], cur: list[float]) -> float:
    """
    Jensen-Shannon divergence between two sample sets.
    Histograms share a common [min, max] range derived from BOTH distributions
    so that non-overlapping distributions produce JS > 0.
    Returns JS ∈ [0, 1].
    """
    n_bins = 20
    all_vals = ref + cur
    lo = min(all_vals)
    hi = max(all_vals)

    p = _histogram_shared(ref, lo, hi, n_bins)
    q = _histogram_shared(cur, lo, hi, n_bins)

    m = [(pi + qi) / 2.0 for pi, qi in zip(p, q)]
    js = 0.5 * _kl_divergence(p, m) + 0.5 * _kl_divergence(q, m)
    return max(0.0, min(1.0, js))


def _js_to_status(js: float | None) -> str:
    if js is None:
        return DriftStatus.UNKNOWN
    if js < _THRESHOLD_WARNING:
        return DriftStatus.NOMINAL
    if js < _THRESHOLD_ALERT:
        return DriftStatus.WARNING
    return DriftStatus.ALERT
