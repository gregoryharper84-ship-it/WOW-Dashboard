"""WNBA composite/PRA fitted candidate built from the existing joint component residual artifact.

This adapter is intentionally candidate-only. It reuses the WNBA Fantasy Score
candidate's strictly-prior joint residual vectors for points/rebounds/assists, so
within-player component dependence is preserved. It never multiplies independent
component probabilities and never claims governed V17 publication authority.

Calibration, lower-bound certification, forward-cohort review, exact runtime
registration, and canonical Action canary remain required before promotion.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from statistics import mean, median, pstdev
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
CONTROLLING_SPECIALIST = "wow.wnba-composite-prop-expert"
MODEL_FAMILY = "WNBA_COMPOSITE_EMPIRICAL_RESIDUAL_JOINT_V1"
MIN_STANDARD_SIMULATIONS = 50_000

COMPONENTS_BY_STAT = {
    "PRA": ("points", "rebounds", "assists"),
    "POINTS_REBOUNDS": ("points", "rebounds"),
    "POINTS_ASSISTS": ("points", "assists"),
    "REBOUNDS_ASSISTS": ("rebounds", "assists"),
}

STAT_ALIASES = {
    "PRA": "PRA",
    "PTS+REB+AST": "PRA",
    "POINTS+REBOUNDS+ASSISTS": "PRA",
    "POINTS_REBOUNDS_ASSISTS": "PRA",
    "PTS_REB_AST": "PRA",
    "POINTS_REBOUNDS": "POINTS_REBOUNDS",
    "PTS+REB": "POINTS_REBOUNDS",
    "POINTS+REBOUNDS": "POINTS_REBOUNDS",
    "PTS_REB": "POINTS_REBOUNDS",
    "POINTS_ASSISTS": "POINTS_ASSISTS",
    "PTS+AST": "POINTS_ASSISTS",
    "POINTS+ASSISTS": "POINTS_ASSISTS",
    "PTS_AST": "POINTS_ASSISTS",
    "REBOUNDS_ASSISTS": "REBOUNDS_ASSISTS",
    "REB+AST": "REBOUNDS_ASSISTS",
    "REBOUNDS+ASSISTS": "REBOUNDS_ASSISTS",
    "REB_AST": "REBOUNDS_ASSISTS",
}

FAILURE_REGIMES = frozenset({
    "NORMAL_ROLE",
    "LIMITED_MINUTES",
    "FOUL_TROUBLE",
    "BLOWOUT_REDUCTION",
    "USAGE_COMPRESSION",
    "INJURY_OR_EARLY_EXIT",
    "PACE_COLLAPSE",
    "ROLE_EXPANSION",
    "ROLE_REDUCTION",
})


class WNBACompositeCandidateError(ValueError):
    """Deterministic validation failure for the nonpublishable candidate lane."""


def _f(value: Any, *, field: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise WNBACompositeCandidateError(f"{field} must be numeric") from exc
    if not math.isfinite(out):
        raise WNBACompositeCandidateError(f"{field} must be finite")
    return out


def canonical_stat(value: str) -> str:
    raw = "_".join(str(value or "").strip().upper().replace("-", " ").split())
    stat = STAT_ALIASES.get(raw)
    if stat is None:
        raise WNBACompositeCandidateError(f"unsupported WNBA composite stat: {raw or '<missing>'}")
    return stat


def _sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _trim_components(values: Mapping[str, Any], components: Sequence[str]) -> dict[str, float]:
    return {name: _f(values.get(name, 0.0), field=name) for name in components}


def derive_candidate_payload(
    source_payload: Mapping[str, Any],
    *,
    source_model_artifact_version: str,
    source_artifact_checksum: str,
    source_training_dataset_hash: str,
    source_training_code_sha: str,
) -> dict[str, Any]:
    """Derive a composite candidate artifact from the existing joint residual source."""
    lane = str(source_payload.get("lane") or "").strip().upper()
    if lane != "WNBA":
        raise WNBACompositeCandidateError("source artifact must be the WNBA joint component candidate")

    player_means_raw = source_payload.get("player_means")
    cohort_means_raw = source_payload.get("cohort_means")
    residuals_raw = source_payload.get("residual_vectors")
    fit_contract = source_payload.get("fit_contract")
    if not isinstance(player_means_raw, Mapping) or not player_means_raw:
        raise WNBACompositeCandidateError("source artifact missing player_means")
    if not isinstance(cohort_means_raw, Mapping) or not cohort_means_raw:
        raise WNBACompositeCandidateError("source artifact missing cohort_means")
    if not isinstance(residuals_raw, Mapping) or not residuals_raw:
        raise WNBACompositeCandidateError("source artifact missing joint residual_vectors")
    if not isinstance(fit_contract, Mapping):
        raise WNBACompositeCandidateError("source artifact missing fit_contract")
    if fit_contract.get("whole_event_chronological_split") is not True:
        raise WNBACompositeCandidateError("source artifact must use whole-event chronological split")
    if fit_contract.get("strictly_prior_residuals") is not True:
        raise WNBACompositeCandidateError("source artifact residuals must be strictly prior")

    components = COMPONENTS_BY_STAT["PRA"]
    player_means: dict[str, dict[str, float]] = {}
    for player, values in player_means_raw.items():
        if isinstance(values, Mapping):
            player_means[str(player)] = _trim_components(values, components)
    cohort_means: dict[str, dict[str, float]] = {}
    for cohort, values in cohort_means_raw.items():
        if isinstance(values, Mapping):
            cohort_means[str(cohort)] = _trim_components(values, components)

    residual_vectors: dict[str, list[dict[str, float]]] = {}
    for cohort, rows in residuals_raw.items():
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            continue
        cleaned: list[dict[str, float]] = []
        for row in rows:
            if isinstance(row, Mapping):
                cleaned.append(_trim_components(row, components))
        if cleaned:
            residual_vectors[str(cohort)] = cleaned

    if not player_means or not cohort_means or not residual_vectors:
        raise WNBACompositeCandidateError("source artifact cannot produce a complete P/R/A candidate")

    payload = {
        "model_family": MODEL_FAMILY,
        "sport": "WNBA",
        "supported_stats": sorted(COMPONENTS_BY_STAT),
        "controlling_specialist": CONTROLLING_SPECIALIST,
        "component_model": "JOINT_EMPIRICAL_RESIDUAL_BOOTSTRAP",
        "components": list(components),
        "player_means": player_means,
        "cohort_means": cohort_means,
        "residual_vectors": residual_vectors,
        "fit_contract": dict(fit_contract),
        "source_artifact": {
            "model_artifact_version": str(source_model_artifact_version),
            "artifact_checksum": str(source_artifact_checksum),
            "training_dataset_hash": str(source_training_dataset_hash),
            "training_code_sha": str(source_training_code_sha),
        },
        "independent_component_multiplication_permitted": False,
        "calibration_status": "BLOCKED_NO_PRA_EXACT_LINE_CALIBRATION_ARTIFACT",
        "certification_status": "CANDIDATE_ONLY",
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }
    payload["candidate_payload_sha256"] = _sha256(payload)
    return payload


def _validate_regimes(regimes: Sequence[Mapping[str, Any]] | None) -> tuple[tuple[dict[str, Any], ...], bool]:
    if not regimes:
        return (({
            "name": "NORMAL_ROLE",
            "probability": 1.0,
            "component_multipliers": {},
            "certified": False,
        },), False)
    total = 0.0
    all_certified = True
    out: list[dict[str, Any]] = []
    for regime in regimes:
        name = str(regime.get("name") or "").strip().upper()
        if name not in FAILURE_REGIMES:
            raise WNBACompositeCandidateError(f"unknown WNBA composite failure regime: {name or '<missing>'}")
        probability = _f(regime.get("probability"), field="regime probability")
        if not 0.0 <= probability <= 1.0:
            raise WNBACompositeCandidateError("regime probabilities must be in [0,1]")
        multipliers = regime.get("component_multipliers") or {}
        if not isinstance(multipliers, Mapping):
            raise WNBACompositeCandidateError("component_multipliers must be a mapping")
        cleaned: dict[str, float] = {}
        for component, multiplier in multipliers.items():
            if component not in COMPONENTS_BY_STAT["PRA"]:
                raise WNBACompositeCandidateError(f"unknown component multiplier: {component}")
            value = _f(multiplier, field=f"multiplier:{component}")
            if value < 0:
                raise WNBACompositeCandidateError("component multipliers must be non-negative")
            cleaned[str(component)] = value
        certified = regime.get("certified") is True
        all_certified = all_certified and certified
        total += probability
        out.append({
            "name": name,
            "probability": probability,
            "component_multipliers": cleaned,
            "certified": certified,
        })
    if abs(total - 1.0) > 1e-8:
        raise WNBACompositeCandidateError("regime probabilities must sum to 1.0")
    return tuple(out), all_certified


def _pick_regime(rng: random.Random, regimes: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    needle = rng.random()
    cumulative = 0.0
    for regime in regimes:
        cumulative += float(regime["probability"])
        if needle <= cumulative:
            return regime
    return regimes[-1]


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * min(1.0, max(0.0, q))
    lo = int(math.floor(index))
    hi = int(math.ceil(index))
    if lo == hi:
        return float(ordered[lo])
    weight = index - lo
    return float(ordered[lo] * (1.0 - weight) + ordered[hi] * weight)


def simulate_exact_line(
    candidate_payload: Mapping[str, Any],
    *,
    player: str,
    stat_type: str,
    exact_line: float,
    side: str,
    simulation_count: int = MIN_STANDARD_SIMULATIONS,
    seed: int = 17,
    opportunity_context: Mapping[str, Any] | None = None,
    regimes: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score a WNBA composite exact line as a nonpublishable fitted candidate."""
    if str(candidate_payload.get("model_family") or "") != MODEL_FAMILY:
        raise WNBACompositeCandidateError("candidate payload model_family mismatch")
    if candidate_payload.get("independent_component_multiplication_permitted") is not False:
        raise WNBACompositeCandidateError("joint-model independence guard missing")
    if simulation_count < MIN_STANDARD_SIMULATIONS:
        raise WNBACompositeCandidateError(
            f"WNBA composite candidate requires at least {MIN_STANDARD_SIMULATIONS} simulations"
        )

    stat = canonical_stat(stat_type)
    components = COMPONENTS_BY_STAT[stat]
    side_n = str(side or "").strip().upper()
    if side_n not in {"MORE", "LESS"}:
        raise WNBACompositeCandidateError("side must be MORE or LESS")
    line = _f(exact_line, field="exact_line")
    player_key = str(player or "").strip()
    if not player_key:
        raise WNBACompositeCandidateError("player identity is required")

    player_means = candidate_payload.get("player_means")
    cohort_means = candidate_payload.get("cohort_means")
    residual_vectors = candidate_payload.get("residual_vectors")
    if not isinstance(player_means, Mapping) or not isinstance(cohort_means, Mapping):
        raise WNBACompositeCandidateError("candidate payload missing fitted means")
    if not isinstance(residual_vectors, Mapping) or not residual_vectors:
        raise WNBACompositeCandidateError("candidate payload missing residual vectors")

    cohort_key = "WNBA" if "WNBA" in cohort_means else next(iter(cohort_means), "")
    means_raw = player_means.get(player_key) or cohort_means.get(cohort_key)
    if not isinstance(means_raw, Mapping):
        raise WNBACompositeCandidateError(f"no fitted baseline for player {player_key}")
    means = _trim_components(means_raw, COMPONENTS_BY_STAT["PRA"])

    context = opportunity_context or {}
    explicit = context.get("component_means")
    if isinstance(explicit, Mapping):
        for name in COMPONENTS_BY_STAT["PRA"]:
            if explicit.get(name) is not None:
                means[name] = _f(explicit[name], field=f"component_mean:{name}")
    full_context_ready = bool(context.get("certified")) and all(
        isinstance(context.get(key), Mapping)
        for key in ("minutes_model", "pace_model", "usage_model")
    )

    residual_library = residual_vectors.get(cohort_key)
    if residual_library is None and len(residual_vectors) == 1:
        residual_library = next(iter(residual_vectors.values()))
    if not isinstance(residual_library, Sequence) or not residual_library:
        raise WNBACompositeCandidateError("no joint residual library for WNBA cohort")

    regime_set, regimes_certified = _validate_regimes(regimes)
    rng = random.Random(seed)
    scores: list[float] = []
    component_totals = {name: 0.0 for name in COMPONENTS_BY_STAT["PRA"]}
    wins_by_dominant = {name: 0 for name in components}
    more_wins = 0

    for _ in range(simulation_count):
        residual = residual_library[rng.randrange(len(residual_library))]
        if not isinstance(residual, Mapping):
            raise WNBACompositeCandidateError("joint residual row must be a mapping")
        regime = _pick_regime(rng, regime_set)
        multipliers = regime.get("component_multipliers") or {}
        draw: dict[str, float] = {}
        for name in COMPONENTS_BY_STAT["PRA"]:
            value = max(
                0.0,
                means[name] + _f(residual.get(name, 0.0), field=f"residual:{name}"),
            )
            value *= _f(multipliers.get(name, 1.0), field=f"multiplier:{name}")
            value = float(max(0, int(round(value))))
            draw[name] = value
            component_totals[name] += value
        score = sum(draw[name] for name in components)
        scores.append(score)
        if score > line:
            more_wins += 1
            dominant = max(components, key=lambda name: draw[name])
            wins_by_dominant[dominant] += 1

    more = more_wins / simulation_count
    less = sum(score < line for score in scores) / simulation_count
    push = max(0.0, 1.0 - more - less)
    requested = more if side_n == "MORE" else less

    blockers = [
        "WNBA_COMPOSITE_CALIBRATION_NOT_CERTIFIED",
        "WNBA_COMPOSITE_CANDIDATE_NOT_CERTIFIED",
    ]
    if not full_context_ready:
        blockers.append("WNBA_COMPOSITE_FULL_MODEL_CONTEXT_NOT_CERTIFIED")
    if not regimes_certified:
        blockers.append("FAILURE_REGIME_PACKAGE_NOT_CERTIFIED")

    denominator = more_wins or 1
    return {
        "sport": "WNBA",
        "stat_type": stat,
        "market_family": f"WNBA_{stat}",
        "controlling_specialist": CONTROLLING_SPECIALIST,
        "model_family": MODEL_FAMILY,
        "model_source": candidate_payload.get("source_artifact"),
        "exact_line": line,
        "side": side_n,
        "simulation_count": simulation_count,
        "seed": seed,
        "raw_candidate_probability": requested,
        "P(MORE)": more,
        "P(LESS)": less,
        "P(PUSH)": push,
        "calibrated_probability": None,
        "calibrated_lower_bound": None,
        "calibrated_upper_bound": None,
        "projection_mean": mean(scores),
        "projection_median": median(scores),
        "projection_std": pstdev(scores),
        "projection_p10": _quantile(scores, 0.10),
        "projection_p90": _quantile(scores, 0.90),
        "component_means": {
            name: component_totals[name] / simulation_count
            for name in COMPONENTS_BY_STAT["PRA"]
        },
        "dominant_more_win_share": {
            name: wins_by_dominant.get(name, 0) / denominator
            for name in components
        },
        "joint_residual_sampling": True,
        "independent_component_multiplication_used": False,
        "terminal_status": "MODEL_QUALIFIED_HOLD",
        "blockers": blockers,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "COMPONENTS_BY_STAT",
    "CONTROLLING_SPECIALIST",
    "MIN_STANDARD_SIMULATIONS",
    "MODEL_FAMILY",
    "WNBACompositeCandidateError",
    "canonical_stat",
    "derive_candidate_payload",
    "simulate_exact_line",
]
