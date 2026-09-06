"""Chronological/OOS evaluation harness for the V17 MLB Game Winner shadow challenger.

This module evaluates sporting probabilities only.  It never changes Game Winner
admission, NO_PICK thresholds, cash/value gates, portfolio rules, serving state,
or the terminal reducer.  Promotion remains a separate governed action.

The evaluator deliberately distinguishes two evidence classes:

* RETROSPECTIVE_PRE_GAME_FEATURE_CONTRACT: historical rows built only from
  information available before each game, but without immutable timestamped
  forward provenance.  These rows may train/calibrate and provide diagnostics.
* TIMESTAMPED_PREGAME: frozen forward rows whose feature/prediction timestamp is
  earlier than event start and whose outcome timestamp is at/after event start.
  Only this class can satisfy the pristine forward-shadow evidence requirement.

No sportsbook price, no-vig probability, PrizePicks payout/multiplier, CLV, or
postgame field is accepted as a sporting-model feature.
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

# Exact 36-feature historical V2A game-vector contract already present in the
# validation database.  This is intentionally separate from the richer MSD tail
# contract; missing MSD tail history is not silently invented or back-filled
# from market information.
HISTORICAL_V2A_FEATURES = (
    "runs_pg_diff",
    "hits_pg_diff",
    "hr_pg_diff",
    "bb_pg_diff",
    "so_pg_diff",
    "tb_pg_diff",
    "runs_allowed_pg_diff",
    "run_diff_pg_diff",
    "errors_pg_diff",
    "sb_pg_diff",
    "cs_pg_diff",
    "win_rate_diff",
    "bp_era_diff",
    "bp_k_rate_diff",
    "bp_bb_rate_diff",
    "bp_hr_rate_diff",
    "bp_pitches_3d_diff",
    "bp_outs_3d_diff",
    "bp_apps_3d_diff",
    "starter_prior_starts_diff",
    "starter_era_diff",
    "starter_k_rate_diff",
    "starter_bb_rate_diff",
    "starter_h_rate_diff",
    "starter_hr_rate_diff",
    "starter_outs_per_start_diff",
    "starter_tbf_per_start_diff",
    "starter_pitches_per_start_diff",
    "starter_strike_rate_diff",
    "starter_days_rest_diff",
    "starter_pitches_last3_diff",
    "park_total_runs_prior",
    "park_prior_games",
    "team_rest_diff",
    "starter_min_prior_starts",
    "team_min_prior_games",
)

# Forward side snapshots are stored from the scoring-team perspective.  Offense
# fields therefore subtract home-away directly, while opponent/starter/bullpen
# fields reverse sides so the historical game-vector semantics remain
# home-minus-away.
_FORWARD_OFFENSE_MAP = {
    "runs_pg_diff": "off_runs_pg",
    "hits_pg_diff": "off_hits_pg",
    "hr_pg_diff": "off_hr_pg",
    "bb_pg_diff": "off_bb_pg",
    "so_pg_diff": "off_so_pg",
    "tb_pg_diff": "off_tb_pg",
    "run_diff_pg_diff": "off_run_diff_pg",
    "sb_pg_diff": "off_sb_pg",
    "cs_pg_diff": "off_cs_pg",
    "win_rate_diff": "off_win_rate",
    "team_rest_diff": "off_days_rest",
}
_FORWARD_REVERSED_OPPONENT_MAP = {
    "runs_allowed_pg_diff": "opp_runs_allowed_pg",
    "errors_pg_diff": "opp_errors_pg",
    "bp_era_diff": "opp_bp_era",
    "bp_k_rate_diff": "opp_bp_k_rate",
    "bp_bb_rate_diff": "opp_bp_bb_rate",
    "bp_hr_rate_diff": "opp_bp_hr_rate",
    "bp_pitches_3d_diff": "opp_bp_pitches_3d",
    "bp_outs_3d_diff": "opp_bp_outs_3d",
    "bp_apps_3d_diff": "opp_bp_apps_3d",
    "starter_prior_starts_diff": "opp_starter_prior_starts",
    "starter_era_diff": "opp_starter_era",
    "starter_k_rate_diff": "opp_starter_k_rate",
    "starter_bb_rate_diff": "opp_starter_bb_rate",
    "starter_h_rate_diff": "opp_starter_h_rate",
    "starter_hr_rate_diff": "opp_starter_hr_rate",
    "starter_outs_per_start_diff": "opp_starter_outs_per_start",
    "starter_tbf_per_start_diff": "opp_starter_tbf_per_start",
    "starter_pitches_per_start_diff": "opp_starter_pitches_per_start",
    "starter_strike_rate_diff": "opp_starter_strike_rate",
    "starter_days_rest_diff": "opp_starter_days_rest",
    "starter_pitches_last3_diff": "opp_starter_pitches_last3",
}


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
    return {
        str(name): _finite(value, field=str(name))
        for name, value in zip(feature_names, feature_vector)
    }


def materialize_historical_game_features(
    feature_names: Sequence[str], feature_vector: Sequence[Any]
) -> dict[str, float]:
    """Materialize the exact pre-existing 36-feature historical game contract."""
    source = _vector_mapping(feature_names, feature_vector)
    missing = [name for name in HISTORICAL_V2A_FEATURES if name not in source]
    if missing:
        raise EvaluationEvidenceError(
            "MODEL_INPUTS_INSUFFICIENT: historical_features_missing=" + ",".join(missing)
        )
    return {name: source[name] for name in HISTORICAL_V2A_FEATURES}


def _shared_context(home: Mapping[str, float], away: Mapping[str, float], name: str) -> float:
    h = home.get(name)
    a = away.get(name)
    if h is None and a is None:
        raise EvaluationEvidenceError(f"MODEL_INPUTS_INSUFFICIENT: forward_feature_missing={name}")
    if h is None:
        return float(a)
    if a is None:
        return float(h)
    # These are venue-level features and should be identical on both side rows.
    # A disagreement is evidence corruption, not a reason to average silently.
    if abs(float(h) - float(a)) > 1e-9:
        raise EvaluationEvidenceError(f"MODEL_INPUTS_CONFLICT: shared_context_mismatch={name}")
    return float(h)


def materialize_forward_game_features(
    home_feature_names: Sequence[str],
    home_feature_vector: Sequence[Any],
    away_feature_names: Sequence[str],
    away_feature_vector: Sequence[Any],
) -> dict[str, float]:
    """Convert two frozen side snapshots to the historical home-minus-away vector.

    This function does not synthesize the richer MSD tail features.  It preserves
    exact semantics for the historical/forward overlap so challenger evidence can
    be accumulated without pretending unsupported tail measurements exist.
    """
    home = _vector_mapping(home_feature_names, home_feature_vector)
    away = _vector_mapping(away_feature_names, away_feature_vector)
    result: dict[str, float] = {}

    for output_name, source_name in _FORWARD_OFFENSE_MAP.items():
        if source_name not in home or source_name not in away:
            raise EvaluationEvidenceError(
                f"MODEL_INPUTS_INSUFFICIENT: forward_feature_missing={source_name}"
            )
        result[output_name] = home[source_name] - away[source_name]

    # Each scoring-side row contains opponent pitching/defense state.  The away
    # scoring row therefore contains the HOME starter/bullpen/defense state.
    for output_name, source_name in _FORWARD_REVERSED_OPPONENT_MAP.items():
        if source_name not in home or source_name not in away:
            raise EvaluationEvidenceError(
                f"MODEL_INPUTS_INSUFFICIENT: forward_feature_missing={source_name}"
            )
        result[output_name] = away[source_name] - home[source_name]

    result["park_total_runs_prior"] = _shared_context(home, away, "park_total_runs_prior")
    result["park_prior_games"] = _shared_context(home, away, "park_prior_games")

    starter_key = "opp_starter_prior_starts"
    if starter_key not in home or starter_key not in away:
        raise EvaluationEvidenceError(
            f"MODEL_INPUTS_INSUFFICIENT: forward_feature_missing={starter_key}"
        )
    result["starter_min_prior_starts"] = min(home[starter_key], away[starter_key])

    team_key = "min_team_prior_games"
    if team_key not in home or team_key not in away:
        raise EvaluationEvidenceError(
            f"MODEL_INPUTS_INSUFFICIENT: forward_feature_missing={team_key}"
        )
    result["team_min_prior_games"] = min(home[team_key], away[team_key])

    missing = [name for name in HISTORICAL_V2A_FEATURES if name not in result]
    if missing:
        raise EvaluationEvidenceError(
            "MODEL_INPUTS_INSUFFICIENT: materialized_features_missing=" + ",".join(missing)
        )
    return {name: result[name] for name in HISTORICAL_V2A_FEATURES}


def validate_evidence_row(row: EvidenceRow) -> None:
    if not row.event_id:
        raise EvaluationEvidenceError("MODEL_INPUTS_INSUFFICIENT: missing_event_id")
    audit_feature_names(tuple(row.feature_row.keys()))
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
    """Create a strict chronological train/calibration/holdout partition."""
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
    calibration = tuple(
        row for row in ordered if train_end < row.event_start_time <= calibration_end
    )
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
    feature_names: Sequence[str] = HISTORICAL_V2A_FEATURES,
    min_train: int = 1000,
    min_calibration: int = 250,
    min_holdout: int = 250,
    bootstrap_models: int = 48,
    seed: int = 1706,
) -> dict[str, Any]:
    """Fit/calibrate chronologically and evaluate the untouched holdout.

    The returned promotion field is evidence state only.  It cannot promote the
    challenger, change serving mode, or alter any pick/cash gate.
    """
    _assert_minimum_evidence(
        split,
        min_train=min_train,
        min_calibration=min_calibration,
        min_holdout=min_holdout,
    )
    names = tuple(feature_names)
    audit_feature_names(names)

    artifact = fit_shadow_challenger(
        [row.feature_row for row in split.train],
        [row.home_win for row in split.train],
        [row.feature_row for row in split.calibration],
        [row.home_win for row in split.calibration],
        feature_names=names,
        bootstrap_models=bootstrap_models,
        seed=seed,
    )
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
        "forward_n": len(rows),
        "pristine_forward_evidence_status": "FORWARD_OOS_COMPLETE",
        "promotion_evidence_status": (
            "SHADOW_REVIEW_REQUIRED" if metrics_pass else "SHADOW_CONTINUE"
        ),
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
    "HISTORICAL_V2A_FEATURES",
    "RETROSPECTIVE_PROVENANCE",
    "SERVING_MODE",
    "TIMESTAMPED_PREGAME_PROVENANCE",
    "chronological_split",
    "evaluate_forward_shadow",
    "evaluate_retrospective_challenger",
    "materialize_forward_game_features",
    "materialize_historical_game_features",
    "validate_evidence_row",
]
