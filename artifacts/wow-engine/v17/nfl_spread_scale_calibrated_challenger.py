"""Research-only NFL spread V3 sporting residual-scale challenger.

V2 fits a leakage-safe sporting margin center from 2021-2023 and freezes 2024
for calibration, 2025 for terminal validation, and 2026 for forward reserve.
This challenger changes only one calibration degree of freedom: a global scale
multiplier on the SPORTING residual distribution. The 2024 calibration season is
split chronologically: an early segment freezes residual samples and a later,
disjoint segment selects scale by margin CRPS. Sportsbook lines are not used to
fit the margin center, residual distribution, or residual scale. Exact lines are
bound only after the V3 sporting distribution is frozen and are used solely for
the untouched 2025 exact-line evaluation.

This is Class-C research only. It can never certify, promote, publish, rank, or
execute a wager.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from tempfile import TemporaryDirectory
from typing import Any, Sequence

import numpy as np

from nfl_event_data_p1 import download_asset, schedules_asset
from v17.nfl_spread_context_challenger import (
    RIDGE_ALPHA,
    _partition,
    build_margin_rows,
    train_candidate,
)
from v17.spread_certification_replay import (
    _read_csv,
    _source_manifest,
    bind_nflverse_close_proxies,
)
from v17.spread_historical_close_proxy import (
    NFLVERSE_EVIDENCE_CLASS,
    evaluate_historical_close_proxy,
)
from v17.spread_margin_challenger import (
    MarginDistributionArtifact,
    MarginTrainingRow,
    SpreadChallengerUnavailable,
    predict_margin_center,
)
from v17.team_state_challenger_maintenance import _nfl_events

CAN_EXECUTE = False
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True
GLOBAL_TERMINAL_REDUCER = "V17_TERMINAL_REDUCER"
AUTOMATIC_CERTIFICATION = False
AUTOMATIC_PROMOTION = False
PROBABILITY_PUBLISHABLE = False
RANK_ELIGIBLE = False
MODEL_PROGRAM = "NFL_SPREAD_CONTEXT_MARGIN_SCALE_CAL_V3"
MODEL_FAMILY = "NFL_SPREAD_CONTEXT_RIDGE_SCALE_CALIBRATED_V3"
FEATURE_SCHEMA_VERSION = "NFL_SPREAD_CONTEXT_FEATURES_V2"
CALIBRATION_METHOD = "SPORTING_MARGIN_CRPS_RESIDUAL_SCALE_2024_CHRONO_SPLIT"
SCALE_GRID = (0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0)
CALIBRATION_FIT_FRACTION = 0.70
MIN_RESIDUAL_FIT_N = 150
MIN_SCALE_TUNE_N = 60


def _hash(payload: Any) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _scaled_artifact(
    artifact: MarginDistributionArtifact,
    scale: float,
) -> MarginDistributionArtifact:
    """Scale sporting residual dispersion around its frozen mean."""
    value = float(scale)
    if not np.isfinite(value) or value <= 0.0:
        raise SpreadChallengerUnavailable(
            "NFL_SPREAD_V3_RESIDUAL_SCALE_INVALID",
            f"residual scale must be positive and finite, got {scale!r}",
        )
    residuals = np.asarray(artifact.calibration_residuals, dtype=float)
    if len(residuals) < 2 or not np.isfinite(residuals).all():
        raise SpreadChallengerUnavailable(
            "NFL_SPREAD_V3_RESIDUALS_INVALID",
            "V3 residual distribution is unavailable or non-finite",
        )
    mean = float(np.mean(residuals))
    scaled = tuple(float(mean + value * (residual - mean)) for residual in residuals)
    return replace(
        artifact,
        model_family=MODEL_FAMILY,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        calibration_residuals=scaled,
        training_dataset_hash=_hash({
            "base_training_dataset_hash": artifact.training_dataset_hash,
            "calibration_method": CALIBRATION_METHOD,
            "residual_scale": value,
            "residual_sample_n": len(residuals),
        }),
    )


def _calibration_split(
    rows: Sequence[MarginTrainingRow],
) -> tuple[list[MarginTrainingRow], list[MarginTrainingRow]]:
    ordered = sorted(rows, key=lambda row: (row.event_start_time, row.event_id))
    if len(ordered) < MIN_RESIDUAL_FIT_N + MIN_SCALE_TUNE_N:
        raise SpreadChallengerUnavailable(
            "NFL_SPREAD_V3_CALIBRATION_SPLIT_INSUFFICIENT",
            f"need >= {MIN_RESIDUAL_FIT_N + MIN_SCALE_TUNE_N} 2024 rows, got {len(ordered)}",
        )
    split = int(np.floor(len(ordered) * CALIBRATION_FIT_FRACTION))
    split = max(MIN_RESIDUAL_FIT_N, min(split, len(ordered) - MIN_SCALE_TUNE_N))
    fit_rows = ordered[:split]
    tune_rows = ordered[split:]
    if fit_rows[-1].event_start_time >= tune_rows[0].event_start_time:
        # Same kickoff timestamp can straddle the index. Move the whole timestamp
        # into the tuning side so no event-time block contributes to both roles.
        boundary = tune_rows[0].event_start_time
        fit_rows = [row for row in ordered if row.event_start_time < boundary]
        tune_rows = [row for row in ordered if row.event_start_time >= boundary]
    if len(fit_rows) < MIN_RESIDUAL_FIT_N or len(tune_rows) < MIN_SCALE_TUNE_N:
        raise SpreadChallengerUnavailable(
            "NFL_SPREAD_V3_CALIBRATION_TIME_BLOCK_SPLIT_INSUFFICIENT",
            f"residual_fit={len(fit_rows)} scale_tune={len(tune_rows)}",
        )
    return fit_rows, tune_rows


def _artifact_with_frozen_residual_fit(
    base_artifact: MarginDistributionArtifact,
    residual_fit_rows: Sequence[MarginTrainingRow],
) -> MarginDistributionArtifact:
    residuals = tuple(
        float(row.margin - predict_margin_center(base_artifact, row.features))
        for row in residual_fit_rows
    )
    if len(residuals) < MIN_RESIDUAL_FIT_N or not np.isfinite(np.asarray(residuals)).all():
        raise SpreadChallengerUnavailable(
            "NFL_SPREAD_V3_RESIDUAL_FIT_INVALID",
            f"valid residual_fit_n={len(residuals)}",
        )
    return replace(
        base_artifact,
        model_family=MODEL_FAMILY,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        calibration_residuals=residuals,
        calibration_rows=len(residuals),
        training_dataset_hash=_hash({
            "base_training_dataset_hash": base_artifact.training_dataset_hash,
            "calibration_method": CALIBRATION_METHOD,
            "residual_fit_event_ids": [row.event_id for row in residual_fit_rows],
        }),
    )


def _empirical_crps(samples: np.ndarray, actual: float) -> float:
    """CRPS for an empirical predictive distribution; lower is better."""
    values = np.asarray(samples, dtype=float)
    if len(values) < 2 or not np.isfinite(values).all() or not np.isfinite(float(actual)):
        raise SpreadChallengerUnavailable(
            "NFL_SPREAD_V3_CRPS_INPUT_INVALID",
            "CRPS requires finite empirical samples and outcome",
        )
    first = float(np.mean(np.abs(values - float(actual))))
    second = 0.5 * float(np.mean(np.abs(values[:, None] - values[None, :])))
    return first - second


def _select_scale(
    *,
    residual_artifact: MarginDistributionArtifact,
    tune_rows: Sequence[MarginTrainingRow],
) -> tuple[float, list[dict[str, Any]]]:
    """Select scale on sporting margin CRPS only; no sportsbook data enters."""
    candidates: list[dict[str, Any]] = []
    for scale in SCALE_GRID:
        artifact = _scaled_artifact(residual_artifact, scale)
        residuals = np.asarray(artifact.calibration_residuals, dtype=float)
        scores: list[float] = []
        absolute_errors: list[float] = []
        for row in tune_rows:
            center = predict_margin_center(artifact, row.features)
            samples = center + residuals
            scores.append(_empirical_crps(samples, float(row.margin)))
            absolute_errors.append(abs(center - float(row.margin)))
        mean_crps = float(np.mean(np.asarray(scores, dtype=float)))
        candidates.append({
            "scale": float(scale),
            "sporting_margin_crps": mean_crps,
            "margin_center_mae": float(np.mean(np.asarray(absolute_errors, dtype=float))),
            "tune_row_n": len(tune_rows),
            "market_line_used": False,
        })
    winner = min(
        candidates,
        key=lambda row: (row["sporting_margin_crps"], abs(row["scale"] - 1.0)),
    )
    return float(winner["scale"]), candidates


def run_nfl_context_v3_scale_calibrated_replay(
    *,
    client: Any,
    ridge_alpha: float = RIDGE_ALPHA,
    download_fn: Any = download_asset,
) -> dict[str, Any]:
    events = _nfl_events(client)
    rows, metadata = build_margin_rows(events)
    _train_rows, calibration_rows, validation_rows, forward_reserve = _partition(rows, metadata)
    base_artifact, base_validation, base_forward_reserve = train_candidate(
        events,
        ridge_alpha=ridge_alpha,
    )
    if [row.event_id for row in base_validation] != [row.event_id for row in validation_rows]:
        raise SpreadChallengerUnavailable(
            "NFL_SPREAD_V3_VALIDATION_IDENTITY_MISMATCH",
            "V2 fit and V3 partitions disagree on untouched validation identity",
        )
    if int(base_forward_reserve) != int(forward_reserve):
        raise SpreadChallengerUnavailable(
            "NFL_SPREAD_V3_FORWARD_RESERVE_MISMATCH",
            "V2 fit and V3 partitions disagree on forward reserve",
        )

    residual_fit_rows, scale_tune_rows = _calibration_split(calibration_rows)
    residual_artifact = _artifact_with_frozen_residual_fit(base_artifact, residual_fit_rows)
    selected_scale, scale_search = _select_scale(
        residual_artifact=residual_artifact,
        tune_rows=scale_tune_rows,
    )
    selected_artifact = _scaled_artifact(residual_artifact, selected_scale)

    # Market evidence begins only after every V3 sporting parameter is frozen.
    with TemporaryDirectory(prefix="wow-nfl-spread-v3-") as directory:
        captured = download_fn(schedules_asset(), directory)
        source_rows = _read_csv(captured.local_path)
        validation_evidence, validation_binding = bind_nflverse_close_proxies(
            test_event_ids=[row.event_id for row in validation_rows],
            source_rows=source_rows,
        )
        if validation_binding.get("coverage") != 1.0:
            raise SpreadChallengerUnavailable(
                "NFL_SPREAD_V3_VALIDATION_EXACT_LINE_COVERAGE_INCOMPLETE",
                f"2025 exact-line coverage={validation_binding.get('coverage')}",
            )
        v3_exact = evaluate_historical_close_proxy(
            artifact=selected_artifact,
            test_rows=validation_rows,
            evidence_by_event=validation_evidence,
            evidence_class=NFLVERSE_EVIDENCE_CLASS,
        )
        v2_exact = evaluate_historical_close_proxy(
            artifact=base_artifact,
            test_rows=validation_rows,
            evidence_by_event=validation_evidence,
            evidence_class=NFLVERSE_EVIDENCE_CLASS,
        )
        source_manifest = _source_manifest(captured)

    no_skill_brier = 0.25
    no_skill_log_loss = float(np.log(2.0))
    screen = {
        "beats_no_skill_brier": bool(v3_exact["cover_brier"] < no_skill_brier),
        "beats_no_skill_log_loss": bool(v3_exact["cover_log_loss"] < no_skill_log_loss),
        "ece_within_0_10": bool(v3_exact["cover_ece"] is not None and v3_exact["cover_ece"] <= 0.10),
        "not_worse_than_v2_brier": bool(v3_exact["cover_brier"] <= v2_exact["cover_brier"]),
        "not_worse_than_v2_log_loss": bool(v3_exact["cover_log_loss"] <= v2_exact["cover_log_loss"]),
    }
    screen["passes"] = all(screen.values())

    return {
        "status": "EXPERIMENT_CREATED",
        "code": "NFL_SPREAD_CONTEXT_V3_SCALE_CAL_EXACT_LINE_REPLAY_COMPLETE",
        "sport": "NFL",
        "model_program": MODEL_PROGRAM,
        "model_family": MODEL_FAMILY,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "calibration_method": CALIBRATION_METHOD,
        "selected_residual_scale": selected_scale,
        "scale_search_sporting_margin_only": scale_search,
        "artifact_train_rows": base_artifact.train_rows,
        "artifact_calibration_rows": selected_artifact.calibration_rows,
        "calibration_season_total_rows": len(calibration_rows),
        "residual_fit_rows": len(residual_fit_rows),
        "scale_tune_rows": len(scale_tune_rows),
        "artifact_test_rows": base_artifact.test_rows,
        "forward_reserve_rows": forward_reserve,
        "training_dataset_hash": selected_artifact.training_dataset_hash,
        "exact_line_metrics": v3_exact,
        "v2_reference_metrics_same_validation": {
            key: v2_exact.get(key)
            for key in ("evidence_row_n", "exact_line_coverage", "cover_brier", "cover_log_loss", "cover_ece", "three_way_brier")
        },
        "binding_audit": validation_binding,
        "source_manifest": source_manifest,
        "research_screen": screen,
        "reference": {
            "no_skill_brier": no_skill_brier,
            "no_skill_log_loss": no_skill_log_loss,
        },
        "calibration_chronology": "2021_2023_CENTER_FIT__2024_EARLY_RESIDUAL_FIT__2024_LATE_SCALE_TUNE__2025_UNTOUCHED_VALIDATION__2026_FORWARD_RESERVE",
        "validation_season_used_for_scale_selection": False,
        "market_line_used_for_scale_selection": False,
        "market_features_used": False,
        "spread_line_used_as_margin_model_feature": False,
        "spread_line_role": "POST_SPORTING_DISTRIBUTION_FREEZE_EXACT_LINE_EVALUATION_ONLY",
        "market_probability_substitution_used": False,
        "moneyline_probability_used": False,
        "manual_probability_adjustments_used": False,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "dry_run_only_no_live_trading_no_market_orders": True,
        "global_terminal_reducer": GLOBAL_TERMINAL_REDUCER,
        "can_execute": False,
    }


__all__ = [
    "CALIBRATION_METHOD",
    "FEATURE_SCHEMA_VERSION",
    "MODEL_FAMILY",
    "MODEL_PROGRAM",
    "SCALE_GRID",
    "_calibration_split",
    "_empirical_crps",
    "_scaled_artifact",
    "_select_scale",
    "run_nfl_context_v3_scale_calibrated_replay",
]
