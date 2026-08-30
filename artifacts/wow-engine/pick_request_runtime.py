"""Governed screenshot/self-discovery -> evidence snapshot -> prop scoring bridge.

This boundary is intentionally acquisition-only. The caller may supply raw,
auditable pregame evidence gathered from screenshots and approved live sources,
but may never supply a probability, model artifact, calibration output, edge,
or approval label. The backend validates and deterministically fingerprints the
evidence, writes the governed wow_prop_evidence_snapshots row, then delegates to
the existing certified /score-prop model path.

Every row terminates exactly once. A bad/unsupported row cannot erase a sibling
row. can_execute is false unconditionally.
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Optional

from fastapi import Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field


PROP_STAT_ALIASES: dict[tuple[str, str], str] = {
    ("MLB", "K"): "PITCHER_STRIKEOUTS",
    ("MLB", "KS"): "PITCHER_STRIKEOUTS",
    ("MLB", "SO"): "PITCHER_STRIKEOUTS",
    ("MLB", "STRIKEOUT"): "PITCHER_STRIKEOUTS",
    ("MLB", "STRIKEOUTS"): "PITCHER_STRIKEOUTS",
    ("MLB", "PITCHER_K"): "PITCHER_STRIKEOUTS",
    ("MLB", "PITCHER_KS"): "PITCHER_STRIKEOUTS",
}


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
    evidence: RawPropEvidence
    seed: int = 0
    money_lane_status: str = "PAYOUT_UNRESOLVED"
    market_side_a: Optional[dict[str, Any]] = None
    market_side_b: Optional[dict[str, Any]] = None


class PickRequestBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(float(v)) for v in row.evidence.game_log):
        raise ValueError("GAME_LOG_NON_NUMERIC")
    if any(not isinstance(v, dict) or not v for v in row.evidence.box_score_log):
        raise ValueError("BOX_SCORE_LOG_INVALID")

    role_status = row.evidence.role_status
    role_label = str(role_status.get("status") or role_status.get("role") or "").strip()
    if not role_label:
        raise ValueError("ROLE_STATUS_MISSING")

    opportunity = row.evidence.opportunity_ledger
    opportunity_status = str(opportunity.get("status") or opportunity.get("gate_label") or "").strip().upper()
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


def _snapshot_payload(row: PickRequestRow, normalized: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    # Fingerprint includes acquisition provenance even though the legacy snapshot
    # table stores provenance source names/times rather than a separate hash column.
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
    canonical = json.dumps(fingerprint_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    fingerprint = sha256(canonical.encode("utf-8")).hexdigest()
    snapshot_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"wow-prop-evidence:{fingerprint}"))

    # Match the live wow_prop_evidence_snapshots table exactly. The deterministic
    # source_snapshot_id makes identical acquisitions idempotent without allowing
    # caller-controlled snapshot identity.
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


def _terminal(row_key: str, status: str, code: str, *, detail: Optional[dict[str, Any]] = None, snapshot_id: Optional[str] = None) -> dict[str, Any]:
    return {
        "row_key": row_key,
        "terminal_status": status,
        "code": code,
        "source_snapshot_id": snapshot_id,
        "detail": detail or {},
        "probability_publishable": False,
        "can_execute": False,
    }


def install_pick_request_routes(app: Any, *, market_api: Any, auth_dependency: Any) -> None:
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
        x_wow_model_identity: Optional[str] = Header(default=None, alias="X-WOW-Model-Identity"),
    ):
        outcomes: list[dict[str, Any]] = []

        for index, row in enumerate(batch.rows):
            row_key = row.row_key or f"row-{index + 1}"
            canonical_stat = _canonical_stat(row.sport, row.stat_type)
            sport = str(row.sport).strip().upper()

            specialist = market_api.prod.base_api._controlling_specialist_provider(sport, canonical_stat)
            if specialist is None:
                outcomes.append(_terminal(row_key, "HELD", "SPECIALIST_ROUTING_UNAVAILABLE"))
                continue
            if specialist.get("controlling_specialist") == "MODEL_UNAVAILABLE":
                outcomes.append(_terminal(
                    row_key,
                    "HELD",
                    "MODEL_UNAVAILABLE",
                    detail={"sport": sport, "stat_type": canonical_stat, "specialist_invoked": False},
                ))
                continue

            route = market_api._prop_route_artifact(sport, canonical_stat)
            if route.get("ok") is not True or route.get("code") != "PROP_CERTIFIED_MODEL_ARTIFACT_READY":
                outcomes.append(_terminal(
                    row_key,
                    "HELD",
                    "MODEL_UNAVAILABLE",
                    detail={
                        "blocker_code": route.get("code") or "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
                        "sport": sport,
                        "stat_type": canonical_stat,
                        "specialist_invoked": False,
                    },
                ))
                continue

            try:
                normalized = _validate_evidence(row, canonical_stat)
            except ValueError as exc:
                outcomes.append(_terminal(
                    row_key,
                    "REJECTED",
                    "RUN_INVALID_ACQUISITION_INCOMPLETE",
                    detail={"blocker": str(exc), "specialist_invoked": False},
                ))
                continue

            snapshot_id, fingerprint, snapshot = _snapshot_payload(row, normalized)
            try:
                market_api.prod.get_client().table("wow_prop_evidence_snapshots").upsert(
                    snapshot,
                    on_conflict="source_snapshot_id",
                ).execute()
            except Exception as exc:
                outcomes.append(_terminal(
                    row_key,
                    "HELD",
                    "PROP_EVIDENCE_PERSISTENCE_UNAVAILABLE",
                    detail={"error_type": type(exc).__name__, "specialist_invoked": False},
                    snapshot_id=snapshot_id,
                ))
                continue

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
                scored = market_api.score_prop(score_req, x_wow_model_identity=x_wow_model_identity)
            except HTTPException as exc:
                raw_detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                code = str(raw_detail.get("code") or "ROW_SCORING_FAILED")
                held = exc.status_code >= 500 or exc.status_code == 409 or code == "MODEL_UNAVAILABLE"
                outcomes.append(_terminal(
                    row_key,
                    "HELD" if held else "REJECTED",
                    code,
                    detail=raw_detail,
                    snapshot_id=snapshot_id,
                ))
                continue
            except Exception as exc:
                outcomes.append(_terminal(
                    row_key,
                    "HELD",
                    "ROW_SCORING_UNAVAILABLE",
                    detail={"error_type": type(exc).__name__},
                    snapshot_id=snapshot_id,
                ))
                continue

            outcomes.append({
                "row_key": row_key,
                "terminal_status": "COMPLETED",
                "code": "MODEL_QUALIFIED" if scored.get("probability_publishable") is True else "MODEL_QUALIFIED_HOLD",
                "source_snapshot_id": snapshot_id,
                "evidence_fingerprint": fingerprint,
                "result": scored,
                "probability_publishable": bool(scored.get("probability_publishable")),
                "can_execute": False,
            })

        completed = sum(1 for row in outcomes if row["terminal_status"] == "COMPLETED")
        held = sum(1 for row in outcomes if row["terminal_status"] == "HELD")
        rejected = sum(1 for row in outcomes if row["terminal_status"] == "REJECTED")
        rows_in = len(batch.rows)
        assert rows_in == completed + held + rejected

        return {
            "ok": True,
            "rows_in": rows_in,
            "rows_completed": completed,
            "rows_held": held,
            "rows_rejected": rejected,
            "reconciliation_pass": rows_in == completed + held + rejected,
            "rows": outcomes,
            "probability_objective": "GOVERNED_MODEL_ONLY",
            "can_execute": False,
        }
