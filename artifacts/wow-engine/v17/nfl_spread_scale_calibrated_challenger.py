"""Research-only NFL spread V3 residual-scale calibration challenger.

V2 fits a leakage-safe sporting margin center from 2021-2023 and freezes 2024
for calibration, 2025 for terminal validation, and 2026 for forward reserve.
This challenger changes only one calibration degree of freedom: a global scale
multiplier applied around the mean of the already-frozen 2024 sporting residual
distribution. The multiplier is selected on 2024 exact-line proper scoring
metrics, then evaluated once on untouched 2025 exact-line evidence.

The sportsbook spread remains a post-fit query threshold. It is not a margin
model feature, is not converted from moneyline probability, and is never used
to alter the fitted margin center. This module is Class-C research only and can
never certify, promote, publish, rank, or execute a wager.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from tempfile import TemporaryDirectory
from typing import Any, Mapping, Sequence

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
    HistoricalCloseProxy,
    evaluate_historical_close_proxy,
)
from v17.spread_margin_challenger import (
    MarginDistributionArtifact,
    MarginTrainingRow,
    SpreadChallengerUnavailable,
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
CALIBRATION_METHOD = "GLOBAL_RESIDUAL_SCALE_SELECTED_ON_2024_ONLY"
SCALE_GRID = (0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0)


def _hash(payload: Any) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _scaled_artifact(
    artifact: MarginDistributionArtifact,
    scale: float,
) -> MarginDistributionArtifact:
    """Scale residual dispersion around its frozen mean without moving center."""
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
            "base V2 residual distribution is unavailable or non-finite",
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
        }),
    )


def _select_scale(
    *,
    base_artifact: MarginDistributionArtifact,
    calibration_rows: Sequence[MarginTrainingRow],
    calibration_evidence: Mapping[str, HistoricalCloseProxy],
) -> tuple[float, list[dict[str, Any]]]:
    """Select one scalar strictly on the frozen calibration season."""
    candidates: list[dict[str, Any]] = []
    for scale in SCALE_GRID:
        artifact = _scaled_artifact(base_artifact, scale)
        metrics = evaluate_historical_close_proxy(
            artifact=artifact,
            test_rows=calibration_rows,
            evidence_by_event=calibration_evidence,
            evidence_class=NFLVERSE_EVIDENCE_CLASS,
        )
        log_loss = metrics.get("cover_log_loss")
        brier = metrics.get("cover_brier")
        ece = metrics.get("cover_ece")
        if not all(isinstance(value, (int, float)) and np.isfinite(float(value)) for value in (log_loss, brier, ece)):
            raise SpreadChallengerUnavailable(
                "NFL_SPREAD_V3_CALIBRATION_METRICS_INVALID",
                f"non-finite exact-line calibration metrics at scale={scale}",
            )
        candidates.append({
            "scale": float(scale),
            "cover_log_loss": float(log_loss),
            "cover_brier": float(brier),
            "cover_ece": float(ece),
            "evidence_row_n": int(metrics.get("evidence_row_n") or 0),
            "exact_line_coverage": float(metrics.get("exact_line_coverage") or 0.0),
        })
    # Predeclared deterministic objective: proper log loss, then Brier, then ECE,
    # then the scale closest to identity. No validation-season value participates.
    winner = min(
        candidates,
        key=lambda row: (
            row["cover_log_loss"],
            row["cover_brier"],
            row["cover_ece"],
            abs(row["scale"] - 1.0),
        ),
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
            "V2 fit and V3 calibration partitions disagree on untouched validation identity",
        )
    if int(base_forward_reserve) != int(forward_reserve):
        raise SpreadChallengerUnavailable(
            "NFL_SPREAD_V3_FORWARD_RESERVE_MISMATCH",
            "V2 fit and V3 calibration partitions disagree on forward reserve",
        )

    with TemporaryDirectory(prefix="wow-nfl-spread-v3-") as directory:
        captured = download_fn(schedules_asset(), directory)
        source_rows = _read_csv(captured.local_path)
        calibration_evidence, calibration_binding = bind_nflverse_close_proxies(
            test_event_ids=[row.event_id for row in calibration_rows],
            source_rows=source_rows,
        )
        validation_evidence, validation_binding = bind_nflverse_close_proxies(
            test_event_ids=[row.event_id for row in validation_rows],
            source_rows=source_rows,
        )
        if calibration_binding.get("coverage") != 1.0:
            raise SpreadChallengerUnavailable(
                "NFL_SPREAD_V3_CALIBRATION_EXACT_LINE_COVERAGE_INCOMPLETE",
                f"2024 exact-line coverage={calibration_binding.get('coverage')}",
            )
        if validation_binding.get("coverage") != 1.0:
            raise SpreadChallengerUnavailable(
                "NFL_SPREAD_V3_VALIDATION_EXACT_LINE_COVERAGE_INCOMPLETE",
                f"2025 exact-line coverage={validation_binding.get('coverage')}",
            )

        selected_scale, scale_search = _select_scale(
            base_artifact=base_artifact,
            calibration_rows=calibration_rows,
            calibration_evidence=calibration_evidence,
        )
        selected_artifact = _scaled_artifact(base_artifact, selected_scale)
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
        "scale_search_calibration_only": scale_search,
        "artifact_train_rows": base_artifact.train_rows,
        "artifact_calibration_rows": base_artifact.calibration_rows,
        "artifact_test_rows": base_artifact.test_rows,
        "forward_reserve_rows": forward_reserve,
        "training_dataset_hash": selected_artifact.training_dataset_hash,
        "exact_line_metrics": v3_exact,
        "v2_reference_metrics_same_validation": {
            key: v2_exact.get(key)
            for key in ("evidence_row_n", "exact_line_coverage", "cover_brier", "cover_log_loss", "cover_ece", "three_way_brier")
        },
        "calibration_binding_audit": calibration_binding,
        "binding_audit": validation_binding,
        "source_manifest": source_manifest,
        "research_screen": screen,
        "reference": {
            "no_skill_brier": no_skill_brier,
            "no_skill_log_loss": no_skill_log_loss,
        },
        "validation_season_used_for_scale_selection": False,
        "market_features_used": False,
        "spread_line_used_as_margin_model_feature": False,
        "spread_line_role": "POST_FIT_THRESHOLD_AND_CALIBRATION_SCORING_TARGET_ONLY",
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
    "_scaled_artifact",
    "_select_scale",
    "run_nfl_context_v3_scale_calibrated_replay",
]
