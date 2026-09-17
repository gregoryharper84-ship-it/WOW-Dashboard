"""Artifact-pinned statistical-independence guard for Fantasy Score evidence.

A calibration observation is one exact pregame sporting thesis inside one exact
model/scoring/calibrator cohort. Refreshed source snapshots and MORE/LESS twins
must not inflate calibration N, while a genuinely new immutable model artifact
must be allowed to collect its own forward evidence on a still-pregame thesis.

No scoring, publication, certification, promotion, or execution authority is
changed. ``can_execute`` remains false.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import v17.fantasy_score_forward_cohort_runtime as runtime


def _normalized_line(value: Any) -> str | None:
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except (InvalidOperation, TypeError, ValueError):
        return None


def thesis_key(row: dict[str, Any]) -> tuple[str, str, str, str] | None:
    event_id = str(row.get("event_id") or "").strip()
    player = " ".join(str(row.get("player") or "").split()).casefold()
    stat_type = str(row.get("stat_type") or "").strip().upper()
    line = _normalized_line(row.get("line"))
    if not event_id or not player or not stat_type or line is None:
        return None
    return event_id, player, stat_type, line


def artifact_key(row: dict[str, Any]) -> tuple[str, str, str, str, str] | None:
    model_family = str(row.get("model_family") or row.get("market_family") or "").strip()
    artifact = str(row.get("model_artifact_version") or "").strip()
    checksum = str(row.get("model_artifact_checksum") or "").strip()
    scoring_profile = str(row.get("scoring_profile_id") or "").strip()
    scoring_hash = str(row.get("scoring_profile_sha256") or "").strip()
    if not all((model_family, artifact, checksum, scoring_profile, scoring_hash)):
        return None
    return model_family, artifact, checksum, scoring_profile, scoring_hash


def _eligible_snapshots(db: Any, spec: Any, limit: int, *, now: Any) -> list[dict[str, Any]]:
    # Keep this selector aligned with the live governed snapshot schema.
    rows = runtime._db_call(
        f"wow_prop_evidence_snapshots.select_fantasy_independent_{spec.lane.lower()}",
        lambda: db.table("wow_prop_evidence_snapshots")
        .select(
            "source_snapshot_id,captured_at,event_id,event_start_time,sport,player,"
            "stat_type,line,hydration_status,blockers"
        )
        .eq("sport", spec.sport)
        .eq("stat_type", spec.stat_type)
        .eq("hydration_status", "PASS")
        .gt("event_start_time", now.isoformat())
        .order("event_start_time")
        .order("captured_at")
        .limit(limit * 10)
        .execute().data or [],
    )
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw in rows:
        row = dict(raw)
        captured = runtime._aware(row.get("captured_at"))
        event_start = runtime._aware(row.get("event_start_time"))
        key = thesis_key(row)
        if row.get("blockers") or not row.get("source_snapshot_id") or key is None:
            continue
        if captured is None or event_start is None or captured >= event_start or event_start <= now:
            continue
        if key in seen:
            continue
        seen.add(key)
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def _existing_keys(_db: Any, _spec: Any) -> set[tuple[str, str]]:
    """Do not pre-skip by source snapshot before the scorer reveals artifact identity.

    The base runtime's snapshot/direction key would cause a newly fitted artifact
    to inherit an older artifact's evidence. The artifact-pinned prediction id
    below is the idempotency boundary instead; Supabase upsert remains the final
    concurrency guard for repeat runs of the same artifact.
    """
    return set()


_BASE_BUILD_PREDICTION_PAYLOAD = runtime._build_prediction_payload


def _build_prediction_payload(**kwargs: Any):
    payload, blockers = _BASE_BUILD_PREDICTION_PAYLOAD(**kwargs)
    if payload is None:
        return payload, blockers
    snapshot = kwargs["snapshot"]
    spec = kwargs["spec"]
    direction = str(kwargs["direction"]).upper()
    thesis = thesis_key(snapshot)
    artifact = artifact_key(payload)
    if thesis is None or artifact is None:
        return None, [*blockers, "FANTASY_SCORE_ARTIFACT_PINNED_IDENTITY_INCOMPLETE"]

    # Candidate-only is a real calibrator state, not permission to pool it with
    # a future calibrated artifact. Persist it explicitly in the ledger column
    # that already owns calibrator identity for prediction rows.
    payload.setdefault("calibration_version", "UNAVAILABLE_CANDIDATE_ONLY")
    identity = "|".join((
        spec.lane,
        spec.sport,
        spec.stat_type,
        *thesis,
        direction,
        *artifact,
        str(payload["calibration_version"]),
    ))
    payload["prediction_id"] = str(uuid5(NAMESPACE_URL, f"wow-v17-fantasy-forward-artifact:{identity}"))
    return payload, blockers


def _lane_readiness(db: Any, spec: Any) -> dict[str, Any]:
    predictions = runtime._paginate(
        f"wow_predictions.select_fantasy_independent_evidence_{spec.lane.lower()}",
        lambda: db.table("wow_predictions")
        .select(
            "prediction_id,source_snapshot_id,event_id,event_start_time,model_timestamp,"
            "player,stat_type,line,direction,model_family,model_artifact_version,"
            "model_artifact_checksum,scoring_profile_id,scoring_profile_sha256,calibration_version"
        )
        .eq("fantasy_score_lane", spec.lane)
        .eq("market_family", spec.market_family)
        .eq("evidence_source_kind", runtime.EVIDENCE_SOURCE_KIND),
    )
    ids = [str(row["prediction_id"]) for row in predictions if row.get("prediction_id")]
    settled = runtime._settled_ids(db, ids) if ids else set()

    all_theses = {key for row in predictions if (key := thesis_key(row)) is not None}
    all_settled_theses = {
        key
        for row in predictions
        if str(row.get("prediction_id")) in settled
        if (key := thesis_key(row)) is not None
    }

    grouped: dict[tuple[str, str, str, str, str, str], dict[str, set[Any]]] = {}
    for row in predictions:
        thesis = thesis_key(row)
        artifact = artifact_key(row)
        calibration_version = str(row.get("calibration_version") or "UNAVAILABLE_CANDIDATE_ONLY")
        if thesis is None or artifact is None:
            continue
        cohort = (*artifact, calibration_version)
        bucket = grouped.setdefault(cohort, {"predicted": set(), "settled": set()})
        bucket["predicted"].add(thesis)
        if str(row.get("prediction_id")) in settled:
            bucket["settled"].add(thesis)

    artifact_cohorts: list[dict[str, Any]] = []
    for cohort, counts in sorted(grouped.items()):
        model_family, artifact, checksum, scoring_profile, scoring_hash, calibration_version = cohort
        artifact_cohorts.append({
            "model_family": model_family,
            "model_artifact_version": artifact,
            "model_artifact_checksum": checksum,
            "scoring_profile_id": scoring_profile,
            "scoring_profile_sha256": scoring_hash,
            "calibration_version": calibration_version,
            "forward_prediction_n": len(counts["predicted"]),
            "forward_settled_n": len(counts["settled"]),
            "counting_basis": "UNIQUE_EVENT_PLAYER_STAT_LINE_THESIS_WITHIN_EXACT_ARTIFACT",
            "can_execute": False,
        })

    strongest_settled_n = max((row["forward_settled_n"] for row in artifact_cohorts), default=0)
    status = "PHASE_A_FORWARD_COHORT_BUILDING"
    if strongest_settled_n >= runtime.PHASE_C_MIN_N:
        status = "PHASE_C_THRESHOLD_REACHED_CALIBRATION_EVIDENCE_BUILD_REQUIRED"
    elif strongest_settled_n >= runtime.PHASE_B_MIN_N:
        status = "PHASE_B_THRESHOLD_REACHED_CALIBRATION_EVIDENCE_BUILD_REQUIRED"

    return {
        "lane": spec.lane,
        "status": status,
        # Compatibility/audit totals only. Certification must use artifact_cohorts.
        "forward_prediction_source_n": len(all_theses),
        "forward_settled_source_n": len(all_settled_theses),
        "artifact_cohorts": artifact_cohorts,
        "strongest_single_artifact_settled_n": strongest_settled_n,
        "phase_b_min_settled_n": runtime.PHASE_B_MIN_N,
        "phase_c_min_settled_n": runtime.PHASE_C_MIN_N,
        "remaining_to_phase_b": max(0, runtime.PHASE_B_MIN_N - strongest_settled_n),
        "remaining_to_phase_c": max(0, runtime.PHASE_C_MIN_N - strongest_settled_n),
        "counting_basis": "UNIQUE_EVENT_PLAYER_STAT_LINE_THESIS",
        "certification_counting_basis": "EXACT_ARTIFACT_COHORT_ONLY",
        "aggregate_counts_are_certification_authority": False,
        "calibrator_fit_performed": False,
        "certification_performed": False,
        "promotion_performed": False,
        "probability_publishable": False,
        "can_execute": False,
    }


BASE_IMPLEMENTATIONS: dict[str, Any] = {}
_OVERRIDES = {
    "_eligible_snapshots": _eligible_snapshots,
    "_existing_keys": _existing_keys,
    "_build_prediction_payload": _build_prediction_payload,
    "_lane_readiness": _lane_readiness,
}


def install() -> None:
    for name, override in _OVERRIDES.items():
        BASE_IMPLEMENTATIONS.setdefault(name, getattr(runtime, name))
        setattr(runtime, name, override)


install()


__all__ = [
    "BASE_IMPLEMENTATIONS",
    "artifact_key",
    "install",
    "thesis_key",
]
