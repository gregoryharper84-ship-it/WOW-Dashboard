"""Governed screenshot/self-discovery -> evidence snapshot -> prop scoring bridge.

This boundary is acquisition/orchestration only. A caller may supply raw,
auditable pregame evidence, or omit evidence when the exact sport/stat route has
a certified backend automatic hydrator. The caller may never supply a model
probability, model artifact, calibration output, edge, or approval label.

Every row is preflighted for specialist, aggregate capability, and exact fitted
artifact before any expensive automatic acquisition. Evidence then follows one
canonical path: validate -> deterministic fingerprint -> immutable/idempotent
snapshot -> existing governed /score-prop model path.

Every row terminates exactly once. A bad/unsupported row cannot erase a sibling
row. can_execute is false unconditionally.
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Literal, Optional

from fastapi import Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from prop_auto_hydration import PropAutoHydrationError, auto_hydrate_prop_evidence
from qualification_policy_v2 import classify_prop_probability
from prop_terminal_reducer_v2 import EVENT_BLOCKERS, TRUE_MODEL_REJECTION_LABELS, reduce_prop_terminal


PROP_STAT_ALIASES: dict[tuple[str, str], str] = {
    ("MLB", "K"): "PITCHER_STRIKEOUTS",
    ("MLB", "KS"): "PITCHER_STRIKEOUTS",
    ("MLB", "SO"): "PITCHER_STRIKEOUTS",
    ("MLB", "STRIKEOUT"): "PITCHER_STRIKEOUTS",
    ("MLB", "STRIKEOUTS"): "PITCHER_STRIKEOUTS",
    ("MLB", "PITCHER_K"): "PITCHER_STRIKEOUTS",
    ("MLB", "PITCHER_KS"): "PITCHER_STRIKEOUTS",
}

PickSourceType = Literal[
    "SCREENSHOT",
    "PDF",
    "AUTONOMOUS_DISCOVERY",
    "PASTED_BOARD",
    "NORMALIZED",
]


class RawPropEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    captured_at: str
    game_log: list[float]
    box_score_log: list[dict[str, Any]]
    role_status: dict[str, Any]
    role_timestamp: str
    opportunity_ledger: dict[str, Any]
    source_timestamps: dict[str, str]
    evidence_version: str = "PROP_EVIDENCE_V1"
    rate_provenance: str


class PickRequestRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_key: Optional[str] = None
    event_id: str
    event_start_time: str
    sport: str
    player: str
    stat_type: str
    line: float
    direction: str
    evidence: Optional[RawPropEvidence] = None
    source_type: PickSourceType = "NORMALIZED"
    platform: Optional[str] = None
    league: Optional[str] = None
    opponent: Optional[str] = None
    source_capture_timestamp: Optional[str] = None
    seed: int = 0
    money_lane_status: str = "PAYOUT_UNRESOLVED"
    market_side_a: Optional[dict[str, Any]] = None
    market_side_b: Optional[dict[str, Any]] = None


class PickRequestBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: Optional[str] = None
    rows: list[PickRequestRow] = Field(min_length=1, max_length=50)


def _parse_aware(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{field}:INVALID_TIMESTAMP") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{field}:TIMEZONE_REQUIRED")
    return parsed.astimezone(timezone.utc)


def _canonical_stat(sport: str, stat_type: str) -> str:
    s = str(sport or "").strip().upper()
    raw = "_".join(str(stat_type or "").strip().upper().replace("-", " ").split())
    return PROP_STAT_ALIASES.get((s, raw), raw)


def _validate_evidence(row: PickRequestRow, canonical_stat: str) -> dict[str, Any]:
    if row.evidence is None:
        raise ValueError("EVIDENCE_MISSING_AFTER_ACQUISITION")

    now = datetime.now(timezone.utc)
    event_start = _parse_aware(row.event_start_time, "event_start_time")
    captured = _parse_aware(row.evidence.captured_at, "captured_at")
    role_ts = _parse_aware(row.evidence.role_timestamp, "role_timestamp")

    if event_start <= now:
        raise ValueError("EVENT_NOT_PREGAME")
    if captured >= event_start:
        raise ValueError("CAPTURE_NOT_PREGAME")
    if role_ts >= event_start:
        raise ValueError("ROLE_TIMESTAMP_NOT_PREGAME")
    if captured > now:
        raise ValueError("CAPTURE_TIMESTAMP_IN_FUTURE")

    if len(row.evidence.game_log) < 10:
        raise ValueError("L10_GAME_LOG_INCOMPLETE")
    if len(row.evidence.box_score_log) < 10:
        raise ValueError("L10_BOX_SCORE_LOG_INCOMPLETE")
    if any(
        not isinstance(v, (int, float))
        or isinstance(v, bool)
        or not math.isfinite(float(v))
        for v in row.evidence.game_log
    ):
        raise ValueError("GAME_LOG_NON_NUMERIC")
    if any(not isinstance(v, dict) or not v for v in row.evidence.box_score_log):
        raise ValueError("BOX_SCORE_LOG_INVALID")

    role_status = row.evidence.role_status
    role_label = str(role_status.get("status") or role_status.get("role") or "").strip()
    if not role_label:
        raise ValueError("ROLE_STATUS_MISSING")

    opportunity = row.evidence.opportunity_ledger
    opportunity_status = str(
        opportunity.get("status") or opportunity.get("gate_label") or ""
    ).strip().upper()
    if opportunity_status not in {"PASS", "COMPLETE", "READY"}:
        raise ValueError("OPPORTUNITY_LEDGER_NOT_READY")

    if not row.evidence.source_timestamps:
        raise ValueError("SOURCE_TIMESTAMPS_MISSING")
    if not str(row.evidence.rate_provenance).strip():
        raise ValueError("RATE_PROVENANCE_MISSING")
    if not str(row.evidence.evidence_version).strip():
        raise ValueError("EVIDENCE_VERSION_MISSING")

    normalized_source_timestamps: dict[str, str] = {}
    for source, timestamp in row.evidence.source_timestamps.items():
        if not str(source).strip():
            raise ValueError("SOURCE_IDENTITY_MISSING")
        source_ts = _parse_aware(timestamp, f"source_timestamps.{source}")
        if source_ts >= event_start:
            raise ValueError("SOURCE_TIMESTAMP_NOT_PREGAME")
        if source_ts > now:
            raise ValueError("SOURCE_TIMESTAMP_IN_FUTURE")
        normalized_source_timestamps[str(source).strip()] = source_ts.isoformat()

    return {
        "event_start_time": event_start.isoformat(),
        "captured_at": captured.isoformat(),
        "role_timestamp": role_ts.isoformat(),
        "source_timestamps": normalized_source_timestamps,
        "sport": str(row.sport).strip().upper(),
        "stat_type": canonical_stat,
        "player": " ".join(str(row.player).strip().split()),
        "event_id": str(row.event_id).strip(),
    }


def _snapshot_payload(
    row: PickRequestRow,
    normalized: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    if row.evidence is None:
        raise ValueError("EVIDENCE_MISSING_AFTER_ACQUISITION")

    fingerprint_input = {
        "event_id": normalized["event_id"],
        "event_start_time": normalized["event_start_time"],
        "sport": normalized["sport"],
        "player": normalized["player"],
        "stat_type": normalized["stat_type"],
        "line": float(row.line),
        "captured_at": normalized["captured_at"],
        "game_log": [float(v) for v in row.evidence.game_log],
        "box_score_log": row.evidence.box_score_log,
        "role_status": row.evidence.role_status,
        "role_timestamp": normalized["role_timestamp"],
        "opportunity_ledger": row.evidence.opportunity_ledger,
        "source_timestamps": normalized["source_timestamps"],
        "evidence_version": str(row.evidence.evidence_version).strip(),
        "rate_provenance": str(row.evidence.rate_provenance).strip(),
        "hydration_status": "PASS",
        "blockers": [],
        "can_execute": False,
    }
    canonical = json.dumps(
        fingerprint_input,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    fingerprint = sha256(canonical.encode("utf-8")).hexdigest()
    snapshot_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"wow-prop-evidence:{fingerprint}"))

    persisted = {
        "source_snapshot_id": snapshot_id,
        "captured_at": normalized["captured_at"],
        "event_id": normalized["event_id"],
        "event_start_time": normalized["event_start_time"],
        "sport": normalized["sport"],
        "player": normalized["player"],
        "stat_type": normalized["stat_type"],
        "line": float(row.line),
        "game_log": [float(v) for v in row.evidence.game_log],
        "box_score_log": row.evidence.box_score_log,
        "role_status": row.evidence.role_status,
        "role_timestamp": normalized["role_timestamp"],
        "opportunity_ledger": row.evidence.opportunity_ledger,
        "source_timestamps": normalized["source_timestamps"],
        "hydration_status": "PASS",
        "blockers": [],
        "evidence_version": str(row.evidence.evidence_version).strip(),
        "can_execute": False,
    }
    return snapshot_id, fingerprint, persisted


def _terminal(
    row_key: str,
    status: str,
    code: str,
    *,
    detail: Optional[dict[str, Any]] = None,
    snapshot_id: Optional[str] = None,
    acquisition: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    payload = detail or {}
    blocker_codes = [code]
    for key in ("blocker_code", "failure_class"):
        if payload.get(key):
            blocker_codes.append(str(payload[key]))
    if payload.get("blocker"):
        blocker_codes.append(str(payload["blocker"]))
    model_evaluated = bool(payload.get("model_evaluated") is True or payload.get("model_evidence"))
    if code in EVENT_BLOCKERS or str(payload.get("blocker") or "") in EVENT_BLOCKERS:
        proposed_label = "NO_PLAY"
        blocker_codes.append(str(payload.get("blocker") or code))
    elif code in TRUE_MODEL_REJECTION_LABELS:
        proposed_label = code
    else:
        proposed_label = str(payload.get("terminal_label") or "MODEL_UNAVAILABLE")
    decision = reduce_prop_terminal(
        proposed_label=proposed_label,
        blockers=blocker_codes,
        model_evaluated=model_evaluated,
    )
    effective_status = "REJECTED" if (decision.pick_rejected or decision.verdict_class == "EVENT_INVALIDATED") else "HELD"
    return {
        "row_key": row_key,
        "terminal_status": effective_status,
        "code": code,
        "terminal_label": decision.terminal_label,
        "verdict_class": decision.verdict_class,
        "model_evaluated": decision.model_evaluated,
        "pick_rejected": decision.pick_rejected,
        "infrastructure_blocked": decision.infrastructure_blocked,
        "blockers": list(decision.blockers),
        "source_snapshot_id": snapshot_id,
        "detail": payload,
        "acquisition": acquisition or {"mode": "NOT_COMPLETED", "can_execute": False},
        "probability_publishable": False,
        "can_execute": False,
    }


def _auto_hydration_hold(code: str) -> bool:
    return code in {
        "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE",
        "PROP_AUTO_HYDRATION_PROVIDER_UNAVAILABLE",
        "MLB_STARTER_STATUS_UNRESOLVED",
    }


def _completed_scored_outcome(
    *,
    row_key: str,
    scored: dict[str, Any],
    snapshot_id: str,
    fingerprint: str,
    acquisition: dict[str, Any],
) -> dict[str, Any]:
    prediction = scored.get("prediction") or {}
    payload = scored.get("probability_qualification")
    if isinstance(payload, dict) and payload.get("terminal_label"):
        terminal_label = str(payload["terminal_label"])
        confidence_tier = str(payload.get("confidence_tier") or "UNKNOWN")
        rank_eligible = bool(payload.get("rank_eligible"))
        model_supported = bool(payload.get("model_supported"))
        money_allowed = bool(payload.get("downstream_money_evaluation_allowed"))
        blockers = list(payload.get("blockers") or [])
    else:
        qualification = classify_prop_probability(
            calibrated_probability=prediction.get("calibrated_probability"),
            calibrated_lower_bound=prediction.get("calibrated_probability_lower_bound"),
            calibration_status=prediction.get("calibration_status"),
            blockers=prediction.get("data_gaps") or [],
            probability_publishable=bool(scored.get("probability_publishable")),
        )
        terminal_label = qualification.terminal_label
        confidence_tier = qualification.confidence_tier
        rank_eligible = qualification.rank_eligible
        model_supported = qualification.model_supported
        money_allowed = qualification.downstream_money_evaluation_allowed
        blockers = list(qualification.blockers)
        lanes = scored.get("objective_lanes") or {}
        if (lanes.get("MARKET") or {}).get("status") != "PASS":
            blockers.append("MARKET_DATA_UNAVAILABLE")
        if (lanes.get("MONEY") or {}).get("status") != "PASS":
            blockers.append("PAYOUT_UNRESOLVED")
    decision = reduce_prop_terminal(
        proposed_label=terminal_label,
        blockers=blockers,
        model_evaluated=True,
    )
    return {
        "row_key": row_key,
        "terminal_status": "REJECTED" if decision.pick_rejected else "COMPLETED",
        "code": decision.terminal_label,
        "terminal_label": decision.terminal_label,
        "confidence_tier": confidence_tier,
        "rank_eligible": rank_eligible,
        "model_supported": model_supported,
        "model_evaluated": True,
        "pick_rejected": decision.pick_rejected,
        "verdict_class": decision.verdict_class,
        "infrastructure_blocked": decision.infrastructure_blocked,
        "downstream_money_evaluation_allowed": money_allowed,
        "source_snapshot_id": snapshot_id,
        "evidence_fingerprint": fingerprint,
        "acquisition": acquisition,
        "result": scored,
        "probability_publishable": bool(scored.get("probability_publishable")),
        "can_execute": False,
    }


def _telemetry(outcomes: list[dict[str, Any]]) -> dict[str, int]:
    auto_attempted = 0
    auto_succeeded = 0
    route_blocked = 0
    acquisition_failures = 0
    model_completed = 0
    for outcome in outcomes:
        acquisition = outcome.get("acquisition") or {}
        if acquisition.get("mode") == "AUTO_HYDRATION":
            auto_attempted += 1
            if acquisition.get("status") == "PASS":
                auto_succeeded += 1
        if outcome.get("code") in {
            "SPECIALIST_ROUTING_UNAVAILABLE",
            "MODEL_UNAVAILABLE",
            "PROP_PROBABILITY_UNAVAILABLE",
        } and acquisition.get("mode") == "NOT_ATTEMPTED_ROUTE_BLOCKED":
            route_blocked += 1
        if outcome.get("code") in {
            "RUN_INVALID_ACQUISITION_INCOMPLETE",
            "PROP_AUTO_HYDRATION_UNSUPPORTED_ROUTE",
            "PROP_AUTO_HYDRATION_PROVIDER_UNAVAILABLE",
            "PROP_PLAYER_IDENTITY_UNRESOLVED",
            "PROP_EVENT_IDENTITY_CONFLICT",
            "MLB_RECENT_STARTS_INSUFFICIENT",
            "MLB_STARTER_STATUS_UNRESOLVED",
            "EVENT_ALREADY_STARTED",
            "PROP_EVIDENCE_PERSISTENCE_UNAVAILABLE",
        }:
            acquisition_failures += 1
        if outcome.get("model_evaluated") is True or outcome.get("terminal_status") == "COMPLETED":
            model_completed += 1
    return {
        "auto_hydration_attempted": auto_attempted,
        "auto_hydration_succeeded": auto_succeeded,
        "route_preflight_blocked": route_blocked,
        "acquisition_failures": acquisition_failures,
        "model_completed": model_completed,
        "false_global_failure_count": 0,
    }


def install_pick_request_routes(
    app: Any,
    *,
    market_api: Any,
    auth_dependency: Any,
) -> None:
    """Install one authenticated row-isolated screenshot/discovery scoring route."""
    if any(getattr(route, "path", None) == "/score-pick-request" for route in app.router.routes):
        return

    @app.post(
        "/score-pick-request",
        dependencies=[auth_dependency],
        operation_id="scoreWowPickRequest",
    )
    def score_pick_request(
        batch: PickRequestBatch,
        x_wow_model_identity: Optional[str] = Header(
            default=None,
            alias="X-WOW-Model-Identity",
        ),
    ):
        outcomes: list[dict[str, Any]] = []

        for index, original_row in enumerate(batch.rows):
            row = original_row
            row_key = row.row_key or f"row-{index + 1}"
            canonical_stat = _canonical_stat(row.sport, row.stat_type)
            sport = str(row.sport).strip().upper()
            route_blocked_acquisition = {
                "mode": "NOT_ATTEMPTED_ROUTE_BLOCKED",
                "status": "NOT_ATTEMPTED",
                "can_execute": False,
            }

            specialist = market_api.prod.base_api._controlling_specialist_provider(
                sport,
                canonical_stat,
            )
            if specialist is None:
                outcomes.append(
                    _terminal(
                        row_key,
                        "HELD",
                        "SPECIALIST_ROUTING_UNAVAILABLE",
                        detail={"specialist_invoked": False},
                        acquisition=route_blocked_acquisition,
                    )
                )
                continue
            if specialist.get("controlling_specialist") == "MODEL_UNAVAILABLE":
                outcomes.append(
                    _terminal(
                        row_key,
                        "HELD",
                        "MODEL_UNAVAILABLE",
                        detail={
                            "sport": sport,
                            "stat_type": canonical_stat,
                            "specialist_invoked": False,
                        },
                        acquisition=route_blocked_acquisition,
                    )
                )
                continue

            lane = market_api.prod._runtime_capability(market_api.prod.PROP_CAPABILITY_KEY)
            if lane.get("capability_status") != "AVAILABLE":
                outcomes.append(
                    _terminal(
                        row_key,
                        "HELD",
                        "PROP_PROBABILITY_UNAVAILABLE",
                        detail={
                            "governed_probability_capability": "UNAVAILABLE",
                            "capability_evidence": lane.get("evidence") or {},
                            "controlling_specialist": specialist.get("controlling_specialist"),
                            "specialist_invoked": False,
                        },
                        acquisition=route_blocked_acquisition,
                    )
                )
                continue

            route = market_api._prop_route_artifact(sport, canonical_stat)
            if route.get("ok") is not True or route.get("code") != "PROP_CERTIFIED_MODEL_ARTIFACT_READY":
                outcomes.append(
                    _terminal(
                        row_key,
                        "HELD",
                        "MODEL_UNAVAILABLE",
                        detail={
                            "blocker_code": route.get("code")
                            or "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
                            "sport": sport,
                            "stat_type": canonical_stat,
                            "specialist_invoked": False,
                        },
                        acquisition=route_blocked_acquisition,
                    )
                )
                continue

            acquisition: dict[str, Any]
            if row.evidence is None:
                try:
                    raw = auto_hydrate_prop_evidence(
                        sport=sport,
                        player=row.player,
                        stat_type=canonical_stat,
                        event_start_time=row.event_start_time,
                        source_capture_timestamp=row.source_capture_timestamp,
                        source_label=f"{row.source_type}:{row.platform or 'UNKNOWN'}",
                    )
                    row = row.model_copy(
                        update={"evidence": RawPropEvidence.model_validate(raw)}
                    )
                    acquisition = {
                        "mode": "AUTO_HYDRATION",
                        "status": "PASS",
                        "provider": "MLB_STATS_API_OFFICIAL_V1",
                        "source_type": row.source_type,
                        "platform": row.platform,
                        "can_execute": False,
                    }
                except PropAutoHydrationError as exc:
                    acquisition = {
                        "mode": "AUTO_HYDRATION",
                        "status": "FAILED",
                        "provider": "MLB_STATS_API_OFFICIAL_V1",
                        "source_type": row.source_type,
                        "platform": row.platform,
                        "can_execute": False,
                    }
                    outcomes.append(
                        _terminal(
                            row_key,
                            "HELD" if _auto_hydration_hold(exc.code) else "REJECTED",
                            exc.code,
                            detail={**exc.detail, "message": str(exc), "specialist_invoked": False},
                            acquisition=acquisition,
                        )
                    )
                    continue
                except Exception as exc:
                    acquisition = {
                        "mode": "AUTO_HYDRATION",
                        "status": "FAILED",
                        "provider": "MLB_STATS_API_OFFICIAL_V1",
                        "source_type": row.source_type,
                        "platform": row.platform,
                        "can_execute": False,
                    }
                    outcomes.append(
                        _terminal(
                            row_key,
                            "HELD",
                            "PROP_AUTO_HYDRATION_INTERNAL_ERROR",
                            detail={"error_type": type(exc).__name__, "specialist_invoked": False},
                            acquisition=acquisition,
                        )
                    )
                    continue
            else:
                acquisition = {
                    "mode": "CALLER_SUPPLIED_RAW_EVIDENCE",
                    "status": "PASS_PENDING_VALIDATION",
                    "source_type": row.source_type,
                    "platform": row.platform,
                    "can_execute": False,
                }

            try:
                normalized = _validate_evidence(row, canonical_stat)
                acquisition["status"] = "PASS"
            except ValueError as exc:
                acquisition["status"] = "FAILED_VALIDATION"
                outcomes.append(
                    _terminal(
                        row_key,
                        "REJECTED",
                        "RUN_INVALID_ACQUISITION_INCOMPLETE",
                        detail={"blocker": str(exc), "specialist_invoked": False},
                        acquisition=acquisition,
                    )
                )
                continue

            snapshot_id, fingerprint, snapshot = _snapshot_payload(row, normalized)
            try:
                market_api.prod.get_client().table("wow_prop_evidence_snapshots").upsert(
                    snapshot,
                    on_conflict="source_snapshot_id",
                ).execute()
            except Exception as exc:
                acquisition["status"] = "FAILED_PERSISTENCE"
                outcomes.append(
                    _terminal(
                        row_key,
                        "HELD",
                        "PROP_EVIDENCE_PERSISTENCE_UNAVAILABLE",
                        detail={
                            "error_type": type(exc).__name__,
                            "specialist_invoked": False,
                        },
                        snapshot_id=snapshot_id,
                        acquisition=acquisition,
                    )
                )
                continue

            acquisition["snapshot_status"] = "FROZEN"
            acquisition["source_snapshot_id"] = snapshot_id

            request_payload: dict[str, Any] = {
                "event_id": normalized["event_id"],
                "event_start_time": normalized["event_start_time"],
                "sport": sport,
                "player": normalized["player"],
                "stat_type": canonical_stat,
                "line": row.line,
                "direction": str(row.direction).strip().upper(),
                "source_snapshot_id": snapshot_id,
                "seed": row.seed,
                "money_lane_status": row.money_lane_status,
            }
            if row.market_side_a is not None:
                request_payload["market_side_a"] = row.market_side_a
            if row.market_side_b is not None:
                request_payload["market_side_b"] = row.market_side_b

            try:
                score_req = market_api.ScorePropRequest(**request_payload)
                scored = market_api.score_prop(
                    score_req,
                    x_wow_model_identity=x_wow_model_identity,
                )
            except HTTPException as exc:
                raw_detail = (
                    exc.detail
                    if isinstance(exc.detail, dict)
                    else {"message": str(exc.detail)}
                )
                code = str(raw_detail.get("code") or "ROW_SCORING_FAILED")
                held_status = (
                    exc.status_code >= 500
                    or exc.status_code == 409
                    or code == "MODEL_UNAVAILABLE"
                )
                outcomes.append(
                    _terminal(
                        row_key,
                        "HELD" if held_status else "REJECTED",
                        code,
                        detail=raw_detail,
                        snapshot_id=snapshot_id,
                        acquisition=acquisition,
                    )
                )
                continue
            except Exception as exc:
                outcomes.append(
                    _terminal(
                        row_key,
                        "HELD",
                        "ROW_SCORING_UNAVAILABLE",
                        detail={"error_type": type(exc).__name__},
                        snapshot_id=snapshot_id,
                        acquisition=acquisition,
                    )
                )
                continue

            outcomes.append(
                _completed_scored_outcome(
                    row_key=row_key,
                    scored=scored,
                    snapshot_id=snapshot_id,
                    fingerprint=fingerprint,
                    acquisition=acquisition,
                )
            )

        completed = sum(
            1 for outcome in outcomes if outcome["terminal_status"] == "COMPLETED"
        )
        held = sum(
            1 for outcome in outcomes if outcome["terminal_status"] == "HELD"
        )
        rejected = sum(
            1 for outcome in outcomes if outcome["terminal_status"] == "REJECTED"
        )
        pick_rejected_count = sum(1 for outcome in outcomes if outcome.get("pick_rejected") is True)
        infrastructure_blocked_count = sum(1 for outcome in outcomes if outcome.get("infrastructure_blocked") is True)
        rows_in = len(batch.rows)
        reconciliation_pass = rows_in == completed + held + rejected
        if not reconciliation_pass:
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "PICK_REQUEST_RECONCILIATION_FAILED",
                    "rows_in": rows_in,
                    "rows_completed": completed,
                    "rows_held": held,
                    "rows_rejected": rejected,
                    "probability_publishable": False,
                    "can_execute": False,
                },
            )

        if completed == rows_in:
            run_controller_status = "COMPLETE"
        elif completed > 0:
            run_controller_status = "DEGRADED"
        else:
            run_controller_status = "BLOCKED"

        return {
            "ok": completed > 0,
            "request_id": batch.request_id,
            "run_controller_status": run_controller_status,
            "rows_in": rows_in,
            "rows_completed": completed,
            "rows_held": held,
            "rows_rejected": rejected,
            "pick_rejected_count": pick_rejected_count,
            "infrastructure_blocked_count": infrastructure_blocked_count,
            "reconciliation_pass": reconciliation_pass,
            "telemetry": _telemetry(outcomes),
            "rows": outcomes,
            "probability_objective": "GOVERNED_MODEL_ONLY",
            "can_execute": False,
        }
