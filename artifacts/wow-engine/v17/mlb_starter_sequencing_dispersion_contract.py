"""V17 contract/audit helpers for the MLB outright-winner MSD challenger.

This module is deliberately not a replacement scorer.  It defines the machine
contract that a fitted MLB starter/sequencing/dispersion artifact must satisfy
before it can be selected by the governed team-event lane.

No sportsbook price or implied probability is accepted as a sporting-model
feature.  No helper in this module authorizes execution.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable, Mapping, Sequence

PATCH_ID = "LLP-PATCH-2026-09-05-MLB-STARTER-SEQUENCING-DISPERSION"
PATCH_VERSION = "v17"
MODEL_FAMILY = "MLB_STARTER_SEQUENCING_DISPERSION_V17"
FEATURE_SCHEMA_VERSION = "MLB_MSD_V17_FEATURES_V1"
CALIBRATION_VERSION = "MLB_MSD_V17_DYNAMIC_CALIBRATION_V1"
CAN_EXECUTE = False
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS = True

MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
MODEL_INPUTS_INSUFFICIENT = "MODEL_INPUTS_INSUFFICIENT"
MODEL_SCORER_FAILED = "MODEL_SCORER_FAILED"
MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
MODEL_RERUN_REQUIRED = "MODEL_RERUN_REQUIRED"
UNCALIBRATED_MODEL = "UNCALIBRATED_MODEL"

REQUIRED_STARTER_FEATURES = frozenset({
    "identity", "handedness", "confirmation_timestamp",
    "expected_pitch_count_innings_distribution", "xwoba_allowed",
    "xslg_allowed", "xba_allowed", "k_rate", "bb_rate", "whiff_rate",
    "chase_rate", "putaway_rate", "barrel_rate", "hard_hit_rate",
    "gb_rate", "fb_rate", "hr_contact_profile", "velocity_movement_flags",
    "rest_workload", "times_through_order_splits",
})
REQUIRED_OFFENSE_FEATURES = frozenset({
    "projected_batting_order", "batter_event_probabilities",
    "contact_quality_distribution", "platoon_composition",
    "bench_late_substitution_availability",
})
REQUIRED_CONTEXT_FEATURES = frozenset({
    "projected_lineup_handedness_availability", "park_weather_state",
    "bullpen_availability_leverage_workload",
})

FORBIDDEN_MARKET_FEATURE_TOKENS = (
    "sportsbook", "book_price", "market_price", "implied_probability",
    "market_probability", "no_vig", "odds", "moneyline_price", "kalshi_price",
)

REQUIRED_OUTPUTS = frozenset({
    # starter dispersion
    "starter_expected_runs", "starter_run_variance", "p_starter_runs_0_1",
    "p_starter_runs_2_3", "p_starter_runs_4_5", "p_starter_runs_6_plus",
    "expected_innings", "innings_variance", "p_third_time_through",
    "p_early_hook", "catastrophic_start_probability",
    # offensive sequencing
    "offense_expected_runs", "offense_run_variance", "p_offense_runs_0_2",
    "p_offense_runs_3_4", "p_offense_runs_5_plus", "p_three_plus_run_inning",
    "p_scoreless_first_5", "p_opponent_starter_exit_before_5",
    "sequencing_concentration_index",
    # interaction
    "starter_offense_cluster_interaction", "p_multi_run_inning_before_bullpen",
    "p_starter_4plus_given_lineup", "p_starter_6plus_given_lineup",
    "favorite_catastrophic_failure_probability",
    "underdog_offensive_breakthrough_probability",
    # bullpen
    "bullpen_expected_runs", "bullpen_run_variance", "bullpen_availability_score",
    "leverage_arm_availability", "p_bullpen_3plus_runs", "handoff_risk",
    "manager_hook_policy_feature",
    # full-game simulation
    "home_run_distribution", "away_run_distribution", "score_margin_distribution",
    "raw_home_win_probability", "raw_away_win_probability",
    "favorite_loss_path_probabilities", "upset_path_probabilities",
    # calibration/audit
    "raw_probability", "calibrated_probability", "lower_bound", "upper_bound",
    "calibration_method", "calibration_version", "model_version",
    "source_snapshot_id", "model_timestamp", "starter_dispersion_model_version",
    "sequencing_model_version", "bullpen_model_version", "simulation_version",
    "feature_snapshot_timestamp", "participant_snapshot_timestamp",
})

PROBABILITY_FIELDS = frozenset({
    "p_starter_runs_0_1", "p_starter_runs_2_3", "p_starter_runs_4_5",
    "p_starter_runs_6_plus", "p_third_time_through", "p_early_hook",
    "catastrophic_start_probability", "p_offense_runs_0_2",
    "p_offense_runs_3_4", "p_offense_runs_5_plus", "p_three_plus_run_inning",
    "p_scoreless_first_5", "p_opponent_starter_exit_before_5",
    "p_multi_run_inning_before_bullpen", "p_starter_4plus_given_lineup",
    "p_starter_6plus_given_lineup", "favorite_catastrophic_failure_probability",
    "underdog_offensive_breakthrough_probability", "bullpen_availability_score",
    "leverage_arm_availability", "p_bullpen_3plus_runs", "handoff_risk",
    "raw_home_win_probability", "raw_away_win_probability", "raw_probability",
    "calibrated_probability", "lower_bound", "upper_bound",
})


class MsdContractError(ValueError):
    """Raised only for deterministic V17 contract violations."""


@dataclass(frozen=True)
class FailureDecision:
    status: str
    missing_fields: tuple[str, ...] = ()
    preserve_sporting_probability: bool = False


def _present_keys(value: Mapping[str, Any] | None) -> set[str]:
    return set(value or {})


def missing_required_features(
    starter: Mapping[str, Any] | None,
    offense: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    missing: list[str] = []
    for scope, required, supplied in (
        ("starter", REQUIRED_STARTER_FEATURES, _present_keys(starter)),
        ("offense", REQUIRED_OFFENSE_FEATURES, _present_keys(offense)),
        ("context", REQUIRED_CONTEXT_FEATURES, _present_keys(context)),
    ):
        missing.extend(f"{scope}.{name}" for name in sorted(required - supplied))
    return tuple(missing)


def market_leakage_fields(feature_names: Iterable[str]) -> tuple[str, ...]:
    leaked = []
    for raw in feature_names:
        name = raw.lower()
        if any(token in name for token in FORBIDDEN_MARKET_FEATURE_TOKENS):
            leaked.append(raw)
    return tuple(sorted(set(leaked)))


def decide_failure_status(
    *,
    exact_artifact_available: bool,
    artifact_selected: bool,
    missing_fields: Sequence[str] = (),
    scorer_invoked: bool = False,
    scorer_failed: bool = False,
    output_invalid: bool = False,
    market_failed_after_probability: bool = False,
) -> FailureDecision:
    """Apply V17 typed model failure semantics in precedence order."""
    if not exact_artifact_available:
        return FailureDecision(MODEL_UNAVAILABLE)
    if artifact_selected and missing_fields:
        return FailureDecision(MODEL_INPUTS_INSUFFICIENT, tuple(missing_fields))
    if artifact_selected and scorer_invoked and scorer_failed:
        return FailureDecision(MODEL_SCORER_FAILED)
    if artifact_selected and output_invalid:
        return FailureDecision(MODEL_OUTPUT_INVALID)
    if market_failed_after_probability:
        return FailureDecision("MARKET_DATA_UNOBTAINABLE", preserve_sporting_probability=True)
    return FailureDecision("PASS", preserve_sporting_probability=True)


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _probability(value: Any) -> bool:
    return _finite_number(value) and 0.0 <= float(value) <= 1.0


def validate_probability_package(package: Mapping[str, Any], *, tolerance: float = 1e-6) -> None:
    """Fail closed on mean-only, malformed, non-finite, or inconsistent output."""
    missing = sorted(REQUIRED_OUTPUTS - set(package))
    if missing:
        raise MsdContractError(f"{MODEL_OUTPUT_INVALID}: missing_outputs={','.join(missing)}")

    for field in PROBABILITY_FIELDS:
        if not _probability(package[field]):
            raise MsdContractError(f"{MODEL_OUTPUT_INVALID}: invalid_probability={field}")

    numeric_nonprobability = (
        "starter_expected_runs", "starter_run_variance", "expected_innings",
        "innings_variance", "offense_expected_runs", "offense_run_variance",
        "sequencing_concentration_index", "starter_offense_cluster_interaction",
        "bullpen_expected_runs", "bullpen_run_variance", "manager_hook_policy_feature",
    )
    for field in numeric_nonprobability:
        if not _finite_number(package[field]):
            raise MsdContractError(f"{MODEL_OUTPUT_INVALID}: non_finite={field}")

    if abs(float(package["raw_home_win_probability"]) + float(package["raw_away_win_probability"]) - 1.0) > tolerance:
        raise MsdContractError(f"{MODEL_OUTPUT_INVALID}: probability_normalization")
    if float(package["lower_bound"]) > float(package["calibrated_probability"]):
        raise MsdContractError(f"{MODEL_OUTPUT_INVALID}: lower_bound_above_point")
    if float(package["calibrated_probability"]) > float(package["upper_bound"]):
        raise MsdContractError(f"{MODEL_OUTPUT_INVALID}: point_above_upper_bound")

    starter_bucket_sum = sum(float(package[k]) for k in (
        "p_starter_runs_0_1", "p_starter_runs_2_3", "p_starter_runs_4_5", "p_starter_runs_6_plus"
    ))
    offense_bucket_sum = sum(float(package[k]) for k in (
        "p_offense_runs_0_2", "p_offense_runs_3_4", "p_offense_runs_5_plus"
    ))
    if abs(starter_bucket_sum - 1.0) > tolerance:
        raise MsdContractError(f"{MODEL_OUTPUT_INVALID}: starter_bucket_normalization")
    if abs(offense_bucket_sum - 1.0) > tolerance:
        raise MsdContractError(f"{MODEL_OUTPUT_INVALID}: offense_bucket_normalization")


def calibration_width(package: Mapping[str, Any]) -> float:
    return float(package["upper_bound"]) - float(package["lower_bound"])


def lower_bound_rank_key(package: Mapping[str, Any]) -> float:
    return float(package["lower_bound"])


def assert_candidate_specific_uncertainty(packages: Sequence[Mapping[str, Any]], *, tolerance: float = 1e-12) -> None:
    """Reject a universal uncertainty adjustment across materially distinct candidates."""
    if len(packages) < 2:
        return
    widths = [round(calibration_width(p), 12) for p in packages]
    deltas = [round(float(p["raw_probability"]) - float(p["calibrated_probability"]), 12) for p in packages]
    if max(widths) - min(widths) <= tolerance and max(deltas) - min(deltas) <= tolerance:
        raise MsdContractError(f"{UNCALIBRATED_MODEL}: universal_uncertainty_adjustment")


def material_change_requires_rerun(previous: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    keys = (
        "starter_identity", "projected_lineup_hash", "park_weather_hash",
        "bullpen_availability_hash", "event_status",
    )
    return any(previous.get(k) != current.get(k) for k in keys)


def material_change_status(previous: Mapping[str, Any], current: Mapping[str, Any]) -> str:
    return MODEL_RERUN_REQUIRED if material_change_requires_rerun(previous, current) else "UNCHANGED"


def tail_risk_ordering(baseline: Mapping[str, Any], higher_dispersion: Mapping[str, Any]) -> bool:
    """Regression predicate for same-mean/higher-tail candidates."""
    return (
        math.isclose(float(baseline["starter_expected_runs"]), float(higher_dispersion["starter_expected_runs"]), rel_tol=0, abs_tol=1e-9)
        and float(higher_dispersion["p_starter_4plus_given_lineup"]) > float(baseline["p_starter_4plus_given_lineup"])
        and calibration_width(higher_dispersion) > calibration_width(baseline)
    )


def cluster_interaction_increases(baseline: Mapping[str, Any], challenger: Mapping[str, Any]) -> bool:
    return (
        float(challenger["starter_offense_cluster_interaction"]) > float(baseline["starter_offense_cluster_interaction"])
        and float(challenger["p_three_plus_run_inning"]) > float(baseline["p_three_plus_run_inning"])
    )


def handoff_risk_increases(baseline: Mapping[str, Any], challenger: Mapping[str, Any]) -> bool:
    return (
        float(challenger["handoff_risk"]) > float(baseline["handoff_risk"])
        and float(challenger["bullpen_run_variance"]) > float(baseline["bullpen_run_variance"])
    )


def promotion_allowed(champion: Mapping[str, float], challenger: Mapping[str, float]) -> bool:
    """Calibration-first promotion gate; win rate/ROI are intentionally ignored."""
    required = ("brier_score", "log_loss", "calibration_slope", "calibration_intercept")
    if any(k not in champion or k not in challenger for k in required):
        return False
    return (
        challenger["brier_score"] <= champion["brier_score"]
        and challenger["log_loss"] <= champion["log_loss"]
        and abs(challenger["calibration_slope"] - 1.0) <= abs(champion["calibration_slope"] - 1.0)
        and abs(challenger["calibration_intercept"]) <= abs(champion["calibration_intercept"])
    )


def audit_feature_names(feature_names: Iterable[str]) -> None:
    leaked = market_leakage_fields(feature_names)
    if leaked:
        raise MsdContractError(f"GOVERNANCE_MARKET_LEAKAGE: {','.join(leaked)}")


def preserve_probability_on_market_failure(package: Mapping[str, Any]) -> dict[str, Any]:
    """Keep sporting probability while explicitly blocking market/value publication."""
    return {
        "raw_probability": package.get("raw_probability"),
        "calibrated_probability": package.get("calibrated_probability"),
        "lower_bound": package.get("lower_bound"),
        "upper_bound": package.get("upper_bound"),
        "probability_status": "PASS",
        "market_value_status": "MARKET_DATA_UNOBTAINABLE",
        "can_execute": False,
    }
