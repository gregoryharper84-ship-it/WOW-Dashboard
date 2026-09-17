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

from agent_runtime.idempotency import input_hash as _compute_input_hash
from agent_runtime.registry import worker_spec
from agent_runtime.runner_scout_research import execute_envelope
from agent_runtime.schemas import WorkerJobEnvelope
from agent_runtime.scout_research import RESEARCH_RECONCILER, RESEARCH_WORKERS, scout_lane
from mlb_1ip_specialist import CANONICAL_STAT_TYPE as MLB_1IP_STAT_TYPE
from mlb_1ip_specialist import score_mlb_1ip, starter_changed
from mlb_1ip_ingress_runtime import score_mlb_1ip_ingress
from prop_auto_hydration import PropAutoHydrationError, auto_hydrate_prop_evidence
from qualification_policy_v2 import classify_prop_probability
from prop_terminal_reducer_v2 import EVENT_BLOCKERS, TRUE_MODEL_REJECTION_LABELS, reduce_prop_terminal
from v17.portfolio_exposure_gate import evaluate_portfolio_qualification
from v17.slip_portfolio_optimizer import optimize_portfolio, thesis_identity


PROP_STAT_ALIASES: dict[tuple[str, str], str] = {
    ("MLB", "K"): "PITCHER_STRIKEOUTS",
    ("MLB", "KS"): "PITCHER_STRIKEOUTS",
    ("MLB", "SO"): "PITCHER_STRIKEOUTS",
    ("MLB", "STRIKEOUT"): "PITCHER_STRIKEOUTS",
    ("MLB", "STRIKEOUTS"): "PITCHER_STRIKEOUTS",
    ("MLB", "PITCHER_K"): "PITCHER_STRIKEOUTS",
    ("MLB", "PITCHER_KS"): "PITCHER_STRIKEOUTS",
    ("MLB", "1IP"): MLB_1IP_STAT_TYPE,
    ("MLB", "1ST_INNING_PITCHES"): MLB_1IP_STAT_TYPE,
    ("MLB", "1ST_INNING_PITCH_COUNT"): MLB_1IP_STAT_TYPE,
    ("MLB", "FIRST_INNING_PITCHES"): MLB_1IP_STAT_TYPE,
    ("MLB", "FIRST_INNING_PITCH_COUNT"): MLB_1IP_STAT_TYPE,
    ("MLB", "FIRST_INNING_PITCHES_THROWN"): MLB_1IP_STAT_TYPE,
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
    # MLB 1IP-only (WOW-PATCH-2026-09-01-MLB-1IP-FULL-MODEL-GOVERNED). Carries
    # starter/lineup state and event-tree inputs; see
    # mlb_1ip_specialist.score_mlb_1ip for the required shape. Left None for
    # every other stat type.
    lineup_evidence: Optional[dict[str, Any]] = None
    # Optional, additive opponent-lineup evidence (postmortem patch
    # WOW-PATCH-2026-09-02, issues #116/#119). Passed through unchanged to
    # the model adapter's feature contract (see prop_model_adapters.py's
    # mlb_pitcher_so_failure_path_nb_v1_adapter docstring). Caller-supplied
    # here, but never a probability, bound, or terminal label -- ordinary
    # evidence input like game_log/box_score_log, not a governed output.
    opponent_context: Optional[dict[str, Any]] = None


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
    # Only present when a caller actually supplies it: the persisted table
    # column (migrations/20260902_prop_evidence_opponent_context.sql) may not
    # yet be live everywhere this runs, and omitting an unused key keeps
    # every existing caller's fingerprint/upsert payload byte-identical to
    # before this field existed.
    if row.evidence.opponent_context is not None:
        fingerprint_input["opponent_context"] = row.evidence.opponent_context
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
    # Same opt-in guard as fingerprint_input above -- see comment there.
    if row.evidence.opponent_context is not None:
        persisted["opponent_context"] = row.evidence.opponent_context
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
        "terminal_cause": decision.terminal_cause,
        "concurrent_infrastructure_blockers": list(decision.concurrent_infrastructure_blockers),
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
        "terminal_cause": decision.terminal_cause,
        "concurrent_infrastructure_blockers": list(decision.concurrent_infrastructure_blockers),
        "downstream_money_evaluation_allowed": money_allowed,
        "source_snapshot_id": snapshot_id,
        "evidence_fingerprint": fingerprint,
        "acquisition": acquisition,
        "result": scored,
        "probability_publishable": bool(scored.get("probability_publishable")),
        "can_execute": False,
    }


def _portfolio_leg(
    row_key: str,
    row: "PickRequestRow",
    canonical_stat: str,
    scored: dict[str, Any],
) -> dict[str, Any]:
    """Build one leg record for the portfolio governance stages (dependency/
    correlation structure, session/directional exposure, duplicate thesis).

    Only identity fields and existing (already-computed) quality signals are
    read here -- this never computes or mutates a sporting probability, it
    only lets v17.slip_portfolio_optimizer and v17.portfolio_exposure_gate
    recognize when two completed rows in the same request share the same
    underlying thesis, the same event, or the same directional lean.
    """
    prediction = scored.get("prediction") if isinstance(scored.get("prediction"), dict) else scored
    return {
        "row_id": row_key,
        "event_id": getattr(row, "event_id", None),
        "player": getattr(row, "player", None),
        "prop_type": canonical_stat,
        "direction": getattr(row, "direction", None),
        "line": getattr(row, "line", None),
        "model_probability": prediction.get("raw_model_probability") if isinstance(prediction, dict) else None,
        "calibrated_probability": prediction.get("calibrated_probability") if isinstance(prediction, dict) else None,
        "calibrated_lower_bound": (
            prediction.get("calibrated_probability_lower_bound") if isinstance(prediction, dict) else None
        ),
    }


def _apply_portfolio_governance(
    request_id: Optional[str],
    scored_legs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> None:
    """Run the DEPENDENCY_CORRELATION_STRUCTURE and
    SESSION_DIRECTIONAL_DUPLICATE_THESIS_EXPOSURE gates (per the V17 shared
    gate order) across this batch and attach one unified, machine-enforced
    portfolio_governance decision to each completed row's outcome.

    All of this is genuinely computed -- v17.slip_portfolio_optimizer for
    duplicate-thesis, v17.portfolio_exposure_gate for same-event dependency
    and session/directional exposure -- never a caller-attested boolean, and
    never a fabricated joint probability. A row's own terminal_status,
    terminal_label, probability_publishable, calibrated_probability, and
    rank_eligible are read nowhere in this function and are never touched:
    portfolio/slip qualification is a separate objective lane from model
    probability (see qualification_policy_v2.downstream_money_evaluation_allowed
    for the same existing objective-separation pattern). What IS enforced is
    downstream_portfolio_evaluation_allowed -- a caller must not combine two
    rows into the same slip/card when either carries
    downstream_portfolio_evaluation_allowed=False.
    """
    if not scored_legs:
        return
    legs = [leg for leg, _ in scored_legs]
    card = {"card_id": request_id or "score-pick-request-batch", "legs": list(legs)}
    optimizer_result = optimize_portfolio([card])
    # A thesis appearing more than once in this batch is flagged regardless of
    # whether the optimizer could also remove/replace it -- removal requires
    # room to shrink below optimize_portfolio's minimum card size and/or a
    # stronger independent alternative, neither of which this row-scoring
    # endpoint supplies. Detection must not depend on those preconditions.
    duplicate_thesis_flagged = {
        str(leg["row_id"]): optimizer_result.duplicate_counts.get(thesis_identity(leg), 1) > 1 for leg in legs
    }

    qualification = evaluate_portfolio_qualification(legs, duplicate_thesis_flagged=duplicate_thesis_flagged)

    for leg, outcome in scored_legs:
        row_id = str(leg["row_id"])
        identity = thesis_identity(leg)
        decision = qualification[row_id]
        outcome["portfolio_governance"] = {
            "thesis_identity": identity,
            "duplicate_thesis_count": optimizer_result.duplicate_counts.get(identity, 1),
            "duplicate_thesis_flagged": decision["duplicate_thesis_flagged"],
            "same_event_dependent": decision["same_event_dependent"],
            "co_dependent_row_ids": decision["co_dependent_row_ids"],
            "session_event_leg_count": decision["session_event_leg_count"],
            "directional_exposure": decision["directional_exposure"],
            "portfolio_qualification": decision["portfolio_qualification"],
            "blockers": decision["blockers"],
            "sporting_probability_mutated": False,
            "can_execute": False,
        }
        outcome["downstream_portfolio_evaluation_allowed"] = decision["downstream_portfolio_evaluation_allowed"]


def _new_specialist_utilization_audit() -> dict[str, Any]:
    """Compact per-row proof of which V17 specialist/orchestration stages ran.

    This is observability only. It never creates model support, changes a
    probability, promotes a candidate artifact, or grants execution authority.
    """
    return {
        "assigned_specialist": None,
        "specialist_registered": False,
        "specialist_invoked": False,
        "research_agents_invoked": [],
        "research_barrier_status": "NOT_REACHED",
        "fitted_artifact_found": False,
        "model_execution_path": "NOT_REACHED",
        "model_family": None,
        "model_family_adapter_invoked": None,
        "calibrator_invoked": None,
        "raw_probability_produced": False,
        "publication_allowed": False,
        "rank_eligible": False,
        "exact_blocker": None,
        "can_execute": False,
    }


def _mark_research_utilization(audit: dict[str, Any], detail: dict[str, Any], *, passed: bool) -> None:
    stages = list(detail.get("stages") or [])
    audit["research_agents_invoked"] = [
        str(stage.get("worker_id"))
        for stage in stages
        if isinstance(stage, dict) and stage.get("worker_id")
    ]
    audit["research_barrier_status"] = "PASS" if passed else "BLOCKED"
    if not passed and audit.get("exact_blocker") is None:
        blockers = list(detail.get("blockers") or [])
        audit["exact_blocker"] = str(blockers[0] if blockers else "SCOUT_RESEARCH_BARRIER_BLOCKED")


def _mark_scored_utilization(audit: dict[str, Any], scored: dict[str, Any]) -> None:
    model_evidence = scored.get("model_evidence") if isinstance(scored.get("model_evidence"), dict) else {}
    prediction = scored.get("prediction") if isinstance(scored.get("prediction"), dict) else {}
    qualification = (
        scored.get("probability_qualification")
        if isinstance(scored.get("probability_qualification"), dict)
        else {}
    )
    model_family = model_evidence.get("model_family") or prediction.get("model_family")
    audit["model_family"] = model_family
    audit["model_family_adapter_invoked"] = bool(model_family)
    audit["raw_probability_produced"] = any(
        value is not None
        for value in (
            model_evidence.get("raw_model_probability"),
            model_evidence.get("raw_probability"),
            model_evidence.get("probability_more"),
            model_evidence.get("probability_less"),
            prediction.get("raw_model_probability"),
            prediction.get("raw_probability"),
        )
    )
    audit["calibrator_invoked"] = any(
        value is not None
        for value in (
            model_evidence.get("calibration_status"),
            model_evidence.get("calibration_method"),
            model_evidence.get("calibrated_probability"),
            model_evidence.get("calibrated_probability_lower_bound"),
            prediction.get("calibration_status"),
            prediction.get("calibrated_probability"),
            prediction.get("calibrated_probability_lower_bound"),
        )
    )
    audit["publication_allowed"] = bool(scored.get("probability_publishable"))
    audit["rank_eligible"] = bool(qualification.get("rank_eligible"))


def _mark_1ip_utilization(audit: dict[str, Any], outcome: dict[str, Any]) -> None:
    result = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
    detail = outcome.get("detail") if isinstance(outcome.get("detail"), dict) else {}
    barrier = result.get("scout_research_barrier") or detail.get("scout_research_barrier")
    if isinstance(barrier, dict):
        _mark_research_utilization(
            audit,
            barrier,
            passed=outcome.get("code") != "SCOUT_RESEARCH_BARRIER_BLOCKED",
        )
    audit["model_execution_path"] = "DIRECT_SPECIALIST"
    audit["model_family"] = result.get("model_family")
    audit["model_family_adapter_invoked"] = False
    audit["specialist_invoked"] = bool(
        outcome.get("model_evaluated") is True
        or detail.get("specialist_invoked") is True
    )
    audit["raw_probability_produced"] = any(
        result.get(key) is not None
        for key in ("raw_probability", "selected_probability")
    )
    audit["calibrator_invoked"] = any(
        result.get(key) is not None
        for key in (
            "calibration_method",
            "calibrated_probability",
            "calibrated_probability_lower_bound",
        )
    )
    audit["publication_allowed"] = bool(outcome.get("probability_publishable"))
    audit["rank_eligible"] = bool(outcome.get("rank_eligible"))
    if outcome.get("terminal_status") == "HELD" and audit.get("exact_blocker") is None:
        audit["exact_blocker"] = str(outcome.get("code") or "UNKNOWN_BLOCKER")


def _specialist_utilization_summary(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    audits = [
        outcome.get("specialist_utilization_audit")
        for outcome in outcomes
        if isinstance(outcome.get("specialist_utilization_audit"), dict)
    ]
    return {
        "rows_audited": len(audits),
        "rows_with_specialist_assigned": sum(bool(a.get("specialist_registered")) for a in audits),
        "rows_research_barrier_invoked": sum(a.get("research_barrier_status") != "NOT_REACHED" for a in audits),
        "rows_research_barrier_passed": sum(a.get("research_barrier_status") == "PASS" for a in audits),
        "rows_specialist_invoked": sum(bool(a.get("specialist_invoked")) for a in audits),
        "rows_fitted_artifact_found": sum(bool(a.get("fitted_artifact_found")) for a in audits),
        "rows_model_family_adapter_confirmed": sum(a.get("model_family_adapter_invoked") is True for a in audits),
        "rows_calibrator_confirmed": sum(a.get("calibrator_invoked") is True for a in audits),
        "rows_raw_probability_produced": sum(bool(a.get("raw_probability_produced")) for a in audits),
        "rows_publication_allowed": sum(bool(a.get("publication_allowed")) for a in audits),
        "rows_rank_eligible": sum(bool(a.get("rank_eligible")) for a in audits),
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


def _scout_research_envelope(run_id: str, candidate_id: str, worker_id: str, payload: dict[str, Any]) -> WorkerJobEnvelope:
    spec = worker_spec(worker_id)
    return WorkerJobEnvelope(
        run_id=run_id,
        job_id=f"{run_id}:{worker_id}",
        candidate_id=candidate_id,
        worker_id=worker_id,
        worker_version=spec.worker_version,
        as_of=datetime.now(timezone.utc).isoformat(),
        input_hash=_compute_input_hash(payload),
        payload=payload,
    )


def _run_mandatory_scout_research(
    *, row_key: str, run_id: str, candidate: dict[str, Any]
) -> tuple[bool, dict[str, Any]]:
    """Mandatory Scout -> Research evidence barrier ahead of the 1IP
    specialist, driven synchronously in-process against the exact worker
    handlers the durable Agent Runtime coordinator dispatches through Celery
    for full-slate/prop runs (agent_runtime.runner_scout_research). Reused
    from the same primitive as the v17 team-event convergence work -- this
    is not a second Scout/Research implementation.

    Returns (ok, detail). detail["stages"] always lists what ran; when
    ok is False, detail also carries "stage" and "blockers" for the caller
    to report as a fail-closed outcome.
    """
    stages: list[dict[str, Any]] = []

    def _run(worker_id: str, payload: dict[str, Any]):
        env = _scout_research_envelope(run_id, row_key, worker_id, payload)
        out = execute_envelope(env)
        stages.append({"worker_id": worker_id, "status": out.status, "blockers": list(out.blockers)})
        return out

    scout_out = _run("wow.global-scout-coordinator", {"candidate": candidate, "scout_mode": "FOCUSED"})
    if scout_out.status != "SUCCEEDED":
        return False, {"stage": "wow.global-scout-coordinator", "blockers": scout_out.blockers, "stages": stages}

    lane = scout_lane(candidate)
    lane_worker = "wow.prop-scout-router" if lane == "PROP" else "wow.ml-event-scout-router"
    lane_out = _run(lane_worker, {"candidate": candidate})
    if lane_out.status != "SUCCEEDED":
        return False, {"stage": lane_worker, "blockers": lane_out.blockers, "stages": stages}

    reports: list[dict[str, Any]] = []
    team_jobs_ok = True
    for worker_id in RESEARCH_WORKERS:
        out = _run(worker_id, {"candidate": candidate, "evidence": candidate.get("evidence")})
        team_jobs_ok = team_jobs_ok and out.status == "SUCCEEDED"
        reports.append(
            out.output if out.status == "SUCCEEDED" else {"research_status": "DATA_UNOBTAINABLE", "worker_id": worker_id}
        )

    reconciler_out = _run(
        RESEARCH_RECONCILER,
        {
            "research_reports": reports,
            "team_jobs_ok": team_jobs_ok,
            "evidence_present": isinstance(candidate.get("evidence"), dict),
            "event_start_present": bool(candidate.get("event_start_utc")),
        },
    )
    if reconciler_out.status != "SUCCEEDED":
        return False, {"stage": RESEARCH_RECONCILER, "blockers": reconciler_out.blockers, "stages": stages}

    return True, {"stages": stages}


def _score_mlb_1ip_row(
    row: "PickRequestRow",
    row_key: str,
    *,
    market_api: Any,
    request_id: Optional[str],
) -> dict[str, Any]:
    """Canonical MLB 1IP path after specialist/capability/artifact preflight.

    Acquisition, mandatory Scout -> Research, controlling-specialist scoring,
    and provisional final-refresh queuing are delegated to the dedicated 1IP
    ingress helper. The preflight remains in score_pick_request so a genuinely
    missing certified artifact still terminates as MODEL_UNAVAILABLE before
    expensive acquisition begins.
    """
    return score_mlb_1ip_ingress(
        row=row,
        row_key=row_key,
        market_api=market_api,
        request_id=request_id,
        run_research=_run_mandatory_scout_research,
        terminal=_terminal,
        reduce_terminal=reduce_prop_terminal,
    )


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
        scored_legs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        utilization_by_row: dict[str, dict[str, Any]] = {}

        for index, original_row in enumerate(batch.rows):
            row = original_row
            row_key = row.row_key or f"row-{index + 1}"
            utilization = _new_specialist_utilization_audit()
            utilization_by_row[row_key] = utilization
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
            if isinstance(specialist, dict):
                utilization["assigned_specialist"] = specialist.get("controlling_specialist")
                utilization["specialist_registered"] = bool(
                    specialist.get("controlling_specialist")
                    and specialist.get("controlling_specialist") != "MODEL_UNAVAILABLE"
                )
            if specialist is None:
                utilization["exact_blocker"] = "SPECIALIST_ROUTING_UNAVAILABLE"
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
                utilization["exact_blocker"] = "MODEL_UNAVAILABLE"
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
                utilization["exact_blocker"] = "PROP_PROBABILITY_UNAVAILABLE"
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
                utilization["exact_blocker"] = str(
                    route.get("code") or "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND"
                )
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

            utilization["fitted_artifact_found"] = True

            if canonical_stat == MLB_1IP_STAT_TYPE:
                mlb_1ip_outcome = _score_mlb_1ip_row(row, row_key, market_api=market_api, request_id=batch.request_id)
                _mark_1ip_utilization(utilization, mlb_1ip_outcome)
                outcomes.append(mlb_1ip_outcome)
                if mlb_1ip_outcome["terminal_status"] == "COMPLETED":
                    scored_legs.append(
                        (_portfolio_leg(row_key, row, canonical_stat, mlb_1ip_outcome.get("result") or {}), mlb_1ip_outcome)
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

            research_candidate = {
                "sport": sport,
                "league": row.league,
                "official_event_id": row.event_id,
                "participant": row.player,
                "opponent": row.opponent,
                "market_family": "PLAYER_PROP",
                "stat_family": canonical_stat,
                "period": _prop_period(canonical_stat) if hasattr(market_api, "_prop_period") else "FULL_GAME",
                "exact_line": float(row.line),
                "side": str(row.direction).strip().upper(),
                "event_start_utc": row.event_start_time,
                "evidence": normalized,
            }
            research_ok, research_detail = _run_mandatory_scout_research(
                row_key=row_key,
                run_id=f"pick-request-prop-{batch.request_id or 'adhoc'}-{row_key}",
                candidate=research_candidate,
            )
            _mark_research_utilization(utilization, research_detail, passed=research_ok)
            if not research_ok:
                outcomes.append(
                    _terminal(
                        row_key,
                        "HELD",
                        "SCOUT_RESEARCH_BARRIER_BLOCKED",
                        detail={
                            "terminal_label": "RESEARCH_INTEREST",
                            "stage": research_detail.get("stage"),
                            "blocker": (
                                (research_detail.get("blockers") or ["SCOUT_RESEARCH_BARRIER_BLOCKED"])[0]
                            ),
                            "scout_research_barrier": research_detail,
                            "specialist_invoked": False,
                        },
                        snapshot_id=snapshot_id,
                        acquisition=acquisition,
                    )
                )
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
            if row.platform is not None:
                # Platform identifies which reviewed server rule to hydrate;
                # it never supplies settlement semantics itself.
                request_payload["settlement_provider"] = row.platform
            if row.market_side_a is not None:
                request_payload["market_side_a"] = row.market_side_a
            if row.market_side_b is not None:
                request_payload["market_side_b"] = row.market_side_b

            try:
                score_req = market_api.ScorePropRequest(**request_payload)
                utilization["specialist_invoked"] = True
                utilization["model_execution_path"] = "CERTIFIED_MODEL_FAMILY_ADAPTER"
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
                utilization["exact_blocker"] = str(
                    raw_detail.get("blocker_code") or raw_detail.get("blocker") or code
                )
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
                utilization["exact_blocker"] = "ROW_SCORING_UNAVAILABLE"
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

            _mark_scored_utilization(utilization, scored)
            completed_outcome = _completed_scored_outcome(
                row_key=row_key,
                scored=scored,
                snapshot_id=snapshot_id,
                fingerprint=fingerprint,
                acquisition=acquisition,
            )
            outcomes.append(completed_outcome)
            if completed_outcome["terminal_status"] == "COMPLETED":
                scored_legs.append((_portfolio_leg(row_key, row, canonical_stat, scored), completed_outcome))

        _apply_portfolio_governance(batch.request_id, scored_legs)

        for outcome in outcomes:
            audit = utilization_by_row.get(str(outcome.get("row_key")))
            if audit is not None:
                outcome["specialist_utilization_audit"] = audit

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
            "specialist_utilization_summary": _specialist_utilization_summary(outcomes),
            "rows": outcomes,
            "probability_objective": "GOVERNED_MODEL_ONLY",
            "can_execute": False,
        }
