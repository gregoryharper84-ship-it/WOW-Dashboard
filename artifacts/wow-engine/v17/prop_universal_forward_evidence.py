"""Universal exact-route forward-evidence orchestration for V17 props.

This module does not certify, promote, publish, rank, or execute anything.  It
makes every declared prop route and every required V17 sport visible, dispatches
routes to the narrow evidence collector that owns them, and adds a generic
exact-route collector for fitted scalar routes that already use the canonical
WOW prop scorer.

Production/calibration authority is never inferred from evidence collection.
can_execute=false unconditionally.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from v17.cross_sport_certification_inventory import CERTIFICATION_SPORTS
from v17.fantasy_score_forward_cohort_runtime import (
    FantasyScoreForwardCohortRequest,
    LANE_SPECS,
    run_fantasy_score_forward_cohort,
)
from v17.prop_capability_manifest import DECLARED_PROP_LANES, normalize_prop_sport
from v17.prop_forward_cohort_runtime import PropForwardCohortRequest, run_prop_forward_cohort
from v17.prop_route_lifecycle import FEATURE_SCHEMA_VERSION

CAN_EXECUTE = False
PROVIDER = "WOW_PROP_FITTED_MODEL_V1"
EVIDENCE_SOURCE_KIND = "IMMUTABLE_PREGAME_SETTLED"
DIRECTIONS = ("MORE", "LESS")
PAGE_SIZE = 1000
IN_FILTER_CHUNK_SIZE = 200

COLLECTOR_STRIKEOUT = "LEGACY_STRIKEOUT_FORWARD_COHORT"
COLLECTOR_FANTASY = "FANTASY_SCORE_FORWARD_COHORT"
COLLECTOR_GENERIC = "GENERIC_EXACT_ROUTE_FORWARD_COHORT"
COLLECTOR_SEPARATE = "SEPARATE_LANE_CONTRACT"
COLLECTOR_NONE = "NO_COLLECTOR"

COLLECTING = "FORWARD_EVIDENCE_COLLECTING"
COLLECTION_AVAILABLE = "FORWARD_EVIDENCE_COLLECTION_AVAILABLE"
SEPARATE_CONTRACT_REQUIRED = "SEPARATE_FORWARD_CONTRACT_REQUIRED"
NO_CURRENT_PROP_CATEGORY_DECLARED = "NO_CURRENT_PROP_CATEGORY_DECLARED"

STRIKEOUT_ROUTE = ("MLB", "PITCHER_STRIKEOUTS")
SEPARATE_ROUTES = {("MLB", "1ST_INNING_PITCHES_THROWN")}
FANTASY_ROUTE_TO_LANE = {
    (spec.sport, spec.stat_type): lane for lane, spec in LANE_SPECS.items()
}


class UniversalPropForwardEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    routes: list[str] = Field(default_factory=list)
    max_snapshots_per_route: int = Field(default=25, ge=1, le=100)


@dataclass(frozen=True)
class ForwardRouteInventoryRow:
    sport: str
    stat_type: str
    collector: str
    status: str
    blocker: str | None
    controlling_specialist: str | None
    declared_lane_status: str | None
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def route_key(sport: str, stat_type: str) -> tuple[str, str]:
    return normalize_prop_sport(sport), str(stat_type or "").strip().upper()


def route_token(sport: str, stat_type: str) -> str:
    s, stat = route_key(sport, stat_type)
    return f"{s}:{stat}"


def _collector_for(key: tuple[str, str]) -> tuple[str, str, str | None]:
    if key == STRIKEOUT_ROUTE:
        return COLLECTOR_STRIKEOUT, COLLECTING, None
    if key in FANTASY_ROUTE_TO_LANE:
        return COLLECTOR_FANTASY, COLLECTION_AVAILABLE, None
    if key in SEPARATE_ROUTES:
        return (
            COLLECTOR_SEPARATE,
            SEPARATE_CONTRACT_REQUIRED,
            "EXACT_ROUTE_SEPARATE_FORWARD_CONTRACT_REQUIRED",
        )
    if key in DECLARED_PROP_LANES:
        return COLLECTOR_GENERIC, COLLECTION_AVAILABLE, None
    return COLLECTOR_NONE, NO_CURRENT_PROP_CATEGORY_DECLARED, "PROP_ROUTE_NOT_DECLARED"


def build_forward_evidence_inventory(
    *,
    sports: Iterable[str] = CERTIFICATION_SPORTS,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen_sports: set[str] = set()
    for key, capability in sorted(DECLARED_PROP_LANES.items()):
        collector, status, blocker = _collector_for(key)
        output.append(
            ForwardRouteInventoryRow(
                sport=key[0],
                stat_type=key[1],
                collector=collector,
                status=status,
                blocker=blocker,
                controlling_specialist=capability.controlling_specialist,
                declared_lane_status=capability.lane_status,
            ).as_dict()
        )
        seen_sports.add(key[0])

    for raw_sport in sports:
        sport = normalize_prop_sport(raw_sport)
        if sport in seen_sports:
            continue
        output.append(
            ForwardRouteInventoryRow(
                sport=sport,
                stat_type="__SPORT_PROP_CATEGORY_INVENTORY__",
                collector=COLLECTOR_NONE,
                status=NO_CURRENT_PROP_CATEGORY_DECLARED,
                blocker="NO_CURRENT_PROP_CATEGORY_DECLARED",
                controlling_specialist=None,
                declared_lane_status=None,
            ).as_dict()
        )
    output.sort(key=lambda row: (row["sport"], row["stat_type"]))
    return output


def _db_call(boundary: str, call: Callable[[], Any]) -> Any:
    try:
        return call()
    except Exception as exc:
        raise RuntimeError(f"{boundary}: {type(exc).__name__}") from exc


def _paginate(boundary: str, build: Callable[[], Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        page = _db_call(
            boundary,
            lambda start=start: build().range(start, start + PAGE_SIZE - 1).execute().data or [],
        )
        batch = [dict(row) for row in page]
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
        start += PAGE_SIZE


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def _aware(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


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


def _eligible_snapshots(
    db: Any,
    *,
    sport: str,
    stat_type: str,
    limit: int,
    now: datetime,
) -> list[dict[str, Any]]:
    rows = _db_call(
        f"wow_prop_evidence_snapshots.select_universal_{sport.lower()}_{stat_type.lower()}",
        lambda: db.table("wow_prop_evidence_snapshots")
        .select(
            "source_snapshot_id,captured_at,event_id,event_start_time,sport,player,team,opponent,"
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
        captured = _aware(row.get("captured_at"))
        event_start = _aware(row.get("event_start_time"))
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


def _existing_thesis_directions(db: Any, sport: str, stat_type: str) -> set[tuple[tuple[str, str, str, str], str]]:
    rows = _paginate(
        f"wow_predictions.select_universal_existing_{sport.lower()}_{stat_type.lower()}",
        lambda: db.table("wow_predictions")
        .select("event_id,player,stat_type,line,direction,model_provider_identity")
        .eq("sport", sport)
        .eq("stat_type", stat_type)
        .eq("model_provider_identity", PROVIDER),
    )
    return {
        (key, str(row.get("direction") or "").upper())
        for row in rows
        if (key := thesis_key(row)) is not None
        if str(row.get("direction") or "").upper() in DIRECTIONS
    }


def _extract_output(scored: dict[str, Any], direction: str) -> dict[str, Any] | None:
    for name in ("prediction", "research_model_output", "candidate_model_output"):
        value = scored.get(name)
        if isinstance(value, dict):
            output = dict(value)
            break
    else:
        output = dict(scored)
    raw = output.get("raw_model_probability")
    if raw is None:
        raw = output.get("raw_specialist_probability")
    if raw is None:
        raw = output.get("raw_probability_more" if direction == "MORE" else "raw_probability_less")
    if raw is None:
        return None
    output["raw_model_probability"] = raw
    return output


def _prediction_id(sport: str, stat_type: str, thesis: tuple[str, str, str, str], direction: str) -> str:
    identity = "|".join((sport, stat_type, *thesis, direction))
    return str(uuid5(NAMESPACE_URL, f"wow-v17-universal-forward:{identity}"))


def _prediction_payload(
    *,
    sport: str,
    stat_type: str,
    snapshot: dict[str, Any],
    direction: str,
    scored: dict[str, Any],
    now: datetime,
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    output = _extract_output(scored, direction)
    blockers: list[str] = []
    if output is None:
        return None, ("FORWARD_MODEL_OUTPUT_MISSING",)

    model_timestamp = _aware(output.get("model_timestamp") or scored.get("model_timestamp"))
    event_start = _aware(snapshot.get("event_start_time"))
    captured_at = _aware(snapshot.get("captured_at"))
    if model_timestamp is None:
        blockers.append("FORWARD_MODEL_TIMESTAMP_MISSING")
    if event_start is None or captured_at is None:
        blockers.append("FORWARD_EVENT_OR_CAPTURE_TIMESTAMP_INVALID")
    elif captured_at >= event_start or now >= event_start:
        blockers.append("FORWARD_NOT_PREGAME")
    if model_timestamp is not None and event_start is not None and model_timestamp >= event_start:
        blockers.append("FORWARD_MODEL_TIMESTAMP_NOT_PREGAME")

    provider = str(output.get("model_provider_identity") or output.get("provider_identity") or PROVIDER)
    if provider != PROVIDER:
        blockers.append("FORWARD_MODEL_PROVIDER_IDENTITY_MISMATCH")
    model_family = str(output.get("model_family") or "").strip()
    artifact_version = str(output.get("model_artifact_version") or output.get("model_version") or "").strip()
    artifact_checksum = str(output.get("model_artifact_checksum") or output.get("model_source_sha256") or "").strip()
    if not model_family:
        blockers.append("FORWARD_MODEL_FAMILY_MISSING")
    if not artifact_version:
        blockers.append("FORWARD_MODEL_ARTIFACT_VERSION_MISSING")
    if not artifact_checksum:
        blockers.append("FORWARD_MODEL_ARTIFACT_CHECKSUM_MISSING")

    thesis = thesis_key(snapshot)
    if thesis is None:
        blockers.append("FORWARD_THESIS_IDENTITY_INVALID")
    try:
        raw_probability = float(output["raw_model_probability"])
    except (TypeError, ValueError, KeyError):
        raw_probability = -1.0
    if not 0.0 < raw_probability < 1.0:
        blockers.append("FORWARD_RAW_PROBABILITY_INVALID")

    if blockers or thesis is None or model_timestamp is None:
        return None, tuple(dict.fromkeys(blockers))

    capability = DECLARED_PROP_LANES[(sport, stat_type)]
    payload: dict[str, Any] = {
        "prediction_id": _prediction_id(sport, stat_type, thesis, direction),
        "event_id": snapshot["event_id"],
        "event_start_time": snapshot["event_start_time"],
        "model_timestamp": model_timestamp.isoformat(),
        "player": snapshot.get("player"),
        "team": snapshot.get("team"),
        "opponent": snapshot.get("opponent"),
        "sport": sport,
        "market_type": "PLAYER_PROP",
        "stat_type": stat_type,
        "line": float(snapshot["line"]),
        "direction": direction,
        "raw_model_probability": raw_probability,
        "source_snapshot_id": snapshot["source_snapshot_id"],
        "model_provider_identity": provider,
        "model_family": model_family,
        "model_artifact_version": artifact_version,
        "model_artifact_checksum": artifact_checksum,
        "feature_schema_version": str(output.get("feature_schema_version") or FEATURE_SCHEMA_VERSION),
        "controlling_specialist": capability.controlling_specialist,
        "evidence_source_kind": EVIDENCE_SOURCE_KIND,
        "probability_publishable": bool(scored.get("probability_publishable") is True),
        "money_lane_status": "PAYOUT_UNRESOLVED",
        "locked_at": now.isoformat(),
        "blockers": list(scored.get("blockers") or []),
    }
    if scored.get("research_only") is True:
        payload["probability_publishable"] = False
    for key in (
        "independent_model_probability", "effective_sample_size", "calibration_status",
        "calibration_method", "calibration_version", "calibrator_version",
        "calibration_training_n", "calibration_parent_cohort", "calibration_fit_start",
        "calibration_fit_end", "bounds_method_version", "calibrated_probability",
        "calibrated_probability_lower_bound", "calibrated_probability_upper_bound",
        "probability_ceiling", "model_bundle_fingerprint", "model_artifact_lifecycle_state",
        "feature_transform_version", "feature_snapshot_hash", "training_dataset_hash",
        "training_code_sha", "specialist_version", "certification_id", "distribution_type",
        "probability_more", "probability_less", "push_probability",
    ):
        if output.get(key) is not None:
            payload[key] = output[key]
    return payload, ()


def _persist_prediction(db: Any, payload: dict[str, Any]) -> None:
    _db_call(
        "wow_predictions.upsert_universal_forward_prediction",
        lambda: db.table("wow_predictions").upsert(
            payload, on_conflict="prediction_id", ignore_duplicates=True
        ).execute(),
    )


def _route_predictions(db: Any, sport: str, stat_type: str) -> list[dict[str, Any]]:
    return _paginate(
        f"wow_predictions.select_universal_evidence_{sport.lower()}_{stat_type.lower()}",
        lambda: db.table("wow_predictions")
        .select("prediction_id,event_id,player,stat_type,line,direction,source_snapshot_id")
        .eq("sport", sport)
        .eq("stat_type", stat_type)
        .eq("model_provider_identity", PROVIDER),
    )


def _settled_ids(db: Any, prediction_ids: list[str]) -> set[str]:
    settled: set[str] = set()
    for chunk in _chunks(prediction_ids, IN_FILTER_CHUNK_SIZE):
        rows = _paginate(
            "wow_outcomes.select_universal_settled_predictions",
            lambda chunk=chunk: db.table("wow_outcomes")
            .select("prediction_id,actual_stat,settlement_timestamp,void")
            .in_("prediction_id", chunk),
        )
        settled.update(
            str(row["prediction_id"])
            for row in rows
            if row.get("prediction_id")
            and row.get("actual_stat") is not None
            and row.get("settlement_timestamp") is not None
            and row.get("void") is not True
        )
    return settled


def _route_readiness(db: Any, sport: str, stat_type: str) -> dict[str, Any]:
    predictions = _route_predictions(db, sport, stat_type)
    ids = [str(row["prediction_id"]) for row in predictions if row.get("prediction_id")]
    settled = _settled_ids(db, ids) if ids else set()
    theses = {key for row in predictions if (key := thesis_key(row)) is not None}
    settled_theses = {
        key
        for row in predictions
        if str(row.get("prediction_id")) in settled
        if (key := thesis_key(row)) is not None
    }
    return {
        "sport": sport,
        "stat_type": stat_type,
        "forward_prediction_n": len(theses),
        "forward_settled_n": len(settled_theses),
        "forward_unsettled_n": max(0, len(theses) - len(settled_theses)),
        "counting_basis": "UNIQUE_EVENT_PLAYER_STAT_LINE_THESIS",
        "calibration_or_certification_performed": False,
        "can_execute": False,
    }


def run_generic_forward_route(
    *,
    sport: str,
    stat_type: str,
    max_snapshots: int,
    db: Any,
    market_api: Any,
    now: datetime,
) -> dict[str, Any]:
    snapshots = _eligible_snapshots(
        db, sport=sport, stat_type=stat_type, limit=max_snapshots, now=now
    )
    existing = _existing_thesis_directions(db, sport, stat_type)
    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        thesis = thesis_key(snapshot)
        assert thesis is not None
        for direction in DIRECTIONS:
            key = (thesis, direction)
            if key in existing:
                rows.append({
                    "source_snapshot_id": snapshot["source_snapshot_id"],
                    "direction": direction,
                    "status": "SKIPPED_ALREADY_CAPTURED_THESIS",
                    "can_execute": False,
                })
                continue
            identity = {
                "event_id": snapshot.get("event_id"),
                "event_start_time": snapshot.get("event_start_time"),
                "sport": sport,
                "player": snapshot.get("player"),
                "stat_type": stat_type,
                "line": snapshot.get("line"),
                "source_snapshot_id": snapshot.get("source_snapshot_id"),
                "direction": direction,
            }
            try:
                scored = market_api.score_prop(
                    market_api.ScorePropRequest(**identity), "WOW_BETTING_ENGINE"
                )
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                rows.append({
                    "source_snapshot_id": snapshot["source_snapshot_id"],
                    "direction": direction,
                    "status": "HELD_SCORER",
                    "detail": detail,
                    "probability_publishable": False,
                    "can_execute": False,
                })
                continue
            except Exception as exc:
                rows.append({
                    "source_snapshot_id": snapshot["source_snapshot_id"],
                    "direction": direction,
                    "status": "HELD_SCORER_EXCEPTION",
                    "error_type": type(exc).__name__,
                    "probability_publishable": False,
                    "can_execute": False,
                })
                continue
            payload, package_blockers = _prediction_payload(
                sport=sport,
                stat_type=stat_type,
                snapshot=snapshot,
                direction=direction,
                scored=dict(scored),
                now=now,
            )
            if payload is None:
                rows.append({
                    "source_snapshot_id": snapshot["source_snapshot_id"],
                    "direction": direction,
                    "status": "HELD_FORWARD_PACKAGE_INVALID",
                    "blockers": list(package_blockers),
                    "probability_publishable": False,
                    "can_execute": False,
                })
                continue
            _persist_prediction(db, payload)
            existing.add(key)
            rows.append({
                "source_snapshot_id": snapshot["source_snapshot_id"],
                "direction": direction,
                "prediction_id": payload["prediction_id"],
                "status": "CAPTURED_FORWARD",
                "probability_publishable": payload["probability_publishable"],
                "can_execute": False,
            })

    expected = len(snapshots) * len(DIRECTIONS)
    completed = sum(row["status"] == "CAPTURED_FORWARD" for row in rows)
    skipped = sum(row["status"] == "SKIPPED_ALREADY_CAPTURED_THESIS" for row in rows)
    held = len(rows) - completed - skipped
    balanced = len(rows) == expected == completed + skipped + held
    return {
        "sport": sport,
        "stat_type": stat_type,
        "collector": COLLECTOR_GENERIC,
        "status": "COMPLETED" if balanced else "RECONCILIATION_FAILED",
        "snapshots_considered": len(snapshots),
        "directions_considered": expected,
        "captured_forward_predictions": completed,
        "skipped_already_captured": skipped,
        "held": held,
        "rows": rows,
        "row_reconciliation": {
            "rows_in": expected,
            "rows_completed": completed,
            "rows_skipped": skipped,
            "rows_held": held,
            "balanced": balanced,
            "can_execute": False,
        },
        "calibration_readiness": _route_readiness(db, sport, stat_type),
        "settlement_adapter": "BACKEND_DERIVED_SCALAR_FROM_FROZEN_LINE_DIRECTION_V1",
        "settlement_actual_stat_source": "OFFICIAL_STAT_REQUIRED",
        "calibration_or_certification_performed": False,
        "can_execute": False,
    }


def _parse_requested_routes(tokens: list[str]) -> list[tuple[str, str]]:
    if not tokens:
        return sorted(DECLARED_PROP_LANES)
    routes: list[tuple[str, str]] = []
    for token in tokens:
        parts = str(token or "").split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"invalid prop route token: {token!r}; expected SPORT:STAT_TYPE")
        key = route_key(parts[0], parts[1])
        if key not in DECLARED_PROP_LANES:
            raise ValueError(f"undeclared prop route: {route_token(*key)}")
        if key not in routes:
            routes.append(key)
    return routes


def run_universal_prop_forward_evidence(
    req: UniversalPropForwardEvidenceRequest,
    *,
    db: Any,
    market_api: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    requested = _parse_requested_routes(req.routes)
    route_results: list[dict[str, Any]] = []

    fantasy_requested = [FANTASY_ROUTE_TO_LANE[key] for key in requested if key in FANTASY_ROUTE_TO_LANE]
    fantasy_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    if fantasy_requested:
        fantasy = run_fantasy_score_forward_cohort(
            FantasyScoreForwardCohortRequest(
                lanes=fantasy_requested,
                max_snapshots_per_lane=req.max_snapshots_per_route,
            ),
            db=db,
            market_api=market_api,
            now=now,
        )
        for lane_result in fantasy["lanes"]:
            spec = LANE_SPECS[lane_result["lane"]]
            fantasy_by_key[(spec.sport, spec.stat_type)] = lane_result

    for key in requested:
        sport, stat_type = key
        collector, inventory_status, blocker = _collector_for(key)
        if collector == COLLECTOR_STRIKEOUT:
            result = run_prop_forward_cohort(
                PropForwardCohortRequest(max_snapshots=req.max_snapshots_per_route),
                db=db,
                market_api=market_api,
                now=now,
            )
            route_results.append({
                "sport": sport,
                "stat_type": stat_type,
                "collector": collector,
                "status": result["run_status"],
                "result": result,
                "can_execute": False,
            })
        elif collector == COLLECTOR_FANTASY:
            result = fantasy_by_key[key]
            route_results.append({
                "sport": sport,
                "stat_type": stat_type,
                "collector": collector,
                "status": "COMPLETED" if result["row_reconciliation"]["balanced"] else "RECONCILIATION_FAILED",
                "result": result,
                "can_execute": False,
            })
        elif collector == COLLECTOR_GENERIC:
            route_results.append(
                run_generic_forward_route(
                    sport=sport,
                    stat_type=stat_type,
                    max_snapshots=req.max_snapshots_per_route,
                    db=db,
                    market_api=market_api,
                    now=now,
                )
            )
        else:
            route_results.append({
                "sport": sport,
                "stat_type": stat_type,
                "collector": collector,
                "status": inventory_status,
                "blockers": [blocker] if blocker else [],
                "can_execute": False,
            })

    route_keys = {(row["sport"], row["stat_type"]) for row in route_results}
    requested_keys = set(requested)
    balanced = route_keys == requested_keys and len(route_results) == len(requested)
    return {
        "terminal": True,
        "run_status": "COMPLETED" if balanced else "RECONCILIATION_FAILED",
        "routes_requested": len(requested),
        "routes_terminated": len(route_results),
        "route_reconciliation_balanced": balanced,
        "route_results": route_results,
        "full_inventory": build_forward_evidence_inventory(),
        "calibration_performed": False,
        "certification_performed": False,
        "promotion_performed": False,
        "production_registration_performed": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "COLLECTOR_FANTASY",
    "COLLECTOR_GENERIC",
    "COLLECTOR_SEPARATE",
    "COLLECTOR_STRIKEOUT",
    "EVIDENCE_SOURCE_KIND",
    "ForwardRouteInventoryRow",
    "UniversalPropForwardEvidenceRequest",
    "build_forward_evidence_inventory",
    "route_key",
    "route_token",
    "run_generic_forward_route",
    "run_universal_prop_forward_evidence",
    "thesis_key",
]
