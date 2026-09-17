"""Exact-route forward calibration/certification audit for V17 props.

This is a fail-closed control-plane runtime. It reads immutable production
prediction/outcome evidence and evaluates only one exact artifact cohort at a
time. It never fits a calibrator, certifies by declaration, promotes an artifact,
changes publication state, or grants execution authority.

A route may reach CALIBRATION_CERTIFIED_PASS only when a reviewed, versioned
route policy is present in ``REVIEWED_ROUTE_POLICIES``. Until governance reviews
an exact policy, the runtime still reports cohort counts/metrics but returns the
typed blocker ``CERTIFICATION_POLICY_REVIEW_REQUIRED``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from calibration import PHASE_B_MIN_N
from v17.prop_calibration_certification import (
    CALIBRATION_CERTIFICATION_BLOCKED,
    CALIBRATION_EVIDENCE_REQUIRED,
    PropCalibrationCertificationPolicy,
    PropCalibrationObservation,
    build_calibration_certification_packet,
)
from v17.prop_capability_manifest import DECLARED_PROP_LANES, normalize_prop_sport
from v17.prop_route_lifecycle import (
    CALIBRATION_CERTIFIED_PASS,
    FEATURE_SCHEMA_VERSION,
    PHASE_A_CALIBRATION_STATES,
)

CAN_EXECUTE = False
PROVIDER = "WOW_PROP_FITTED_MODEL_V1"
PAGE_SIZE = 1000
OUTCOME_CHUNK_SIZE = 150
POLICY_REVIEW_REQUIRED = "CERTIFICATION_POLICY_REVIEW_REQUIRED"
EXACT_ARTIFACT_COHORT_ONLY = "EXACT_ARTIFACT_COHORT_ONLY"


class PropCertificationAuditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    routes: list[str] = Field(default_factory=list)
    include_inactive: bool = True


@dataclass(frozen=True)
class RoutePolicyStatus:
    sport: str
    stat_type: str
    policy_id: str
    review_status: str
    min_forward_settled_n: int
    blocker: str | None
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _token(sport: str, stat_type: str) -> str:
    return f"{normalize_prop_sport(sport)}:{str(stat_type or '').strip().upper()}"


# The universal ladder ratifies >=200 settled independent forward rows before
# Phase B is even possible. Metric cutoffs are intentionally not invented here.
# A reviewed policy is added route-by-route only after governance explicitly
# approves its thresholds. This registry therefore makes the missing review
# visible instead of silently substituting one universal threshold set.
ROUTE_POLICY_STATUS: dict[tuple[str, str], RoutePolicyStatus] = {
    key: RoutePolicyStatus(
        sport=key[0],
        stat_type=key[1],
        policy_id=f"V17_{key[0]}_{key[1]}_FORWARD_CERT_POLICY_V1",
        review_status="REVIEW_REQUIRED",
        min_forward_settled_n=PHASE_B_MIN_N,
        blocker=POLICY_REVIEW_REQUIRED,
    )
    for key in sorted(DECLARED_PROP_LANES)
}

# Only independently reviewed policies belong here. Empty is a valid governed
# state: engineering can be complete while empirical/governance approval remains
# outstanding. Tests and future reviewed release PRs may pass an explicit map to
# the pure audit function without mutating this production registry.
REVIEWED_ROUTE_POLICIES: dict[tuple[str, str], PropCalibrationCertificationPolicy] = {}


def _rows(result: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in (getattr(result, "data", None) or [])]


def _paginate(build: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    start = 0
    while True:
        batch = _rows(build().range(start, start + PAGE_SIZE - 1).execute())
        output.extend(batch)
        if len(batch) < PAGE_SIZE:
            return output
        start += PAGE_SIZE


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[i:i + size] for i in range(0, len(values), size)]


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
    return sha256(raw.encode("utf-8")).hexdigest()


def _descriptive_metrics(rows: list[PropCalibrationObservation], *, bins: int = 10) -> dict[str, Any]:
    if not rows:
        return {
            "n": 0,
            "mean_calibrated_probability": None,
            "mean_calibrated_lower_bound": None,
            "observed_hit_rate": None,
            "brier_score": None,
            "log_loss": None,
            "expected_calibration_error": None,
            "calibration_bias": None,
            "lower_bound_reliability_margin": None,
        }
    ps = [float(row.calibrated_probability) for row in rows]
    lbs = [float(row.calibrated_lower_bound) for row in rows]
    ys = [int(row.outcome) for row in rows]
    n = len(rows)
    mean_p = sum(ps) / n
    mean_lb = sum(lbs) / n
    observed = sum(ys) / n
    brier = sum((p - y) ** 2 for p, y in zip(ps, ys)) / n
    eps = 1e-15
    log_loss = -sum(y * math.log(max(p, eps)) + (1-y) * math.log(max(1-p, eps)) for p, y in zip(ps, ys)) / n
    ece = 0.0
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        members = [i for i, p in enumerate(ps) if low <= p < high or (index == bins - 1 and p == 1.0)]
        if members:
            mp = sum(ps[i] for i in members) / len(members)
            my = sum(ys[i] for i in members) / len(members)
            ece += (len(members) / n) * abs(my - mp)
    return {
        "n": n,
        "mean_calibrated_probability": mean_p,
        "mean_calibrated_lower_bound": mean_lb,
        "observed_hit_rate": observed,
        "brier_score": brier,
        "log_loss": log_loss,
        "expected_calibration_error": ece,
        "calibration_bias": observed - mean_p,
        "lower_bound_reliability_margin": observed - mean_lb,
    }


def _artifact_rows(db: Any, *, include_inactive: bool) -> list[dict[str, Any]]:
    fields = (
        "artifact_id,provider_identity,model_family,model_artifact_version,calibrator_version,"
        "sport,stat_type,feature_schema_version,feature_transform_version,specialist_version,"
        "certification_id,lifecycle_state,training_dataset_hash,training_code_sha,artifact_checksum,"
        "validation_metrics,promoted,active,probability_publishable,can_execute,candidate_research_active"
    )
    query = db.table("wow_prop_fitted_model_artifacts").select(fields).eq("provider_identity", PROVIDER)
    if not include_inactive:
        query = query.eq("active", True)
    return _rows(query.execute())


def _prediction_rows(db: Any, artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    fields = (
        "prediction_id,event_id,event_start_time,model_timestamp,locked_at,source_snapshot_id,player,"
        "sport,stat_type,line,direction,model_family,model_artifact_version,model_artifact_checksum,"
        "feature_schema_version,calibration_version,calibration_status,calibrated_probability,"
        "calibrated_probability_lower_bound,model_provider_identity"
    )
    return _paginate(
        lambda: db.table("wow_predictions").select(fields)
        .eq("model_provider_identity", PROVIDER)
        .eq("sport", str(artifact.get("sport")))
        .eq("stat_type", str(artifact.get("stat_type")))
        .eq("model_artifact_version", str(artifact.get("model_artifact_version")))
        .eq("model_artifact_checksum", str(artifact.get("artifact_checksum")))
    )


def _outcome_map(db: Any, prediction_ids: list[str]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(prediction_ids, OUTCOME_CHUNK_SIZE):
        result = (
            db.table("wow_outcomes")
            .select("prediction_id,hit,push,void,actual_stat,settlement_source,settlement_timestamp")
            .in_("prediction_id", chunk).execute()
        )
        for row in _rows(result):
            if row.get("prediction_id"):
                output[str(row["prediction_id"])] = row
    return output


def _iso_before(left: Any, right: Any) -> bool:
    from datetime import datetime
    try:
        a = datetime.fromisoformat(str(left).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(right).replace("Z", "+00:00"))
        return a.utcoffset() is not None and b.utcoffset() is not None and a < b
    except (TypeError, ValueError):
        return False


def build_independent_observations(
    predictions: Iterable[Mapping[str, Any]],
    outcomes: Mapping[str, Mapping[str, Any]],
) -> tuple[list[PropCalibrationObservation], dict[str, int]]:
    """Return one deterministic settled side per independent sporting thesis.

    Direction twins and refreshed snapshots are audit-visible but cannot inflate
    calibration N. MORE is the canonical side when both directions exist; within
    a direction the latest immutable pregame snapshot is retained.
    """
    grouped: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = {}
    excluded = 0
    settled_direction_rows = 0
    for row in predictions:
        outcome = outcomes.get(str(row.get("prediction_id") or ""))
        if not outcome or outcome.get("hit") is None or outcome.get("push") is True or outcome.get("void") is True:
            continue
        settled_direction_rows += 1
        if not row.get("source_snapshot_id") or not row.get("locked_at"):
            excluded += 1
            continue
        if not _iso_before(row.get("model_timestamp"), row.get("event_start_time")) or not _iso_before(row.get("locked_at"), row.get("event_start_time")):
            excluded += 1
            continue
        p = _finite(row.get("calibrated_probability"))
        lb = _finite(row.get("calibrated_probability_lower_bound"))
        if p is None or lb is None or not (0.0 < lb <= p < 1.0):
            excluded += 1
            continue
        key = (
            str(row.get("event_id") or ""),
            str(row.get("player") or "").strip().casefold(),
            str(row.get("stat_type") or "").upper(),
            str(row.get("line")),
        )
        if not all(key):
            excluded += 1
            continue
        grouped.setdefault(key, []).append(row)

    observations: list[PropCalibrationObservation] = []
    duplicate_rows = 0
    for members in grouped.values():
        duplicate_rows += max(0, len(members) - 1)
        more = [r for r in members if str(r.get("direction") or "").upper() == "MORE"]
        pool = more or [r for r in members if str(r.get("direction") or "").upper() == "LESS"]
        if not pool:
            excluded += len(members)
            continue
        chosen = sorted(pool, key=lambda r: (str(r.get("model_timestamp") or ""), str(r.get("prediction_id") or "")))[-1]
        outcome = outcomes[str(chosen["prediction_id"])]
        observations.append(PropCalibrationObservation(
            prediction_id=str(chosen["prediction_id"]),
            sport=str(chosen.get("sport") or ""),
            stat_type=str(chosen.get("stat_type") or ""),
            feature_schema_version=str(chosen.get("feature_schema_version") or ""),
            model_family=str(chosen.get("model_family") or ""),
            model_artifact_version=str(chosen.get("model_artifact_version") or ""),
            artifact_checksum=str(chosen.get("model_artifact_checksum") or ""),
            calibrator_version=str(chosen.get("calibration_version") or ""),
            calibration_status=str(chosen.get("calibration_status") or ""),
            calibrated_probability=float(chosen["calibrated_probability"]),
            calibrated_lower_bound=float(chosen["calibrated_probability_lower_bound"]),
            outcome=1 if outcome.get("hit") is True else 0,
            model_timestamp=str(chosen.get("model_timestamp") or ""),
            event_start_timestamp=str(chosen.get("event_start_time") or ""),
        ))
    observations.sort(key=lambda row: row.prediction_id)
    return observations, {
        "settled_direction_row_n": settled_direction_rows,
        "independent_settled_thesis_n": len(observations),
        "duplicate_or_twin_row_n": duplicate_rows,
        "excluded_invalid_row_n": excluded,
    }


def audit_artifact_certification(
    artifact: Mapping[str, Any],
    observations: list[PropCalibrationObservation],
    *,
    reviewed_policies: Mapping[tuple[str, str], PropCalibrationCertificationPolicy] | None = None,
) -> dict[str, Any]:
    sport = normalize_prop_sport(str(artifact.get("sport") or ""))
    stat = str(artifact.get("stat_type") or "").upper()
    key = (sport, stat)
    policy_status = ROUTE_POLICY_STATUS.get(key)
    policy_map = reviewed_policies if reviewed_policies is not None else REVIEWED_ROUTE_POLICIES
    policy = policy_map.get(key)
    metrics = _descriptive_metrics(observations)
    calibration_states = sorted({str(row.calibration_status or "").upper() for row in observations})
    blockers: list[str] = []
    if len(observations) < PHASE_B_MIN_N:
        blockers.append("MIN_SETTLED_CALIBRATION_COHORT_NOT_MET")
    if calibration_states and all(state in PHASE_A_CALIBRATION_STATES for state in calibration_states):
        blockers.append("PHASE_A_PRECALIBRATION_NOT_FORWARD_CERTIFICATION")
    if policy is None:
        blockers.append(POLICY_REVIEW_REQUIRED)
        status = CALIBRATION_EVIDENCE_REQUIRED if blockers[0] != POLICY_REVIEW_REQUIRED else CALIBRATION_CERTIFICATION_BLOCKED
        payload = {
            "artifact": {k: artifact.get(k) for k in (
                "sport","stat_type","feature_schema_version","model_family","model_artifact_version","artifact_checksum","calibrator_version"
            )},
            "policy_id": policy_status.policy_id if policy_status else None,
            "calibration_states": calibration_states,
            "metrics": metrics,
            "blockers": list(dict.fromkeys(blockers)),
            "prediction_ids": [row.prediction_id for row in observations],
            "can_execute": False,
        }
        return {
            **payload["artifact"],
            "policy_id": payload["policy_id"],
            "policy_review_status": policy_status.review_status if policy_status else "MISSING",
            "status": status,
            "blockers": list(dict.fromkeys(blockers)),
            "calibration_states": calibration_states,
            "metrics": metrics,
            "evidence_hash": _canonical_hash(payload),
            "promotion_package_ready": False,
            "counting_basis": EXACT_ARTIFACT_COHORT_ONLY,
            "can_execute": False,
        }

    packet = build_calibration_certification_packet(observations, policy=policy)
    return {
        **packet.as_dict(),
        "policy_review_status": "APPROVED",
        "calibration_states": calibration_states,
        "metrics": metrics,
        "promotion_package_ready": packet.status == CALIBRATION_CERTIFIED_PASS,
        "counting_basis": EXACT_ARTIFACT_COHORT_ONLY,
        "can_execute": False,
    }


def run_prop_certification_audit(req: PropCertificationAuditRequest, *, db: Any) -> dict[str, Any]:
    wanted = {str(value).strip().upper() for value in req.routes if str(value).strip()}
    artifacts = _artifact_rows(db, include_inactive=req.include_inactive)
    rows: list[dict[str, Any]] = []
    for artifact in artifacts:
        token = _token(str(artifact.get("sport")), str(artifact.get("stat_type")))
        if wanted and token not in wanted:
            continue
        predictions = _prediction_rows(db, artifact)
        outcomes = _outcome_map(db, [str(row["prediction_id"]) for row in predictions if row.get("prediction_id")])
        observations, counts = build_independent_observations(predictions, outcomes)
        audit = audit_artifact_certification(artifact, observations)
        audit.update({
            "artifact_registry_lifecycle_state": artifact.get("lifecycle_state"),
            "artifact_registry_promoted": bool(artifact.get("promoted")),
            "artifact_registry_active": bool(artifact.get("active")),
            "artifact_registry_certification_id": artifact.get("certification_id"),
            "source_provenance_ready": bool(artifact.get("training_dataset_hash") and artifact.get("training_code_sha")),
            **counts,
        })
        rows.append(audit)
    rows.sort(key=lambda row: (str(row.get("sport")), str(row.get("stat_type")), str(row.get("model_artifact_version"))))
    declared = [
        ROUTE_POLICY_STATUS[key].as_dict()
        for key in sorted(ROUTE_POLICY_STATUS)
        if not wanted or _token(*key) in wanted
    ]
    return {
        "status": "PROP_CERTIFICATION_AUDIT_COMPLETE",
        "artifact_rows": rows,
        "route_policy_inventory": declared,
        "reviewed_policy_count": len(REVIEWED_ROUTE_POLICIES),
        "certified_pass_n": sum(1 for row in rows if row.get("status") == CALIBRATION_CERTIFIED_PASS),
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE", "EXACT_ARTIFACT_COHORT_ONLY", "POLICY_REVIEW_REQUIRED",
    "REVIEWED_ROUTE_POLICIES", "ROUTE_POLICY_STATUS", "PropCertificationAuditRequest",
    "RoutePolicyStatus", "audit_artifact_certification", "build_independent_observations",
    "run_prop_certification_audit",
]
