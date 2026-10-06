"""Research-only MLB pitcher-strikeout feature-local lower-bound challenger.

Class C experiment for #1391.

This module does not score the sporting distribution. It consumes the governed
MLB pitcher-strikeout specialist's already-computed selected-side probability
and immutable pregame evidence, then estimates an empirical one-sided decision
bound from strictly prior settled observations with similar model-internal
features.

Governance:
- no market/implied-probability substitution
- no production threshold mutation
- probability_publishable = False
- promotion_authorized = False
- rank_eligible = False
- can_execute = False
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite, sqrt
import random
from typing import Any, Mapping, Sequence

EXPERIMENT_VERSION = "MLB_K_FEATURE_LOCAL_BOUND_V1"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
PROMOTION_AUTHORIZED = False
RANK_ELIGIBLE = False
MARKET_PROBABILITY_SUBSTITUTION_ALLOWED = False

SPORT = "MLB"
STAT_TYPE = "PITCHER_STRIKEOUTS"
DEFAULT_SHORTENED_OUTS_THRESHOLD = 15


class MLBKFeatureLocalError(ValueError):
    """Typed fail-closed error for feature-local research inputs."""


@dataclass(frozen=True)
class MLBKFeatureVector:
    prior_so_per_out: float
    prior_shortened_rate: float
    line: float
    opponent_k_rate_per_pa: float | None
    prior_start_n: int


@dataclass(frozen=True)
class FeatureLocalPolicy:
    name: str = "FEATURE_LOCAL_WILSON90_BLOCK_Q10_V1"
    min_effective_n: int = 30
    max_neighbors: int = 50
    max_line_distance: float = 0.0
    max_standardized_distance: float = 2.0
    use_opponent_k_rate: bool = False
    wilson_z: float = 1.645
    bootstrap_draws: int = 2000
    bootstrap_quantile: float = 0.10
    min_blocks: int = 10


@dataclass(frozen=True)
class FeatureLocalBound:
    lane_key: str
    point_probability: float
    local_wilson_lower: float
    block_bootstrap_q10_lower: float
    composite_lower_bound: float
    sample_n: int
    block_n: int
    max_feature_distance_used: float
    max_line_distance_used: float
    feature_scales: Mapping[str, float]
    included_opponent_context_n: int
    excluded_push_n: int
    policy_name: str


def _aware_dt(value: Any, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise MLBKFeatureLocalError(f"{field}_TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MLBKFeatureLocalError(f"{field}_TIMESTAMP_INVALID")
    return parsed


def _finite(value: Any, *, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise MLBKFeatureLocalError(f"{field}_INVALID") from exc
    if not isfinite(parsed):
        raise MLBKFeatureLocalError(f"{field}_INVALID")
    return parsed


def _probability(value: Any, *, field: str) -> float:
    parsed = _finite(value, field=field)
    if not 0.0 <= parsed <= 1.0:
        raise MLBKFeatureLocalError(f"{field}_OUT_OF_RANGE")
    return parsed


def _direction(value: Any) -> str:
    direction = str(value or "").strip().upper()
    if direction not in {"MORE", "LESS"}:
        raise MLBKFeatureLocalError("DIRECTION_INVALID")
    return direction


def _model_family(value: Any) -> str:
    family = str(value or "").strip()
    if not family:
        raise MLBKFeatureLocalError("MODEL_FAMILY_MISSING")
    return family


def _market_independent(row: Mapping[str, Any]) -> bool:
    if bool(row.get("market_probability_substitution_used", False)):
        return False
    weight = row.get("market_prior_weight")
    if weight is None:
        return False
    try:
        return abs(float(weight)) <= 1e-15
    except (TypeError, ValueError):
        return False


def _outcome(value: Any) -> int | None:
    """Mirror the current governed V17 prop calibration contract.

    The production candidate-calibrator and MLB-K calibration-health paths
    exclude PUSH rows from their binary hit/loss cohorts. This research
    challenger must reproduce that contract for apples-to-apples comparison.
    Issue #1425 separately evaluates whether the raw selected-side PMF mass
    should instead be renormalized or calibrated as a three-way outcome.
    """
    if value in (1, True):
        return 1
    if value in (0, False):
        return 0
    text = str(value or "").strip().upper()
    if text in {"WIN", "HIT"}:
        return 1
    if text in {"LOSS", "MISS"}:
        return 0
    if text == "PUSH":
        return None
    raise MLBKFeatureLocalError("SETTLED_OUTCOME_INVALID")


def _sample_std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return sqrt(max(0.0, variance))


def _normalized_delta(left: float, right: float, scale: float) -> float:
    if scale <= 1e-12:
        return 0.0 if abs(left - right) <= 1e-12 else float("inf")
    return (left - right) / scale


def _wilson_lower(successes: int, n: int, *, z: float) -> float:
    if n <= 0:
        raise MLBKFeatureLocalError("WILSON_SAMPLE_EMPTY")
    if successes < 0 or successes > n:
        raise MLBKFeatureLocalError("WILSON_SUCCESS_COUNT_INVALID")
    if not isfinite(float(z)) or float(z) <= 0.0:
        raise MLBKFeatureLocalError("WILSON_Z_INVALID")
    p = successes / n
    zz = float(z) ** 2
    denom = 1.0 + zz / n
    center = p + zz / (2.0 * n)
    radius = float(z) * sqrt((p * (1.0 - p) / n) + (zz / (4.0 * n * n)))
    return max(0.0, min(1.0, (center - radius) / denom))


def _empirical_quantile(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise MLBKFeatureLocalError("BOOTSTRAP_SAMPLE_EMPTY")
    if not 0.0 <= quantile <= 1.0:
        raise MLBKFeatureLocalError("BOOTSTRAP_QUANTILE_INVALID")
    ordered = sorted(float(value) for value in values)
    index = int(quantile * (len(ordered) - 1))
    return ordered[index]


def extract_feature_vector(
    *,
    game_log: Any,
    box_score_log: Any,
    line: Any,
    opponent_context: Any = None,
    shortened_outs_threshold: int = DEFAULT_SHORTENED_OUTS_THRESHOLD,
    min_prior_starts: int = 10,
) -> MLBKFeatureVector:
    if not isinstance(game_log, list) or not isinstance(box_score_log, list):
        raise MLBKFeatureLocalError("FEATURE_HISTORY_REQUIRED")
    if len(game_log) != len(box_score_log):
        raise MLBKFeatureLocalError("FEATURE_HISTORY_MISALIGNED")
    if len(game_log) < int(min_prior_starts):
        raise MLBKFeatureLocalError("FEATURE_HISTORY_TOO_SHORT")
    if int(shortened_outs_threshold) <= 0:
        raise MLBKFeatureLocalError("SHORTENED_OUTS_THRESHOLD_INVALID")

    strikeouts = [_finite(value, field="GAME_LOG_STRIKEOUTS") for value in game_log]
    outs: list[float] = []
    for entry in box_score_log:
        if not isinstance(entry, Mapping):
            raise MLBKFeatureLocalError("BOX_SCORE_LOG_ENTRY_INVALID")
        out = _finite(entry.get("outs"), field="BOX_SCORE_OUTS")
        if out < 0.0:
            raise MLBKFeatureLocalError("BOX_SCORE_OUTS_INVALID")
        outs.append(out)

    total_outs = sum(outs)
    if total_outs <= 0.0:
        raise MLBKFeatureLocalError("ZERO_TOTAL_PRIOR_OUTS")

    prior_so_per_out = sum(strikeouts) / total_outs
    prior_shortened_rate = (
        sum(1 for out in outs if out < int(shortened_outs_threshold)) / len(outs)
    )
    parsed_line = _finite(line, field="LINE")
    if parsed_line < 0.0:
        raise MLBKFeatureLocalError("LINE_INVALID")

    opponent_k_rate = None
    if isinstance(opponent_context, Mapping) and opponent_context.get("k_rate_per_pa") is not None:
        opponent_k_rate = _finite(
            opponent_context.get("k_rate_per_pa"),
            field="OPPONENT_K_RATE_PER_PA",
        )
        if not 0.0 <= opponent_k_rate <= 1.0:
            raise MLBKFeatureLocalError("OPPONENT_K_RATE_PER_PA_OUT_OF_RANGE")

    return MLBKFeatureVector(
        prior_so_per_out=prior_so_per_out,
        prior_shortened_rate=prior_shortened_rate,
        line=parsed_line,
        opponent_k_rate_per_pa=opponent_k_rate,
        prior_start_n=len(game_log),
    )


def _policy_validate(policy: FeatureLocalPolicy) -> None:
    if policy.min_effective_n < 1:
        raise MLBKFeatureLocalError("POLICY_MIN_EFFECTIVE_N_INVALID")
    if policy.max_neighbors < policy.min_effective_n:
        raise MLBKFeatureLocalError("POLICY_MAX_NEIGHBORS_INVALID")
    if policy.max_line_distance < 0.0:
        raise MLBKFeatureLocalError("POLICY_MAX_LINE_DISTANCE_INVALID")
    if policy.max_standardized_distance <= 0.0:
        raise MLBKFeatureLocalError("POLICY_MAX_STANDARDIZED_DISTANCE_INVALID")
    if policy.bootstrap_draws < 2000:
        raise MLBKFeatureLocalError("POLICY_BOOTSTRAP_DRAWS_LT_2000")
    if not 0.0 < policy.bootstrap_quantile < 0.5:
        raise MLBKFeatureLocalError("POLICY_BOOTSTRAP_QUANTILE_INVALID")
    if policy.min_blocks < 2:
        raise MLBKFeatureLocalError("POLICY_MIN_BLOCKS_INVALID")


def _history_feature(row: Mapping[str, Any]) -> MLBKFeatureVector:
    return extract_feature_vector(
        game_log=row.get("game_log"),
        box_score_log=row.get("box_score_log"),
        line=row.get("line"),
        opponent_context=row.get("opponent_context"),
    )


def feature_local_bound(
    *,
    candidate: Mapping[str, Any],
    historical_rows: Sequence[Mapping[str, Any]],
    candidate_as_of: str,
    policy: FeatureLocalPolicy = FeatureLocalPolicy(),
    random_seed: int = 17,
) -> FeatureLocalBound:
    """Build a feature-local empirical lower bound from prior settled rows only."""
    _policy_validate(policy)
    as_of = _aware_dt(candidate_as_of, field="CANDIDATE_AS_OF")
    direction = _direction(candidate.get("direction"))
    family = _model_family(candidate.get("model_family"))
    lane_key = str(candidate.get("lane_key") or "").strip()
    if not lane_key:
        raise MLBKFeatureLocalError("LANE_KEY_MISSING")
    point = _probability(candidate.get("calibrated_probability"), field="CALIBRATED_PROBABILITY")
    if not _market_independent(candidate):
        raise MLBKFeatureLocalError("MARKET_PROBABILITY_SUBSTITUTION_FORBIDDEN")

    candidate_features = _history_feature(candidate)
    if policy.use_opponent_k_rate and candidate_features.opponent_k_rate_per_pa is None:
        raise MLBKFeatureLocalError("CANDIDATE_OPPONENT_CONTEXT_REQUIRED_BY_POLICY")

    eligible: list[dict[str, Any]] = []
    excluded_push_n = 0
    for row in historical_rows:
        if bool(row.get("void", False)):
            continue
        if not _market_independent(row):
            continue
        try:
            row_direction = _direction(row.get("direction"))
            row_family = _model_family(row.get("model_family"))
        except MLBKFeatureLocalError:
            continue
        if row_direction != direction or row_family != family:
            continue

        prediction_at = _aware_dt(row.get("prediction_timestamp"), field="HISTORY_PREDICTION")
        outcome_at = _aware_dt(row.get("outcome_available_at"), field="HISTORY_OUTCOME")
        if prediction_at >= as_of or outcome_at >= as_of:
            continue
        if outcome_at < prediction_at:
            raise MLBKFeatureLocalError("HISTORY_OUTCOME_PRECEDES_PREDICTION")

        features = _history_feature(row)
        line_distance = abs(features.line - candidate_features.line)
        if line_distance > policy.max_line_distance + 1e-12:
            continue
        if policy.use_opponent_k_rate and features.opponent_k_rate_per_pa is None:
            continue

        event_id = str(row.get("event_id") or "").strip()
        if not event_id:
            raise MLBKFeatureLocalError("HISTORY_EVENT_ID_MISSING")

        outcome = _outcome(row.get("outcome"))
        if outcome is None:
            excluded_push_n += 1
            continue

        eligible.append(
            {
                "features": features,
                "outcome": outcome,
                "event_id": event_id,
                "point_probability": _probability(
                    row.get("calibrated_probability"),
                    field="HISTORY_CALIBRATED_PROBABILITY",
                ),
                "line_distance": line_distance,
            }
        )

    if len(eligible) < policy.min_effective_n:
        raise MLBKFeatureLocalError(
            f"FEATURE_LOCAL_SUPPORT_INSUFFICIENT:{len(eligible)}:{policy.min_effective_n}"
        )

    scale_so = _sample_std([item["features"].prior_so_per_out for item in eligible])
    scale_short = _sample_std([item["features"].prior_shortened_rate for item in eligible])
    scales: dict[str, float] = {
        "prior_so_per_out": scale_so,
        "prior_shortened_rate": scale_short,
    }

    scale_opp = 0.0
    if policy.use_opponent_k_rate:
        opp_values = [
            item["features"].opponent_k_rate_per_pa
            for item in eligible
            if item["features"].opponent_k_rate_per_pa is not None
        ]
        scale_opp = _sample_std([float(value) for value in opp_values])
        scales["opponent_k_rate_per_pa"] = scale_opp

    ranked: list[dict[str, Any]] = []
    for item in eligible:
        feature = item["features"]
        deltas = [
            _normalized_delta(
                feature.prior_so_per_out,
                candidate_features.prior_so_per_out,
                scale_so,
            ),
            _normalized_delta(
                feature.prior_shortened_rate,
                candidate_features.prior_shortened_rate,
                scale_short,
            ),
        ]
        if policy.use_opponent_k_rate:
            assert feature.opponent_k_rate_per_pa is not None
            assert candidate_features.opponent_k_rate_per_pa is not None
            deltas.append(
                _normalized_delta(
                    feature.opponent_k_rate_per_pa,
                    candidate_features.opponent_k_rate_per_pa,
                    scale_opp,
                )
            )
        if any(not isfinite(delta) for delta in deltas):
            continue
        distance = sqrt(sum(delta * delta for delta in deltas) / len(deltas))
        if distance > policy.max_standardized_distance:
            continue
        ranked.append(
            {
                **item,
                "feature_distance": distance,
                "probability_distance": abs(item["point_probability"] - point),
            }
        )

    ranked.sort(
        key=lambda item: (
            item["feature_distance"],
            item["line_distance"],
            item["probability_distance"],
            item["event_id"],
        )
    )
    selected = ranked[: policy.max_neighbors]
    if len(selected) < policy.min_effective_n:
        raise MLBKFeatureLocalError(
            f"FEATURE_LOCAL_SUPPORT_INSUFFICIENT_AFTER_DISTANCE:"
            f"{len(selected)}:{policy.min_effective_n}"
        )

    successes = sum(int(item["outcome"]) for item in selected)
    wilson = min(point, _wilson_lower(successes, len(selected), z=policy.wilson_z))

    blocks: dict[str, list[int]] = {}
    for item in selected:
        blocks.setdefault(item["event_id"], []).append(int(item["outcome"]))
    if len(blocks) < policy.min_blocks:
        raise MLBKFeatureLocalError(
            f"FEATURE_LOCAL_BLOCK_SUPPORT_INSUFFICIENT:{len(blocks)}:{policy.min_blocks}"
        )

    block_values = list(blocks.values())
    rng = random.Random(int(random_seed))
    bootstrap_rates: list[float] = []
    for _ in range(policy.bootstrap_draws):
        sampled: list[int] = []
        for _block_index in range(len(block_values)):
            sampled.extend(block_values[rng.randrange(len(block_values))])
        if not sampled:
            raise MLBKFeatureLocalError("BOOTSTRAP_RESAMPLE_EMPTY")
        bootstrap_rates.append(sum(sampled) / len(sampled))

    bootstrap_q = min(
        point,
        _empirical_quantile(bootstrap_rates, policy.bootstrap_quantile),
    )
    composite = min(point, wilson, bootstrap_q)

    return FeatureLocalBound(
        lane_key=lane_key,
        point_probability=point,
        local_wilson_lower=wilson,
        block_bootstrap_q10_lower=bootstrap_q,
        composite_lower_bound=composite,
        sample_n=len(selected),
        block_n=len(blocks),
        max_feature_distance_used=max(item["feature_distance"] for item in selected),
        max_line_distance_used=max(item["line_distance"] for item in selected),
        feature_scales=scales,
        included_opponent_context_n=sum(
            1
            for item in selected
            if item["features"].opponent_k_rate_per_pa is not None
        ),
        excluded_push_n=excluded_push_n,
        policy_name=policy.name,
    )


def research_receipt(bound: FeatureLocalBound) -> dict[str, Any]:
    return {
        "experiment_version": EXPERIMENT_VERSION,
        "lane_key": bound.lane_key,
        "point_probability": bound.point_probability,
        "local_wilson_lower": bound.local_wilson_lower,
        "block_bootstrap_q10_lower": bound.block_bootstrap_q10_lower,
        "composite_lower_bound": bound.composite_lower_bound,
        "sample_n": bound.sample_n,
        "block_n": bound.block_n,
        "max_feature_distance_used": bound.max_feature_distance_used,
        "max_line_distance_used": bound.max_line_distance_used,
        "feature_scales": dict(bound.feature_scales),
        "included_opponent_context_n": bound.included_opponent_context_n,
        "excluded_push_n": bound.excluded_push_n,
        "binary_push_semantics": "CURRENT_GOVERNED_CONTRACT_PUSH_EXCLUDED",
        "probability_target_experiment_issue": 1425,
        "policy_name": bound.policy_name,
        "market_probability_substitution_allowed": False,
        "probability_publishable": False,
        "promotion_authorized": False,
        "rank_eligible": False,
        "terminal_authority": TERMINAL_AUTHORITY,
        "can_execute": False,
        "status": "EXPERIMENT_CREATED",
    }


__all__ = [
    "CAN_EXECUTE",
    "EXPERIMENT_VERSION",
    "FeatureLocalBound",
    "FeatureLocalPolicy",
    "MARKET_PROBABILITY_SUBSTITUTION_ALLOWED",
    "MLBKFeatureLocalError",
    "MLBKFeatureVector",
    "PROBABILITY_PUBLISHABLE",
    "PROMOTION_AUTHORIZED",
    "RANK_ELIGIBLE",
    "TERMINAL_AUTHORITY",
    "extract_feature_vector",
    "feature_local_bound",
    "research_receipt",
]
