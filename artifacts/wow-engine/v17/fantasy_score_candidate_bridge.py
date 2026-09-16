"""Evidence-only runtime bridge for V17 Fantasy Score fitted candidates.

This module is intentionally *not* a production probability publisher. It gives
an explicitly selected frozen CANDIDATE/SHADOW artifact a narrow runtime path so
immutable pregame forecasts can be collected and later settled/calibrated.

Hard boundaries:
- exact Fantasy Score lane + controlling specialist only;
- candidate_research_active is separate from active/promoted production state;
- exact frozen model/source hash and exact verified scoring profile required;
- >= 50,000 Monte Carlo draws;
- no calibrated probability or bound is invented;
- probability_publishable=False, rank_eligible=False, can_execute=False always;
- absent artifact is MODEL_UNAVAILABLE; invoked scorer failures keep typed errors.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import random
from statistics import mean, median, pstdev
from typing import Any, Mapping, Sequence

from fastapi import HTTPException

from v17.fantasy_score_forward_cohort_runtime import LANE_SPECS, MIN_SIMULATIONS

FEATURE_SCHEMA_VERSION = "PROP_FEATURES_V1"
ARTIFACT_FORMAT = "FANTASY_SCORE_CANDIDATE_JSON_V1"
ARTIFACT_SCHEMA_VERSION = "FANTASY_SCORE_CANDIDATE_ARTIFACT_V1"
ALLOWED_LIFECYCLES = {"CANDIDATE", "SHADOW"}

NFL_COMPONENTS = (
    "passing_yards",
    "passing_td",
    "interceptions",
    "rushing_yards",
    "rushing_td",
    "receiving_yards",
    "receptions",
    "receiving_td",
    "fumbles_lost",
    "two_point_conversions",
)
NBA_COMPONENTS = ("points", "rebounds", "assists", "steals", "blocks", "turnovers")
MLB_HITTER_COMPONENTS = (
    "singles", "doubles", "triples", "home_runs", "runs", "rbi", "walks", "hbp", "stolen_bases"
)
MLB_PITCHER_COMPONENTS = ("wins", "quality_starts", "strikeouts", "outs_recorded", "earned_runs")

COMPONENTS = {
    "NFL": NFL_COMPONENTS,
    "NBA": NBA_COMPONENTS,
    "WNBA": NBA_COMPONENTS,
    "MLB_HITTER": MLB_HITTER_COMPONENTS,
    "MLB_PITCHER": MLB_PITCHER_COMPONENTS,
}
COUNT_COMPONENTS = {
    "NFL": frozenset({
        "passing_td", "interceptions", "rushing_td", "receptions", "receiving_td",
        "fumbles_lost", "two_point_conversions",
    }),
    "NBA": frozenset(NBA_COMPONENTS),
    "WNBA": frozenset(NBA_COMPONENTS),
    "MLB_HITTER": frozenset(MLB_HITTER_COMPONENTS),
    "MLB_PITCHER": frozenset(MLB_PITCHER_COMPONENTS),
}
NFL_POSITIONS = {"QB", "RB", "WR", "TE"}


class FantasyScoreCandidateBridgeError(RuntimeError):
    def __init__(self, code: str, message: str, *, failure_class: str = "MODEL_SCORER_FAILED") -> None:
        super().__init__(message)
        self.code = code
        self.failure_class = failure_class


def _lane_for_request(req: Any) -> str | None:
    sport = str(getattr(req, "sport", "") or "").strip().upper()
    stat = str(getattr(req, "stat_type", "") or "").strip().upper()
    for lane, spec in LANE_SPECS.items():
        if spec.sport == sport and spec.stat_type == stat:
            return lane
    return None


def is_fantasy_score_request(req: Any) -> bool:
    return _lane_for_request(req) is not None


def _sha256_text(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        return None
    return text


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _finite(value: Any, *, field: str) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_CANDIDATE_ARTIFACT_INVALID",
            f"{field} must be numeric",
            failure_class="MODEL_OUTPUT_INVALID",
        ) from exc
    if not math.isfinite(out):
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_CANDIDATE_ARTIFACT_INVALID",
            f"{field} must be finite",
            failure_class="MODEL_OUTPUT_INVALID",
        )
    return out


def _candidate_artifact(db: Any, *, lane: str) -> dict[str, Any]:
    spec = LANE_SPECS[lane]
    try:
        result = (
            db.table("wow_prop_fitted_model_artifacts")
            .select("*")
            .eq("sport", spec.sport)
            .eq("stat_type", spec.stat_type)
            .eq("feature_schema_version", FEATURE_SCHEMA_VERSION)
            .eq("candidate_research_active", True)
            .limit(2)
            .execute()
        )
    except Exception as exc:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_CANDIDATE_ARTIFACT_REGISTRY_UNAVAILABLE",
            "Fantasy Score candidate artifact registry lookup failed",
        ) from exc

    rows = [dict(row) for row in (result.data or [])]
    if not rows:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_CANDIDATE_ARTIFACT_NOT_FOUND",
            f"No explicitly activated research candidate exists for {lane}",
            failure_class="MODEL_UNAVAILABLE",
        )
    if len(rows) != 1:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_CANDIDATE_ARTIFACT_AMBIGUOUS",
            f"Multiple research candidates resolved for {lane}",
            failure_class="MODEL_OUTPUT_INVALID",
        )
    return rows[0]


def _artifact_payload(artifact: Mapping[str, Any], *, lane: str) -> dict[str, Any]:
    lifecycle = str(artifact.get("lifecycle_state") or "").strip().upper()
    if lifecycle not in ALLOWED_LIFECYCLES:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_CANDIDATE_LIFECYCLE_INVALID",
            "Evidence-only scorer requires CANDIDATE or SHADOW lifecycle",
            failure_class="MODEL_OUTPUT_INVALID",
        )
    if artifact.get("candidate_research_active") is not True:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_CANDIDATE_NOT_RESEARCH_ACTIVE",
            "Candidate is not explicitly activated for research scoring",
            failure_class="MODEL_UNAVAILABLE",
        )
    if any(artifact.get(key) is True for key in ("promoted", "active", "probability_publishable", "can_execute")):
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_CANDIDATE_AUTHORITY_CONFLICT",
            "Research candidate cannot carry production/publication/execution authority",
            failure_class="MODEL_OUTPUT_INVALID",
        )
    if str(artifact.get("artifact_format") or "") != ARTIFACT_FORMAT:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_CANDIDATE_ARTIFACT_FORMAT_UNSUPPORTED",
            "Candidate artifact format is not supported by the V17 research bridge",
            failure_class="MODEL_OUTPUT_INVALID",
        )
    payload = artifact.get("artifact_payload")
    if not isinstance(payload, Mapping):
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_CANDIDATE_ARTIFACT_INVALID",
            "artifact_payload must be an object",
            failure_class="MODEL_OUTPUT_INVALID",
        )
    payload = dict(payload)
    if payload.get("artifact_schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_CANDIDATE_ARTIFACT_SCHEMA_UNSUPPORTED",
            "Candidate artifact schema version is unsupported",
            failure_class="MODEL_OUTPUT_INVALID",
        )
    if str(payload.get("lane") or "").strip().upper() != lane:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_CANDIDATE_LANE_MISMATCH",
            "Candidate artifact lane does not match the requested Fantasy Score lane",
            failure_class="MODEL_OUTPUT_INVALID",
        )
    if _sha256_text(artifact.get("training_dataset_hash")) is None:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_CANDIDATE_SOURCE_HASH_INVALID",
            "Candidate training dataset hash is missing or invalid",
            failure_class="MODEL_OUTPUT_INVALID",
        )
    if _sha256_text(artifact.get("artifact_checksum")) is None:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_CANDIDATE_CHECKSUM_INVALID",
            "Candidate artifact checksum is missing or invalid",
            failure_class="MODEL_OUTPUT_INVALID",
        )
    return payload


def _role_value(evidence: Mapping[str, Any], *keys: str) -> str | None:
    role = evidence.get("role_status")
    if isinstance(role, Mapping):
        for key in keys:
            value = str(role.get(key) or "").strip().upper()
            if value:
                return value
    return None


def _cohort(lane: str, evidence: Mapping[str, Any]) -> str:
    if lane == "NFL":
        position = _role_value(evidence, "position", "pos", "role")
        if position not in NFL_POSITIONS:
            raise FantasyScoreCandidateBridgeError(
                "NFL_FANTASY_POSITION_MISSING_OR_INVALID",
                "NFL Fantasy Score research scoring requires QB/RB/WR/TE position evidence",
                failure_class="MODEL_INPUTS_INSUFFICIENT",
            )
        return position
    if lane in {"NBA", "WNBA"}:
        return _role_value(evidence, "position", "pos", "role") or "ALL"
    return lane


def _mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_CANDIDATE_ARTIFACT_INVALID",
            f"{field} must be an object",
            failure_class="MODEL_OUTPUT_INVALID",
        )
    return value


def _player_baseline(payload: Mapping[str, Any], *, lane: str, player: str, cohort: str) -> dict[str, float]:
    components = COMPONENTS[lane]
    player_means = _mapping(payload.get("player_means") or {}, field="player_means")
    cohort_field = "position_means" if lane == "NFL" else "cohort_means"
    cohort_means = _mapping(payload.get(cohort_field) or {}, field=cohort_field)

    source: Any = player_means.get(player)
    if source is None:
        normalized = " ".join(player.casefold().split())
        for key, value in player_means.items():
            if " ".join(str(key).casefold().split()) == normalized:
                source = value
                break
    if source is None:
        source = cohort_means.get(cohort)
    if source is None and len(cohort_means) == 1:
        source = next(iter(cohort_means.values()))
    source = _mapping(source or {}, field="baseline")
    if not source:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_CANDIDATE_BASELINE_UNAVAILABLE",
            f"No fitted baseline exists for player/cohort {player}/{cohort}",
            failure_class="MODEL_INPUTS_INSUFFICIENT",
        )
    return {name: max(0.0, _finite(source.get(name, 0.0), field=f"baseline:{name}")) for name in components}


def _residual_library(payload: Mapping[str, Any], *, lane: str, cohort: str) -> list[Mapping[str, Any]]:
    libraries = _mapping(payload.get("residual_vectors") or {}, field="residual_vectors")
    values = libraries.get(cohort)
    if values is None and len(libraries) == 1:
        values = next(iter(libraries.values()))
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_CANDIDATE_RESIDUAL_LIBRARY_UNAVAILABLE",
            f"No residual library exists for cohort {cohort}",
            failure_class="MODEL_INPUTS_INSUFFICIENT",
        )
    rows = [row for row in values if isinstance(row, Mapping)]
    if not rows:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_CANDIDATE_RESIDUAL_LIBRARY_INVALID",
            "Residual library contains no valid rows",
            failure_class="MODEL_OUTPUT_INVALID",
        )
    return rows


def _scoring_profile(payload: Mapping[str, Any], *, lane: str) -> tuple[dict[str, Any], str, Mapping[str, Any]]:
    profile = dict(_mapping(payload.get("scoring_profile"), field="scoring_profile"))
    profile_id = str(profile.get("profile_id") or "").strip()
    if not profile_id or profile.get("verified") is not True:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_SCORING_PROFILE_UNVERIFIED",
            "Exact verified scoring profile is required",
            failure_class="MODEL_INPUTS_INSUFFICIENT",
        )
    weights = _mapping(profile.get("weights"), field="scoring_profile.weights")
    missing = [name for name in COMPONENTS[lane] if name not in weights]
    if missing:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_SCORING_PROFILE_INCOMPLETE",
            "Scoring profile is missing required component weights",
            failure_class="MODEL_OUTPUT_INVALID",
        )
    profile_hash = _canonical_sha256(profile)
    expected_hash = _sha256_text(payload.get("scoring_profile_sha256"))
    if expected_hash is not None and expected_hash != profile_hash:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_SCORING_PROFILE_HASH_MISMATCH",
            "Frozen scoring profile hash does not match artifact payload",
            failure_class="MODEL_OUTPUT_INVALID",
        )
    return profile, profile_hash, weights


def _score_components(components: Mapping[str, float], weights: Mapping[str, Any], lane: str) -> float:
    return sum(
        components[name] * _finite(weights[name], field=f"weight:{name}")
        for name in COMPONENTS[lane]
    )


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * min(1.0, max(0.0, q))
    lo = int(math.floor(index))
    hi = int(math.ceil(index))
    if lo == hi:
        return float(ordered[lo])
    weight = index - lo
    return float(ordered[lo] * (1.0 - weight) + ordered[hi] * weight)


def score_candidate_from_artifact(
    *,
    artifact: Mapping[str, Any],
    lane: str,
    player: str,
    line: float,
    direction: str,
    evidence: Mapping[str, Any],
    seed: int,
    simulation_count: int = MIN_SIMULATIONS,
    model_timestamp: str | None = None,
) -> dict[str, Any]:
    if simulation_count < MIN_SIMULATIONS:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_SIMULATION_FLOOR_NOT_MET",
            f"Fantasy Score research scoring requires at least {MIN_SIMULATIONS} simulations",
            failure_class="MODEL_OUTPUT_INVALID",
        )
    side = str(direction or "").strip().upper()
    if side not in {"MORE", "LESS"}:
        raise FantasyScoreCandidateBridgeError(
            "PROP_DIRECTION_INVALID", "direction must be MORE or LESS", failure_class="MODEL_INPUTS_INSUFFICIENT"
        )
    spec = LANE_SPECS[lane]
    payload = _artifact_payload(artifact, lane=lane)
    cohort = _cohort(lane, evidence)
    baseline = _player_baseline(payload, lane=lane, player=player, cohort=cohort)
    residuals = _residual_library(payload, lane=lane, cohort=cohort)
    profile, profile_hash, weights = _scoring_profile(payload, lane=lane)
    exact_line = _finite(line, field="line")

    rng = random.Random(int(seed))
    scores: list[float] = []
    component_totals = {name: 0.0 for name in COMPONENTS[lane]}
    for _ in range(simulation_count):
        residual = residuals[rng.randrange(len(residuals))]
        components: dict[str, float] = {}
        for name in COMPONENTS[lane]:
            value = max(0.0, baseline[name] + _finite(residual.get(name, 0.0), field=f"residual:{name}"))
            if name in COUNT_COMPONENTS[lane]:
                value = float(max(0, int(round(value))))
            components[name] = value
            component_totals[name] += value
        scores.append(_score_components(components, weights, lane))

    p_more = sum(score > exact_line for score in scores) / simulation_count
    p_less = sum(score < exact_line for score in scores) / simulation_count
    p_push = max(0.0, 1.0 - p_more - p_less)
    raw = p_more if side == "MORE" else p_less
    if not 0.0 < raw < 1.0:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_RAW_PROBABILITY_DEGENERATE",
            "Candidate side probability must be strictly between zero and one",
            failure_class="MODEL_OUTPUT_INVALID",
        )

    timestamp = model_timestamp or datetime.now(timezone.utc).isoformat()
    return {
        "market_family": spec.market_family,
        "controlling_specialist": spec.controlling_specialist,
        "model_version": str(artifact.get("model_artifact_version") or ""),
        "model_source_sha256": str(artifact.get("training_dataset_hash") or "").lower(),
        "model_artifact_checksum": str(artifact.get("artifact_checksum") or "").lower(),
        "model_lifecycle_state": str(artifact.get("lifecycle_state") or "").upper(),
        "scoring_profile_id": str(profile["profile_id"]),
        "scoring_profile_sha256": profile_hash,
        "simulation_count": simulation_count,
        "seed": int(seed),
        "position": cohort if lane == "NFL" else None,
        "exact_line": exact_line,
        "side": side,
        "raw_candidate_probability": raw,
        "P(MORE)": p_more,
        "P(LESS)": p_less,
        "P(PUSH)": p_push,
        "calibrated_probability": None,
        "calibrated_lower_bound": None,
        "calibrated_upper_bound": None,
        "fantasy_score_mean": mean(scores),
        "fantasy_score_median": median(scores),
        "fantasy_score_std": pstdev(scores),
        "fantasy_score_p10": _quantile(scores, 0.10),
        "fantasy_score_p25": _quantile(scores, 0.25),
        "fantasy_score_p75": _quantile(scores, 0.75),
        "fantasy_score_p90": _quantile(scores, 0.90),
        "component_means": {name: component_totals[name] / simulation_count for name in COMPONENTS[lane]},
        "model_timestamp": timestamp,
        "terminal_status": "CALIBRATION_BLOCKED_NO_PUBLISH",
        "probability_claim_status": "RESEARCH_ONLY_CANDIDATE",
        "blockers": [
            "FANTASY_SCORE_CANDIDATE_UNCALIBRATED",
            "FANTASY_SCORE_FULL_MODEL_CONTEXT_NOT_CERTIFIED",
        ],
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


def _http_failure(req: Any, exc: FantasyScoreCandidateBridgeError, *, specialist: str | None) -> HTTPException:
    status = 409
    if exc.failure_class == "MODEL_INPUTS_INSUFFICIENT":
        status = 422
    elif exc.failure_class == "MODEL_SCORER_FAILED":
        status = 503
    return HTTPException(
        status_code=status,
        detail={
            "code": exc.failure_class,
            "blocker_code": exc.code,
            "requested_scope": {
                "sport": str(getattr(req, "sport", "") or "").upper(),
                "stat_type": str(getattr(req, "stat_type", "") or "").upper(),
            },
            "specialist_model_capability": "UNAVAILABLE" if exc.failure_class == "MODEL_UNAVAILABLE" else "AVAILABLE",
            "specialist_model_name": specialist,
            "specialist_model_status": exc.failure_class,
            "probability_claim_status": exc.failure_class,
            "failed_contract_scope": ["CONFIDENCE"],
            "probability_publishable": False,
            "governed_publishable": False,
            "rank_eligible": False,
            "can_execute": False,
        },
    )


def score_fantasy_candidate_research(
    market_api: Any,
    req: Any,
    *,
    model_identity: str,
) -> dict[str, Any]:
    lane = _lane_for_request(req)
    if lane is None:
        raise FantasyScoreCandidateBridgeError(
            "FANTASY_SCORE_ROUTE_UNSUPPORTED", "Request is not a declared Fantasy Score candidate route"
        )
    spec = LANE_SPECS[lane]

    try:
        evidence = market_api.repair_prop_evidence(
            req,
            primary_fetch=market_api.prod._prop_evidence,
            client=market_api.prod.get_client(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _http_failure(
            req,
            FantasyScoreCandidateBridgeError(
                "FANTASY_SCORE_EVIDENCE_BRIDGE_FAILED",
                "Fantasy Score evidence retrieval failed",
                failure_class="MODEL_SCORER_FAILED",
            ),
            specialist=spec.controlling_specialist,
        ) from exc

    if evidence.get("ok") is not True or evidence.get("code") != "PROP_EVIDENCE_READY":
        raise _http_failure(
            req,
            FantasyScoreCandidateBridgeError(
                str(evidence.get("code") or "FANTASY_SCORE_EVIDENCE_INCOMPLETE"),
                "Fantasy Score candidate inputs are incomplete",
                failure_class="MODEL_INPUTS_INSUFFICIENT",
            ),
            specialist=spec.controlling_specialist,
        )

    try:
        artifact = _candidate_artifact(market_api.prod.get_client(), lane=lane)
        output = score_candidate_from_artifact(
            artifact=artifact,
            lane=lane,
            player=str(getattr(req, "player", "") or "").strip(),
            line=float(getattr(req, "line")),
            direction=str(getattr(req, "direction", "")),
            evidence=evidence,
            seed=int(getattr(req, "seed", 0) or 0),
            simulation_count=MIN_SIMULATIONS,
            model_timestamp=datetime.now(timezone.utc).isoformat(),
        )
    except FantasyScoreCandidateBridgeError as exc:
        raise _http_failure(req, exc, specialist=spec.controlling_specialist) from exc
    except Exception as exc:
        raise _http_failure(
            req,
            FantasyScoreCandidateBridgeError(
                "FANTASY_SCORE_CANDIDATE_SCORER_EXCEPTION",
                "Fantasy Score candidate scorer threw an unexpected exception",
                failure_class="MODEL_SCORER_FAILED",
            ),
            specialist=spec.controlling_specialist,
        ) from exc

    return {
        "ok": True,
        "research_only": True,
        "candidate_evidence_collection_eligible": True,
        "candidate_model_output": output,
        "requested_scope": {"sport": spec.sport, "stat_type": spec.stat_type, "lane": lane},
        "objective_lanes": {
            "MODEL": {"status": "PASS_RESEARCH_ONLY", "specialist_invoked": True, "can_execute": False},
            "CALIBRATION": {"status": "HOLD", "reason": "FANTASY_SCORE_CANDIDATE_UNCALIBRATED", "can_execute": False},
            "PUBLICATION": {"status": "BLOCKED", "governed_publishable": False, "can_execute": False},
            "MONEY": {"status": "HOLD", "reason": "CALIBRATED_LOWER_BOUND_UNAVAILABLE", "can_execute": False},
        },
        "backend_traversal": {
            "requester_model": model_identity,
            "render": "PASS",
            "supabase_evidence": "PASS",
            "controlling_specialist": "PASS",
            "candidate_artifact": "PASS",
            "candidate_raw_model": "PASS",
            "calibration": "NOT_CERTIFIED",
            "governed_publication": "BLOCKED",
            "prediction_ledger_write": "NOT_ATTEMPTED_BY_SCORER",
        },
        "probability_publishable": False,
        "governed_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    }


__all__ = [
    "ARTIFACT_FORMAT",
    "ARTIFACT_SCHEMA_VERSION",
    "FantasyScoreCandidateBridgeError",
    "is_fantasy_score_request",
    "score_candidate_from_artifact",
    "score_fantasy_candidate_research",
]
