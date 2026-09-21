"""NBA scalar/composite fitted candidate derived from the frozen NBA joint residual artifact.

This lane is research-only.  It reuses the existing NBA Fantasy Score candidate's
strictly-prior joint component residuals for points/rebounds/assists, preserving
within-player dependence instead of multiplying independent component probabilities.

No function in this module certifies, promotes, publishes, ranks, prices, or executes.
Exact-line forward calibration, independent certification, production registration,
and a canonical Action canary remain mandatory before any route can graduate.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from statistics import mean, median, pstdev
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
CONTROLLING_SPECIALIST = "wow.nba-player-prop-probability-expert"
MODEL_FAMILY = "NBA_PLAYER_PROP_EMPIRICAL_RESIDUAL_JOINT_V1"
ARTIFACT_FORMAT = "JSON_JOINT_EMPIRICAL_RESIDUAL_V1"
MIN_STANDARD_SIMULATIONS = 50_000

COMPONENTS_BY_STAT: dict[str, tuple[str, ...]] = {
    "POINTS": ("points",),
    "REBOUNDS": ("rebounds",),
    "ASSISTS": ("assists",),
    "PRA": ("points", "rebounds", "assists"),
    "POINTS_REBOUNDS": ("points", "rebounds"),
    "POINTS_ASSISTS": ("points", "assists"),
    "REBOUNDS_ASSISTS": ("rebounds", "assists"),
}

STAT_ALIASES = {
    "POINTS": "POINTS", "PTS": "POINTS",
    "REBOUNDS": "REBOUNDS", "REB": "REBOUNDS",
    "ASSISTS": "ASSISTS", "AST": "ASSISTS",
    "PRA": "PRA", "PTS+REB+AST": "PRA", "POINTS+REBOUNDS+ASSISTS": "PRA",
    "POINTS_REBOUNDS_ASSISTS": "PRA", "PTS_REB_AST": "PRA",
    "POINTS_REBOUNDS": "POINTS_REBOUNDS", "PTS+REB": "POINTS_REBOUNDS",
    "POINTS+REBOUNDS": "POINTS_REBOUNDS", "PTS_REB": "POINTS_REBOUNDS",
    "POINTS_ASSISTS": "POINTS_ASSISTS", "PTS+AST": "POINTS_ASSISTS",
    "POINTS+ASSISTS": "POINTS_ASSISTS", "PTS_AST": "POINTS_ASSISTS",
    "REBOUNDS_ASSISTS": "REBOUNDS_ASSISTS", "REB+AST": "REBOUNDS_ASSISTS",
    "REBOUNDS+ASSISTS": "REBOUNDS_ASSISTS", "REB_AST": "REBOUNDS_ASSISTS",
}


class NBAScalarCandidateError(ValueError):
    """Deterministic validation failure for the nonpublishable NBA candidate."""


def _f(value: Any, *, field: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise NBAScalarCandidateError(f"{field} must be numeric") from exc
    if not math.isfinite(out):
        raise NBAScalarCandidateError(f"{field} must be finite")
    return out


def canonical_stat(value: str) -> str:
    raw = "_".join(str(value or "").strip().upper().replace("-", " ").split())
    stat = STAT_ALIASES.get(raw)
    if stat is None:
        raise NBAScalarCandidateError(f"unsupported NBA scalar stat: {raw or '<missing>'}")
    return stat


def _sha256(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _trim_components(values: Mapping[str, Any]) -> dict[str, float]:
    return {
        name: _f(values.get(name, 0.0), field=name)
        for name in ("points", "rebounds", "assists")
    }


def derive_candidate_payload(
    source_payload: Mapping[str, Any],
    *,
    source_model_artifact_version: str,
    source_artifact_checksum: str,
    source_training_dataset_hash: str,
    source_training_code_sha: str,
) -> dict[str, Any]:
    """Freeze the NBA P/R/A joint residual candidate from an existing fitted source."""
    if str(source_payload.get("lane") or "").strip().upper() != "NBA":
        raise NBAScalarCandidateError("source artifact must be the NBA joint component candidate")
    player_means_raw = source_payload.get("player_means")
    cohort_means_raw = source_payload.get("cohort_means")
    residuals_raw = source_payload.get("residual_vectors")
    fit_contract = source_payload.get("fit_contract")
    if not isinstance(player_means_raw, Mapping) or not player_means_raw:
        raise NBAScalarCandidateError("source artifact missing player_means")
    if not isinstance(cohort_means_raw, Mapping) or not cohort_means_raw:
        raise NBAScalarCandidateError("source artifact missing cohort_means")
    if not isinstance(residuals_raw, Mapping) or not residuals_raw:
        raise NBAScalarCandidateError("source artifact missing residual_vectors")
    if not isinstance(fit_contract, Mapping):
        raise NBAScalarCandidateError("source artifact missing fit_contract")
    if fit_contract.get("whole_event_chronological_split") is not True:
        raise NBAScalarCandidateError("source artifact must use whole-event chronological split")
    if fit_contract.get("strictly_prior_residuals") is not True:
        raise NBAScalarCandidateError("source residuals must be strictly prior")
    if fit_contract.get("untouched_test_required") is not True:
        raise NBAScalarCandidateError("source artifact must preserve an untouched holdout")

    player_means = {
        str(player): _trim_components(values)
        for player, values in player_means_raw.items()
        if isinstance(values, Mapping)
    }
    cohort_means = {
        str(cohort): _trim_components(values)
        for cohort, values in cohort_means_raw.items()
        if isinstance(values, Mapping)
    }
    residual_vectors: dict[str, list[dict[str, float]]] = {}
    for cohort, rows in residuals_raw.items():
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            continue
        cleaned = [_trim_components(row) for row in rows if isinstance(row, Mapping)]
        if cleaned:
            residual_vectors[str(cohort)] = cleaned
    if not player_means or not cohort_means or not residual_vectors:
        raise NBAScalarCandidateError("source artifact cannot produce a complete NBA P/R/A candidate")

    payload = {
        "model_family": MODEL_FAMILY,
        "sport": "NBA",
        "supported_stats": sorted(COMPONENTS_BY_STAT),
        "controlling_specialist": CONTROLLING_SPECIALIST,
        "component_model": "JOINT_EMPIRICAL_RESIDUAL_BOOTSTRAP",
        "components": ["points", "rebounds", "assists"],
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
        "calibration_status": "BLOCKED_NO_EXACT_LINE_CALIBRATION_ARTIFACT",
        "certification_status": "CANDIDATE_ONLY",
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }
    payload["candidate_payload_sha256"] = _sha256(payload)
    return payload


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = (len(ordered) - 1) * min(1.0, max(0.0, q))
    lo = int(math.floor(index)); hi = int(math.ceil(index))
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
) -> dict[str, Any]:
    """Produce a raw, nonpublishable exact-line candidate probability."""
    if str(candidate_payload.get("model_family") or "") != MODEL_FAMILY:
        raise NBAScalarCandidateError("candidate payload model_family mismatch")
    if candidate_payload.get("independent_component_multiplication_permitted") is not False:
        raise NBAScalarCandidateError("joint-model independence guard missing")
    if simulation_count < MIN_STANDARD_SIMULATIONS:
        raise NBAScalarCandidateError(f"NBA scalar candidate requires at least {MIN_STANDARD_SIMULATIONS} simulations")

    stat = canonical_stat(stat_type)
    components = COMPONENTS_BY_STAT[stat]
    side_n = str(side or "").strip().upper()
    if side_n not in {"MORE", "LESS"}:
        raise NBAScalarCandidateError("side must be MORE or LESS")
    line = _f(exact_line, field="exact_line")
    player_key = " ".join(str(player or "").strip().split())
    if not player_key:
        raise NBAScalarCandidateError("player identity is required")

    player_means = candidate_payload.get("player_means")
    cohort_means = candidate_payload.get("cohort_means")
    residual_vectors = candidate_payload.get("residual_vectors")
    if not isinstance(player_means, Mapping) or not isinstance(cohort_means, Mapping):
        raise NBAScalarCandidateError("candidate payload missing fitted means")
    if not isinstance(residual_vectors, Mapping) or not residual_vectors:
        raise NBAScalarCandidateError("candidate payload missing residual vectors")

    baseline_raw = player_means.get(player_key)
    if baseline_raw is None:
        normalized = player_key.casefold()
        for name, values in player_means.items():
            if " ".join(str(name).casefold().split()) == normalized:
                baseline_raw = values
                break
    if baseline_raw is None:
        baseline_raw = cohort_means.get("ALL")
    if baseline_raw is None and len(cohort_means) == 1:
        baseline_raw = next(iter(cohort_means.values()))
    if not isinstance(baseline_raw, Mapping):
        raise NBAScalarCandidateError(f"no fitted baseline for player {player_key}")
    baseline = _trim_components(baseline_raw)

    residual_library = residual_vectors.get("ALL")
    if residual_library is None and len(residual_vectors) == 1:
        residual_library = next(iter(residual_vectors.values()))
    if not isinstance(residual_library, Sequence) or isinstance(residual_library, (str, bytes)) or not residual_library:
        raise NBAScalarCandidateError("no NBA joint residual library available")

    rng = random.Random(int(seed))
    scores: list[float] = []
    totals = {name: 0.0 for name in ("points", "rebounds", "assists")}
    for _ in range(simulation_count):
        residual = residual_library[rng.randrange(len(residual_library))]
        if not isinstance(residual, Mapping):
            raise NBAScalarCandidateError("joint residual row must be an object")
        draw: dict[str, float] = {}
        for name in totals:
            value = max(0.0, baseline[name] + _f(residual.get(name, 0.0), field=f"residual:{name}"))
            value = float(max(0, int(round(value))))
            draw[name] = value
            totals[name] += value
        scores.append(sum(draw[name] for name in components))

    p_more = sum(score > line for score in scores) / simulation_count
    p_less = sum(score < line for score in scores) / simulation_count
    p_push = max(0.0, 1.0 - p_more - p_less)
    requested = p_more if side_n == "MORE" else p_less
    if not 0.0 < requested < 1.0:
        raise NBAScalarCandidateError("raw side probability is degenerate")

    return {
        "candidate_family": "NBA_SCALAR",
        "sport": "NBA",
        "stat_type": stat,
        "market_family": f"NBA_{stat}",
        "controlling_specialist": CONTROLLING_SPECIALIST,
        "model_family": MODEL_FAMILY,
        "simulation_count": simulation_count,
        "seed": int(seed),
        "exact_line": line,
        "side": side_n,
        "raw_candidate_probability": requested,
        "P(MORE)": p_more,
        "P(LESS)": p_less,
        "P(PUSH)": p_push,
        "calibrated_probability": None,
        "calibrated_lower_bound": None,
        "calibrated_upper_bound": None,
        "simulated_mean": mean(scores),
        "simulated_median": median(scores),
        "simulated_std": pstdev(scores),
        "simulated_p10": _quantile(scores, 0.10),
        "simulated_p90": _quantile(scores, 0.90),
        "component_means": {name: totals[name] / simulation_count for name in totals},
        "terminal_status": "CALIBRATION_BLOCKED_NO_PUBLISH",
        "probability_claim_status": "RESEARCH_ONLY_CANDIDATE",
        "blockers": ["NBA_SCALAR_CANDIDATE_UNCALIBRATED", "NBA_SCALAR_FORWARD_CERTIFICATION_REQUIRED"],
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


__all__ = [
    "ARTIFACT_FORMAT", "CAN_EXECUTE", "COMPONENTS_BY_STAT", "CONTROLLING_SPECIALIST",
    "MIN_STANDARD_SIMULATIONS", "MODEL_FAMILY", "NBAScalarCandidateError", "canonical_stat",
    "derive_candidate_payload", "simulate_exact_line",
]
