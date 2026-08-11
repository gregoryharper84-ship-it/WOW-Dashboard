"""
gate_engine/universal_agent/model_validation/walk_forward.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4-MODELVAL

Walk-Forward Historical Replay Engine.

Replays FeatureSnapshots in chronological order (by as_of_date), applying a
caller-supplied scoring function at each time step. Produces a ReplayResult
with per-step predictions and summary statistics.

Strict point-in-time constraint: the scoring function receives ONLY the
features from the snapshot at or before the current time step. No future
data leaks forward. The engine itself enforces this by filtering.

The engine is read-only (never modifies snapshots) and deterministic
(given the same snapshots and scoring function, produces the same output).

can_execute = False
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from gate_engine.universal_agent.model_validation.feature_store import FeatureSnapshot

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"


@dataclass(frozen=True)
class ReplayStep:
    """One time-step in a walk-forward replay."""
    step_index:  int
    as_of_date:  str
    snapshot_id: str
    prediction:  float | None   # output of scoring_fn, or None on error
    error:       str | None     # set if scoring_fn raised


@dataclass(frozen=True)
class ReplayResult:
    """
    Full walk-forward replay output.

    steps:           Per-step predictions in chronological order.
    n_steps:         Total steps attempted.
    n_successful:    Steps where scoring_fn succeeded.
    n_failed:        Steps where scoring_fn raised.
    predictions:     List of float predictions (successful steps only).
    mean_prediction: Mean of successful predictions, or None.
    model_id:        Passed through for provenance.
    """
    steps:           list[ReplayStep]
    n_steps:         int
    n_successful:    int
    n_failed:        int
    predictions:     list[float]
    mean_prediction: float | None
    model_id:        str


class WalkForwardReplayEngine:
    """
    Read-only walk-forward replay engine.
    can_execute = False — never modifies snapshots or production state.
    """

    def replay(
        self,
        *,
        model_id:      str,
        snapshots:     list[FeatureSnapshot],
        scoring_fn:    Callable[[dict[str, Any]], float | None],
        min_date:      str | None = None,
        max_date:      str | None = None,
    ) -> ReplayResult:
        """
        Run walk-forward replay over a list of FeatureSnapshots.

        Parameters
        ----------
        model_id    Provenance label for this replay run.
        snapshots   List of FeatureSnapshot (need not be pre-sorted).
        scoring_fn  Callable(features_dict) -> float | None.
                    Must be pure / stateless. May return None on insufficient data.
                    Any exception is caught; that step is marked as failed.
        min_date    YYYY-MM-DD lower bound (inclusive). None = no lower bound.
        max_date    YYYY-MM-DD upper bound (inclusive). None = no upper bound.

        Returns ReplayResult.
        """
        # Sort chronologically
        ordered = sorted(snapshots, key=lambda s: s.as_of_date)

        # Apply date filters
        if min_date:
            ordered = [s for s in ordered if s.as_of_date >= min_date]
        if max_date:
            ordered = [s for s in ordered if s.as_of_date <= max_date]

        steps: list[ReplayStep] = []
        predictions: list[float] = []
        n_failed = 0

        for i, snap in enumerate(ordered):
            try:
                pred = scoring_fn(snap.features)
                step = ReplayStep(
                    step_index=i,
                    as_of_date=snap.as_of_date,
                    snapshot_id=snap.snapshot_id,
                    prediction=pred,
                    error=None,
                )
                if pred is not None:
                    predictions.append(pred)
            except Exception as exc:
                step = ReplayStep(
                    step_index=i,
                    as_of_date=snap.as_of_date,
                    snapshot_id=snap.snapshot_id,
                    prediction=None,
                    error=f"{type(exc).__name__}: {exc}",
                )
                n_failed += 1

            steps.append(step)

        n_successful = len(steps) - n_failed
        mean_pred = (sum(predictions) / len(predictions)) if predictions else None

        return ReplayResult(
            steps=steps,
            n_steps=len(steps),
            n_successful=n_successful,
            n_failed=n_failed,
            predictions=predictions,
            mean_prediction=round(mean_pred, 6) if mean_pred is not None else None,
            model_id=model_id,
        )
