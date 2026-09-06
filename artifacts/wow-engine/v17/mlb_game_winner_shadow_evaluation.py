"""Chronological/OOS evaluation for the V17 MLB Game Winner shadow challenger.

The evaluator is research/shadow only.  It does not change Game Winner admission,
NO_PICK thresholds, cash/value gates, portfolio rules, serving state, or the V17
terminal reducer.

The historical/forward bridge is intentionally built from the exact 38-column
side-feature contract shared by:
- wow_mlb_v2a_run_features_2024 / 2025, and
- wow_mlb_forward_feature_snapshots.

That matters because the older 36-column V2A *game* vector used different rolling
availability semantics (for example capped team/starter history counts).  Mapping
uncapped 2026 forward side snapshots into that older contract creates severe OOD
feature drift.  This module therefore pairs HOME/AWAY side rows under the shared
run-feature schema and derives a new, explicit home-relative game vector.

No sportsbook, no-vig, payout, CLV, or postgame field may enter the sporting
feature vector.  Promotion is evidence-only here and is never automatic.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any, Mapping, Sequence

from v17.mlb_game_winner_shadow_challenger import (
    MARKET_PRIOR_WEIGHT,
    ShadowChallengerError,
    audit_feature_names,
    calibration_first_comparison,
    fit_shadow_challenger,
    predict_shadow,
    probability_metrics,
)

RETROSPECTIVE_PROVENANCE = "RETROSPECTIVE_PRE_GAME_FEATURE_CONTRACT"
TIMESTAMPED_PREGAME_PROVENANCE = "TIMESTAMPED_PREGAME"
SERVING_MODE = "SHADOW_ONLY"
AUTOMATIC_PROMOTION = False
CAN_EXECUTE = False
PAIRED_RUN_GAME_FEATURE_SCHEMA_VERSION = "MLB_GAME_WIN_PAIRED_RUN_FEATURES_V1"

RUN_SIDE_FEATURES = (
    "is_home",
    "off_runs_pg",
    "off_hits_pg",
    "off_hr_pg",
    "off_bb_pg",
    "off_so_pg",
    "off_tb_pg",
    "off_run_diff_pg",
    "off_win_rate",
    "off_sb_pg",
    "off_cs_pg",
    "off_days_rest",
    "opp_runs_allowed_pg",
    "opp_errors_pg",
    "opp_win_rate",
    "opp_bp_era",
    "opp_bp_k_rate",
    "opp_bp_bb_rate",
    "opp_bp_hr_rate",
    "opp_bp_pitches_3d",
    "opp_bp_outs_3d",
    "opp_bp_apps_3d",
    "opp_starter_prior_starts",
    "opp_starter_era",
    "opp_starter_k_rate",
    "opp_starter_bb_rate",
    "opp_starter_h_rate",
    "opp_starter_hr_rate",
    "opp_starter_outs_per_start",
    "opp_starter_tbf_per_start",
    "opp_starter_pitches_per_start",
    "opp_starter_strike_rate",
    "opp_starter_days_rest",
    "opp_starter_pitches_last3",
    "park_total_runs_prior",
    "park_prior_games",
    "opp_days_rest",
    "min_team_prior_games",
)

# These are scoring-team features on each side row, so HOME-AWAY is direct.
_DIRECT_SIDE_FEATURES = (
    "off_runs_pg",
    "off_hits_pg",
    "off_hr_pg",
    "off_bb_pg",
    "off_so_pg",
    "off_tb_pg",
    "off_run_diff_pg",
    "off_win_rate",
    "off_sb_pg",
    "off_cs_pg",
    "off_days_rest",
)

# These describe the opponent of the scoring team.  The AWAY scoring row holds
# HOME pitching/defense state, so the subtraction is reversed to preserve
# HOME-entity minus AWAY-entity semantics.
_REVERSED_OPPONENT_FEATURES = (
    "opp_runs_allowed_pg",
    "opp_errors_pg",
    "opp_bp_era",
    "opp_bp_k_rate",
    "opp_bp_bb_rate",
    "opp_bp_hr_rate",
    "opp_bp_pitches_3d",
    "opp_bp_outs_3d",
    "opp_bp_apps_3d",
    "opp_starter_prior_starts",
    "opp_starter_era",
    "opp_starter_k_rate",
    "opp_starter_bb_rate",
    "opp_starter_h_rate",
    "opp_starter_hr_rate",
    "opp_starter_outs_per_start",
    "opp_starter_tbf_per_start",
    "opp_starter_pitches_per_start",
    "opp_starter_strike_rate",
    "opp_starter_days_rest",
    "opp_starter_pitches_last3",
)

# opp_win_rate and opp_days_rest are exact mirrors of off_win_rate/off_days_rest
# in a correctly paired game and are intentionally omitted to avoid duplicate
# columns.  is_home is also omitted because it is constant after pairing.
PAIRED_RUN_GAME_FEATURES = tuple(
    [f"{name}_home_minus_away" for name in _DIRECT_SIDE_FEATURES]
    + [f"{name}_home_entity_minus_away_entity" for name in _REVERSED_OPPONENT_FEATURES]
    + ["park_total_runs_prior", "park_prior_games", "min_team_prior_games"]
)


@dataclass(frozen=True)
class EvidenceRow:
    event_id: str
    event_start_time: datetime
    feature_row: Mapping[str, Any]
    home_win: bool
    provenance_status: str
    feature_timestamp: datetime | None = None
    outcome_timestamp: datetime | None = None
    champion_home_probability: float | None = None


@dataclass(frozen=True)
class ChronologicalSplit:
    train: tuple[EvidenceRow, ...]
    calibration: tuple[EvidenceRow, ...]
    holdout: tuple[EvidenceRow, ...]


class EvaluationEvidenceError(ShadowChallengerError):
    pass


def _finite(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise EvaluationEvidenceError(f"MODEL_INPUTS_INSUFFICIENT: non_numeric_feature={field}")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise EvaluationEvidenceError(f"MODEL_INPUTS_INSUFFICIENT: non_numeric_feature={field}") from exc
    if not isfinite(parsed):
        raise EvaluationEvidenceError(f"MODEL_INPUTS_INSUFFICIENT: non_finite_feature={field}")
    return parsed


def _vector_mapping(feature_names: Sequence[str], feature_vector: Sequence[Any]) -> dict[str, float]:
    audit_feature_names(feature_names)
    if len(feature_names) != len(feature_vector):
        raise EvaluationEvidenceError("MODEL_INPUTS_INSUFFICIENT: feature_vector_length_mismatch")
    mapping = {
        str(name): _finite(value, field=str(name))
        for name, value in zip(feature_names, feature_vector)
    }
    missing = [name for name in RUN_SIDE_FEATURES if name not in mapping]
    if missing:
        raise EvaluationEvidenceError(
            "MODEL_INPUTS_INSUFFICIENT: run_side_features_missing=" + ",".join(missing)
        )
    return mapping


def _shared_context(home: Mapping[str, float], away: Mapping[str, float], name: str) -> float:
    h = home[name]
    a = away[name]
    if abs(h - a) > 1e-9:
        raise EvaluationEvidenceError(f"MODEL_INPUTS_CONFLICT: shared_context_mismatch={name}")
    return h


def materialize_paired_run_game_features(
    home_feature_names: Sequence[str],
    home_feature_vector: Sequence[Any],
    away_feature_names: Sequence[str],
    away_feature_vector: Sequence[Any],
) -> dict[str, float]:
    """Pair exact historical/forward side vectors into one home-relative row."""
    home = _vector_mapping(home_feature_names, home_feature_vector)
    away = _vector_mapping(away_feature_names, away_feature_vector)

    # Side identity is part of the source contract and must agree with the pair.
    if not home["is_home"] >= 0.5 or not away["is_home"] < 0.5:
        raise EvaluationEvidenceError("MODEL_INPUTS_CONFLICT: home_away_side_identity_mismatch")

    result: dict[str, float] = {}
    for name in _DIRECT_SIDE_FEATURES:
        result[f"{name}_home_minus_away"] = home[name] - away[name]
    for name in _REVERSED_OPPONENT_FEATURES:
        result[f"{name}_home_entity_minus_away_entity"] = away[name] - home[name]

    result["park_total_runs_prior"] = _shared_context(home, away, "park_total_runs_prior")
    result["park_prior_games"] = _shared_context(home, away, "park_prior_games")
    result["min_team_prior_games"] = _shared_context(home, away, "min_team_prior_games")

    if tuple(result) != PAIRED_RUN_GAME_FEATURES:
        raise EvaluationEvidenceError("MODEL_OUTPUT_INVALID: paired_feature_order_mismatch")
    audit_feature_names(tuple(result))
    return result


# Clear aliases: both historical run rows and frozen forward rows use the exact
# same source schema; only the provenance wrapper differs.
materialize_historical_run_pair = materialize_paired_run_game_features
materialize_forward_run_pair = materialize_paired_run_game_features


def validate_evidence_row(row: EvidenceRow) -> None:
    if not row.event_id:
        raise EvaluationEvidenceError("MODEL_INPUTS_INSUFFICIENT: missing_event_id")
    audit_feature_names(tuple(row.feature_row.keys()))
    missing = [name for name in PAIRED_RUN_GAME_FEATURES if name not in row.feature_row]
    if missing:
        raise EvaluationEvidenceError(
            "MODEL_INPUTS_INSUFFICIENT: paired_game_features_missing=" + ",".join(missing)
        )

    if row.provenance_status == TIMESTAMPED_PREGAME_PROVENANCE:
        if row.feature_timestamp is None or row.outcome_timestamp is None:
            raise EvaluationEvidenceError("TEMPORAL_PROVENANCE_INSUFFICIENT: timestamps_required")
        if not row.feature_timestamp < row.event_start_time:
            raise EvaluationEvidenceError("GOVERNANCE_POSTGAME_LEAKAGE: feature_timestamp_not_pregame")
        if row.outcome_timestamp < row.event_start_time:
            raise EvaluationEvidenceError("TEMPORAL_PROVENANCE_INVALID: outcome_before_event_start")
    elif row.provenance_status != RETROSPECTIVE_PROVENANCE:
        raise EvaluationEvidenceError(
            f"TEMPORAL_PROVENANCE_INVALID: unsupported_status={row.provenance_status}"
        )

    if row.champion_home_probability is not None:
        p = _finite(row.champion_home_probability, field="champion_home_probability")
        if not 0.0 < p < 1.0:
            raise EvaluationEvidenceError("MODEL_OUTPUT_INVALID: champion_probability_out_of_range")


def chronological_split(
    rows: Sequence[EvidenceRow],
    *,
    train_end: datetime,
    calibration_end: datetime,
) -> ChronologicalSplit:
    """Create strict chronological train/calibration/holdout partitions."""
    if not train_end < calibration_end:
        raise EvaluationEvidenceError("MODEL_INPUTS_INSUFFICIENT: invalid_chronological_boundaries")
    if not rows:
        raise EvaluationEvidenceError("MODEL_INPUTS_INSUFFICIENT: no_evidence_rows")

    ordered = sorted(rows, key=lambda row: (row.event_start_time, row.event_id))
    seen: set[str] = set()
    for row in ordered:
        validate_evidence_row(row)
        if row.event_id in seen:
            raise EvaluationEvidenceError(f"MODEL_INPUTS_CONFLICT: duplicate_event_id={row.event_id}")
        seen.add(row.event_id)

    train = tuple(row for row in ordered if row.event_start_time <= train_end)
    calibration = tuple(row for row in ordered if train_end < row.event_start_time <= calibration_end)
    holdout = tuple(row for row in ordered if row.event_start_time > calibration_end)
    if not train or not calibration or not holdout:
        raise EvaluationEvidenceError("INSUFFICIENT_OOS_EVIDENCE: empty_chronological_partition")
    return ChronologicalSplit(train=train, calibration=calibration, holdout=holdout)


def _assert_minimum_evidence(
    split: ChronologicalSplit,
    *,
    min_train: int,
    min_calibration: int,
    min_holdout: int,
) -> None:
    counts = {
        "train": len(split.train),
        "calibration": len(split.calibration),
        "holdout": len(split.holdout),
    }
    minimums = {
        "train": int(min_train),
        "calibration": int(min_calibration),
        "holdout": int(min_holdout),
    }
    failed = [name for name in counts if counts[name] < minimums[name]]
    if failed:
        detail = ",".join(f"{name}={counts[name]}/{minimums[name]}" for name in failed)
        raise EvaluationEvidenceError(f"INSUFFICIENT_OOS_EVIDENCE: {detail}")


def evaluate_retrospective_challenger(
    split: ChronologicalSplit,
    *,
    min_train: int = 1000,
    min_calibration: int = 250,
    min_holdout: int = 250,
    bootstrap_models: int = 48,
    seed: int = 1706,
) -> dict[str, Any]:
    """Fit/calibrate chronologically and evaluate only the untouched holdout."""
    _assert_minimum_evidence(
        split,
        min_train=min_train,
        min_calibration=min_calibration,
        min_holdout=min_holdout,
    )

    artifact = fit_shadow_challenger(
        [row.feature_row for row in split.train],
        [row.home_win for row in split.train],
        [row.feature_row for row in split.calibration],
        [row.home_win for row in split.calibration],
        feature_names=PAIRED_RUN_GAME_FEATURES,
        bootstrap_models=bootstrap_models,
        seed=seed,
    )
    # The base fitter permits research feature sets.  Make the evidence schema
    # explicit on the research artifact rather than falsely claiming the richer
    # default tail schema.
    artifact.feature_schema_version = PAIRED_RUN_GAME_FEATURE_SCHEMA_VERSION

    predictions = predict_shadow(artifact, [row.feature_row for row in split.holdout])
    challenger_p = [prediction.home_probability_calibrated for prediction in predictions]
    y = [row.home_win for row in split.holdout]
    challenger_metrics = probability_metrics(y, challenger_p)

    champion_available = all(row.champion_home_probability is not None for row in split.holdout)
    comparison = None
    if champion_available:
        comparison = calibration_first_comparison(
            y,
            [float(row.champion_home_probability) for row in split.holdout],
            challenger_p,
        )

    return {
        "feature_schema_version": PAIRED_RUN_GAME_FEATURE_SCHEMA_VERSION,
        "training_n": len(split.train),
        "calibration_n": len(split.calibration),
        "holdout_n": len(split.holdout),
        "holdout_challenger_metrics": challenger_metrics,
        "holdout_champion_comparison": comparison,
        "retrospective_evidence_status": "RETROSPECTIVE_OOS_COMPLETE",
        "pristine_forward_evidence_status": "FORWARD_CHALLENGER_SCORES_REQUIRED",
        "promotion_evidence_status": "SHADOW_CONTINUE",
        "serving_mode": SERVING_MODE,
        "automatic_promotion": AUTOMATIC_PROMOTION,
        "admission_policy_mutated": False,
        "cash_single_gate_mutated": False,
        "market_prior_weight": MARKET_PRIOR_WEIGHT,
        "can_execute": CAN_EXECUTE,
    }


def evaluate_forward_shadow(
    rows: Sequence[EvidenceRow],
    challenger_home_probabilities: Sequence[float],
    *,
    min_forward: int = 100,
) -> dict[str, Any]:
    """Compare timestamped forward challenger probabilities with the champion."""
    if len(rows) != len(challenger_home_probabilities):
        raise EvaluationEvidenceError("MODEL_INPUTS_INSUFFICIENT: forward_probability_length_mismatch")
    if len(rows) < int(min_forward):
        raise EvaluationEvidenceError(
            f"INSUFFICIENT_OOS_EVIDENCE: forward={len(rows)}/{int(min_forward)}"
        )

    event_ids: set[str] = set()
    for row in rows:
        validate_evidence_row(row)
        if row.provenance_status != TIMESTAMPED_PREGAME_PROVENANCE:
            raise EvaluationEvidenceError("TEMPORAL_PROVENANCE_INSUFFICIENT: forward_rows_must_be_timestamped")
        if row.event_id in event_ids:
            raise EvaluationEvidenceError(f"MODEL_INPUTS_CONFLICT: duplicate_event_id={row.event_id}")
        event_ids.add(row.event_id)
        if row.champion_home_probability is None:
            raise EvaluationEvidenceError("MODEL_INPUTS_INSUFFICIENT: champion_probability_required")

    ordered = sorted(zip(rows, challenger_home_probabilities), key=lambda pair: pair[0].event_start_time)
    y = [row.home_win for row, _ in ordered]
    champion = [float(row.champion_home_probability) for row, _ in ordered]
    challenger = [_finite(p, field="challenger_home_probability") for _, p in ordered]
    if any(not 0.0 < p < 1.0 for p in challenger):
        raise EvaluationEvidenceError("MODEL_OUTPUT_INVALID: challenger_probability_out_of_range")

    comparison = calibration_first_comparison(y, champion, challenger)
    metrics_pass = bool(comparison["calibration_first_metrics_pass"])
    return {
        **comparison,
        "feature_schema_version": PAIRED_RUN_GAME_FEATURE_SCHEMA_VERSION,
        "forward_n": len(rows),
        "pristine_forward_evidence_status": "FORWARD_OOS_COMPLETE",
        "promotion_evidence_status": "SHADOW_REVIEW_REQUIRED" if metrics_pass else "SHADOW_CONTINUE",
        "serving_mode": SERVING_MODE,
        "automatic_promotion": AUTOMATIC_PROMOTION,
        "admission_policy_mutated": False,
        "cash_single_gate_mutated": False,
        "market_prior_weight": MARKET_PRIOR_WEIGHT,
        "can_execute": CAN_EXECUTE,
    }


__all__ = [
    "AUTOMATIC_PROMOTION",
    "CAN_EXECUTE",
    "ChronologicalSplit",
    "EvaluationEvidenceError",
    "EvidenceRow",
    "PAIRED_RUN_GAME_FEATURES",
    "PAIRED_RUN_GAME_FEATURE_SCHEMA_VERSION",
    "RETROSPECTIVE_PROVENANCE",
    "RUN_SIDE_FEATURES",
    "SERVING_MODE",
    "TIMESTAMPED_PREGAME_PROVENANCE",
    "chronological_split",
    "evaluate_forward_shadow",
    "evaluate_retrospective_challenger",
    "materialize_forward_run_pair",
    "materialize_historical_run_pair",
    "materialize_paired_run_game_features",
    "validate_evidence_row",
]
