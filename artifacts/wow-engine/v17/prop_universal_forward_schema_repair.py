"""Live-schema and artifact-cohort compatibility for universal V17 prop evidence.

The governed snapshot table does not expose team/opponent. The prediction ledger
uses ``calibration_version`` as calibrator identity. Most importantly, forward
calibration maturity must be computed per immutable model artifact/checksum/
feature-schema/calibrator cohort; aggregate route counts are audit-only and may
never satisfy certification thresholds.
"""
from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, uuid5

import v17.prop_universal_forward_evidence as runtime

_BASE_PREDICTION_PAYLOAD = runtime._prediction_payload


def _eligible_snapshots_live_schema(
    db: Any,
    *,
    sport: str,
    stat_type: str,
    limit: int,
    now: Any,
) -> list[dict[str, Any]]:
    rows = runtime._db_call(
        f"wow_prop_evidence_snapshots.select_universal_{sport.lower()}_{stat_type.lower()}",
        lambda: db.table("wow_prop_evidence_snapshots")
        .select(
            "source_snapshot_id,captured_at,event_id,event_start_time,sport,player,"
            "stat_type,line,hydration_status,blockers"
        )
        .eq("sport", sport)
        .eq("stat_type", stat_type)
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
        key = runtime.thesis_key(row)
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


def _artifact_key(row: dict[str, Any]) -> tuple[str, str, str, str, str] | None:
    feature_schema = str(row.get("feature_schema_version") or "").strip()
    model_family = str(row.get("model_family") or "").strip()
    artifact = str(row.get("model_artifact_version") or "").strip()
    checksum = str(row.get("model_artifact_checksum") or "").strip()
    calibration_version = str(row.get("calibration_version") or "").strip()
    if not all((feature_schema, model_family, artifact, checksum, calibration_version)):
        return None
    return feature_schema, model_family, artifact, checksum, calibration_version


def _prediction_payload_live_schema(**kwargs: Any):
    payload, blockers = _BASE_PREDICTION_PAYLOAD(**kwargs)
    if payload is None:
        return payload, blockers

    # The certification object calls this calibrator_version; the persisted
    # prediction ledger column is calibration_version.
    payload.pop("calibrator_version", None)
    if not str(payload.get("calibration_version") or "").strip():
        payload["calibration_version"] = "UNAVAILABLE_FORWARD_EVIDENCE"

    thesis = runtime.thesis_key(kwargs["snapshot"])
    artifact = _artifact_key(payload)
    direction = str(kwargs["direction"]).upper()
    if thesis is None or artifact is None:
        return None, tuple([*blockers, "FORWARD_ARTIFACT_PINNED_IDENTITY_INCOMPLETE"])

    identity = "|".join((
        kwargs["sport"],
        kwargs["stat_type"],
        *thesis,
        direction,
        *artifact,
    ))
    payload["prediction_id"] = str(uuid5(NAMESPACE_URL, f"wow-v17-prop-forward-artifact:{identity}"))
    return payload, blockers


def _existing_thesis_directions(_db: Any, _sport: str, _stat_type: str):
    """Do not pre-skip a thesis before the scorer reveals artifact identity.

    Repeat runs are deduplicated by the artifact-pinned deterministic prediction
    id + Supabase upsert. A new artifact is therefore allowed to collect its own
    forward observation on the same still-pregame sporting thesis.
    """
    return set()


def _route_readiness(db: Any, sport: str, stat_type: str) -> dict[str, Any]:
    predictions = runtime._paginate(
        f"wow_predictions.select_universal_artifact_cohorts_{sport.lower()}_{stat_type.lower()}",
        lambda: db.table("wow_predictions")
        .select(
            "prediction_id,event_id,player,stat_type,line,direction,feature_schema_version,"
            "model_family,model_artifact_version,model_artifact_checksum,calibration_version"
        )
        .eq("sport", sport)
        .eq("stat_type", stat_type)
        .eq("model_provider_identity", runtime.PROVIDER),
    )
    ids = [str(row["prediction_id"]) for row in predictions if row.get("prediction_id")]
    settled = runtime._settled_ids(db, ids) if ids else set()

    all_theses = {key for row in predictions if (key := runtime.thesis_key(row)) is not None}
    all_settled_theses = {
        key
        for row in predictions
        if str(row.get("prediction_id")) in settled
        if (key := runtime.thesis_key(row)) is not None
    }

    grouped: dict[tuple[str, str, str, str, str], dict[str, set[Any]]] = {}
    for row in predictions:
        thesis = runtime.thesis_key(row)
        artifact = _artifact_key(row)
        if thesis is None or artifact is None:
            continue
        bucket = grouped.setdefault(artifact, {"predicted": set(), "settled": set()})
        bucket["predicted"].add(thesis)
        if str(row.get("prediction_id")) in settled:
            bucket["settled"].add(thesis)

    cohorts: list[dict[str, Any]] = []
    for artifact, counts in sorted(grouped.items()):
        feature_schema, model_family, version, checksum, calibration_version = artifact
        cohorts.append({
            "feature_schema_version": feature_schema,
            "model_family": model_family,
            "model_artifact_version": version,
            "model_artifact_checksum": checksum,
            "calibration_version": calibration_version,
            "forward_prediction_n": len(counts["predicted"]),
            "forward_settled_n": len(counts["settled"]),
            "forward_unsettled_n": max(0, len(counts["predicted"]) - len(counts["settled"])),
            "counting_basis": "UNIQUE_EVENT_PLAYER_STAT_LINE_THESIS_WITHIN_EXACT_ARTIFACT",
            "can_execute": False,
        })

    return {
        "sport": sport,
        "stat_type": stat_type,
        # Cross-artifact numbers are operational coverage only.
        "forward_prediction_n": len(all_theses),
        "forward_settled_n": len(all_settled_theses),
        "forward_unsettled_n": max(0, len(all_theses) - len(all_settled_theses)),
        "artifact_cohorts": cohorts,
        "counting_basis": "UNIQUE_EVENT_PLAYER_STAT_LINE_THESIS",
        "certification_counting_basis": "EXACT_ARTIFACT_COHORT_ONLY",
        "aggregate_counts_are_certification_authority": False,
        "calibration_or_certification_performed": False,
        "can_execute": False,
    }


def install() -> None:
    runtime._eligible_snapshots = _eligible_snapshots_live_schema
    runtime._prediction_payload = _prediction_payload_live_schema
    runtime._existing_thesis_directions = _existing_thesis_directions
    runtime._route_readiness = _route_readiness


install()


__all__ = ["install"]
