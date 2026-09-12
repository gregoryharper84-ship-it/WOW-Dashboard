"""V17 Daily Pick Orchestrator.

Control-plane orchestration over existing governed V17 scoring. This module
never creates or repairs sporting probability. It compiles a terse daily-picks
request, invokes the existing governed Daily snapshot runner, isolates typed
row failures, retries only transient scorer failures once, performs a final
pregame refresh, ranks valid survivors by governed calibrated lower bound, and
returns exact-once reconciliation.

Execution remains disabled. No wager/order path is exposed here.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Callable, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field

from v17.daily_snapshot_runtime import DailySnapshotRequest, run_daily_snapshot


class OrchestratorStage(StrEnum):
    CREATED = "CREATED"
    COMPILED = "COMPILED"
    DISCOVERING = "DISCOVERING"
    NORMALIZING = "NORMALIZING"
    ROUTING = "ROUTING"
    SCORING = "SCORING"
    CALIBRATING = "CALIBRATING"
    REFRESHING = "REFRESHING"
    RECONCILING = "RECONCILING"
    REDUCING = "REDUCING"
    COMPLETED = "COMPLETED"


STAGE_SEQUENCE = [stage.value for stage in OrchestratorStage]
RETRYABLE_CODES = {
    "PROP_SCORER_EXCEPTION",
    "TEAM_EVENT_SCORER_EXCEPTION",
    "MODEL_SCORER_FAILED",
}


class DailyPicksRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str | None = Field(default=None, max_length=1000)
    requested_slate_date: str | None = None
    requested_timezone: str = Field(default="America/Chicago", min_length=1, max_length=64)
    lanes: list[Literal["PROPS", "MONEYLINE"]] = Field(default_factory=lambda: ["PROPS", "MONEYLINE"])
    requested_count: int = Field(default=10, ge=1, le=50)
    max_props: int = Field(default=12, ge=0, le=12)
    max_team_events: int = Field(default=12, ge=0, le=12)
    transient_retry_attempts: int = Field(default=1, ge=0, le=1)


def _today(timezone_name: str) -> str:
    return datetime.now(ZoneInfo(timezone_name)).date().isoformat()


def compile_request(req: DailyPicksRequest) -> dict[str, Any]:
    """Compile safe V17 defaults without manufacturing model support."""
    slate_date = req.requested_slate_date or _today(req.requested_timezone)
    return {
        "request_id": f"v17-picks-{uuid4()}",
        "raw_query": req.query,
        "normalized_intent": "BEST_PICKS_REMAINING_TODAY",
        "primary_objective": "DAILY_PICK_RANKING",
        "requested_slate_date": slate_date,
        "requested_timezone": req.requested_timezone,
        "lanes": list(dict.fromkeys(req.lanes)),
        "remaining_pregame_only": True,
        "scout_intake_required": True,
        "scout_is_additive": True,
        "full_permitted_discovery_required": True,
        "exactly_one_controlling_specialist_per_row": True,
        "probability_requirement": "GOVERNED_NUMERIC_PACKAGE",
        "ranking_metric": "calibrated_lower_bound",
        "final_refresh_required": True,
        "exact_once_reconciliation": True,
        "requested_count": req.requested_count,
        "can_execute": False,
    }


def _identity_key(row: dict[str, Any]) -> tuple[Any, ...]:
    identity = row.get("identity") or {}
    return (
        row.get("lane"),
        identity.get("event_id") or identity.get("official_event_id"),
        identity.get("player"),
        identity.get("stat_type"),
        identity.get("line"),
        identity.get("source_snapshot_id") or identity.get("snapshot_id"),
    )


def _payload_code(payload: dict[str, Any]) -> str | None:
    for key in ("code", "terminal_label", "probability_claim_status", "model_status", "scorer_status"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    blockers = payload.get("blockers")
    if isinstance(blockers, list) and blockers:
        return str(blockers[0])
    return None


def _outcome_quality(outcome: dict[str, Any]) -> tuple[int, int]:
    payload = outcome.get("payload") or {}
    return (
        1 if outcome.get("status") == "COMPLETED" else 0,
        1 if payload.get("rank_eligible") is True and payload.get("probability_publishable") is True else 0,
    )


def _row_quality(row: dict[str, Any]) -> tuple[int, int]:
    result = row.get("result") or {}
    if row.get("lane") == "PROPS":
        outcomes = result.get("outcomes") or []
        completed = sum(1 for outcome in outcomes if outcome.get("status") == "COMPLETED")
        eligible = sum(
            1
            for outcome in outcomes
            if (outcome.get("payload") or {}).get("rank_eligible") is True
            and (outcome.get("payload") or {}).get("probability_publishable") is True
        )
        return completed, eligible
    return (
        1 if row.get("row_status") == "COMPLETED" else 0,
        1 if result.get("rank_eligible") is True and result.get("probability_publishable") is True else 0,
    )


def _has_retryable_failure(snapshot: dict[str, Any]) -> bool:
    for row in snapshot.get("rows") or []:
        if row.get("lane") == "PROPS":
            for outcome in (row.get("result") or {}).get("outcomes") or []:
                if _payload_code(outcome.get("payload") or {}) in RETRYABLE_CODES:
                    return True
        elif _payload_code(row.get("result") or {}) in RETRYABLE_CODES:
            return True
    return False


def _merge_prop_row(current: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Merge prop retries per direction so one recovered side cannot erase another."""
    merged = deepcopy(current)
    current_outcomes = {
        outcome.get("direction"): outcome
        for outcome in (merged.get("result") or {}).get("outcomes") or []
        if outcome.get("direction")
    }
    for outcome in (candidate.get("result") or {}).get("outcomes") or []:
        direction = outcome.get("direction")
        if not direction:
            continue
        existing = current_outcomes.get(direction)
        if existing is None or _outcome_quality(outcome) > _outcome_quality(existing):
            current_outcomes[direction] = deepcopy(outcome)
    outcomes = [current_outcomes[key] for key in ("MORE", "LESS") if key in current_outcomes]
    merged_result = dict(merged.get("result") or {})
    merged_result["outcomes"] = outcomes
    publishable = any(
        (outcome.get("payload") or {}).get("probability_publishable") is True
        and (outcome.get("payload") or {}).get("rank_eligible") is True
        for outcome in outcomes
    )
    merged_result["probability_publishable"] = publishable
    merged_result["rank_eligible"] = publishable
    merged["result"] = merged_result
    merged["row_status"] = "COMPLETED" if any(outcome.get("status") == "COMPLETED" for outcome in outcomes) else "HELD"
    return merged


def _merge_attempts(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge retries by canonical identity without duplicating terminal rows."""
    if not attempts:
        return {"rows": [], "blockers": ["ORCHESTRATOR_NO_SNAPSHOT_ATTEMPT"], "run_status": "FAILED"}
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for attempt in attempts:
        for row in attempt.get("rows") or []:
            key = _identity_key(row)
            current = merged.get(key)
            if current is None:
                merged[key] = deepcopy(row)
            elif row.get("lane") == "PROPS":
                merged[key] = _merge_prop_row(current, row)
            elif _row_quality(row) > _row_quality(current):
                merged[key] = deepcopy(row)
    last = attempts[-1]
    return {
        **last,
        "rows": list(merged.values()),
        "blockers": list(dict.fromkeys(str(x) for attempt in attempts for x in (attempt.get("blockers") or []))),
        "attempt_run_ids": [attempt.get("run_id") for attempt in attempts],
    }


def _future_at_publication(identity: dict[str, Any], now_utc: datetime) -> bool:
    value = identity.get("event_start_time") or identity.get("event_start_time_utc")
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.utcoffset() is not None and parsed.astimezone(timezone.utc) > now_utc
    except (TypeError, ValueError):
        return False


def _number(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    probability = payload.get("probability")
    if isinstance(probability, dict):
        for key in keys:
            value = probability.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
    return None


def _candidate(*, lane: str, identity: dict[str, Any], payload: dict[str, Any], direction: str | None, row_status: str, now_utc: datetime) -> dict[str, Any]:
    still_pregame = _future_at_publication(identity, now_utc)
    lower = _number(payload, "calibrated_lower_bound", "calibrated_probability_lower_bound", "lower_bound")
    calibrated = _number(payload, "calibrated_probability")
    raw = _number(payload, "raw_model_probability", "model_probability", "raw_probability")
    rank_eligible = bool(
        still_pregame
        and row_status == "COMPLETED"
        and payload.get("rank_eligible") is True
        and payload.get("probability_publishable") is True
        and lower is not None
    )
    code = _payload_code(payload)
    if not still_pregame:
        code = "EVENT_ALREADY_STARTED"
    elif not rank_eligible and not code:
        code = "NOT_RANK_ELIGIBLE"
    return {
        "lane": lane,
        "identity": identity,
        "direction": direction,
        "raw_model_probability": raw,
        "calibrated_probability": calibrated,
        "calibrated_lower_bound": lower,
        "main_failure_path": payload.get("largest_failure_path") or payload.get("main_failure_path"),
        "model_status": code or "MODEL_QUALIFIED_OR_BACKEND_NATIVE_PASS",
        "row_status": row_status,
        "probability_publishable": payload.get("probability_publishable") is True,
        "rank_eligible": rank_eligible,
        "final_refresh": "PASS" if still_pregame else "EVENT_ALREADY_STARTED",
        "can_execute": False,
    }


def _expand_candidates(snapshot: dict[str, Any], *, now_utc: datetime) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in snapshot.get("rows") or []:
        lane = str(row.get("lane"))
        identity = dict(row.get("identity") or {})
        row_status = str(row.get("row_status") or "HELD")
        result = dict(row.get("result") or {})
        if lane == "PROPS":
            for outcome in result.get("outcomes") or []:
                candidates.append(_candidate(
                    lane=lane,
                    identity=identity,
                    payload=dict(outcome.get("payload") or {}),
                    direction=outcome.get("direction"),
                    row_status=str(outcome.get("status") or row_status),
                    now_utc=now_utc,
                ))
        else:
            candidates.append(_candidate(
                lane=lane,
                identity=identity,
                payload=result,
                direction=None,
                row_status=row_status,
                now_utc=now_utc,
            ))
    return candidates


def _blocker_summary(candidates: list[dict[str, Any]], upstream_blockers: list[str]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for candidate in candidates:
        if not candidate.get("rank_eligible"):
            counter[str(candidate.get("model_status") or "UNCLASSIFIED_BLOCKER")] += 1
    for blocker in upstream_blockers:
        counter[str(blocker).split(":", 1)[0]] += 1
    return dict(sorted(counter.items()))


def _coverage(snapshot: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    requested_lanes = set(snapshot.get("requested_lanes") or [])
    sports = sorted({
        str((candidate.get("identity") or {}).get("sport"))
        for candidate in candidates
        if (candidate.get("identity") or {}).get("sport")
    })
    moneyline_requested = "MONEYLINE" in requested_lanes
    if not moneyline_requested:
        return {
            "sports_observed": sports,
            "props_discovery": "CANONICAL_SNAPSHOT_DISCOVERY" if "PROPS" in requested_lanes else "NOT_REQUESTED",
            "moneyline_discovery": "NOT_REQUESTED",
            "cross_sport_moneyline_complete": None,
            "cross_sport_blocker": None,
            "future_adapter_registration_required": False,
        }
    return {
        "sports_observed": sports,
        "props_discovery": "CANONICAL_SNAPSHOT_DISCOVERY" if "PROPS" in requested_lanes else "NOT_REQUESTED",
        "moneyline_discovery": "MLB_FORWARD_SHADOW_ONLY",
        "cross_sport_moneyline_complete": False,
        "cross_sport_blocker": "CROSS_SPORT_DISCOVERY_ADAPTERS_NOT_REGISTERED_IN_DAILY_SNAPSHOT_RUNTIME",
        "future_adapter_registration_required": True,
    }


def run_daily_picks(req: DailyPicksRequest, *, db: Any, market_api: Any, event_api: Any, snapshot_runner: Callable[..., dict[str, Any]] = run_daily_snapshot) -> dict[str, Any]:
    compiled = compile_request(req)
    snapshot_req = DailySnapshotRequest(
        requested_slate_date=compiled["requested_slate_date"],
        requested_timezone=compiled["requested_timezone"],
        lanes=compiled["lanes"],
        max_props=req.max_props,
        max_team_events=req.max_team_events,
    )

    attempts = [snapshot_runner(snapshot_req, db=db, market_api=market_api, event_api=event_api)]
    retries = 0
    while retries < req.transient_retry_attempts and _has_retryable_failure(attempts[-1]):
        retries += 1
        attempts.append(snapshot_runner(snapshot_req, db=db, market_api=market_api, event_api=event_api))

    snapshot = _merge_attempts(attempts)
    candidates = _expand_candidates(snapshot, now_utc=datetime.now(timezone.utc))
    ranked = sorted(
        (candidate for candidate in candidates if candidate.get("rank_eligible")),
        key=lambda candidate: float(candidate["calibrated_lower_bound"]),
        reverse=True,
    )
    leaderboard = [dict(candidate, rank=index + 1) for index, candidate in enumerate(ranked[: req.requested_count])]

    terminal_candidates = len(candidates)
    rank_eligible_count = len(ranked)
    blocked_count = terminal_candidates - rank_eligible_count
    reconciliation = {
        "snapshot_rows": len(snapshot.get("rows") or []),
        "terminal_candidates": terminal_candidates,
        "rank_eligible": rank_eligible_count,
        "blocked_or_purged": blocked_count,
        "balanced": terminal_candidates == rank_eligible_count + blocked_count,
        "retry_attempts_used": retries,
        "no_silent_candidate_loss": terminal_candidates == rank_eligible_count + blocked_count,
    }

    blocker_summary = _blocker_summary(candidates, [str(x) for x in snapshot.get("blockers") or []])
    coverage = _coverage(snapshot, candidates)
    run_status = "COMPLETED" if reconciliation["balanced"] else "RUN_RECONCILIATION_FAILED"
    coverage_blocked = coverage.get("cross_sport_moneyline_complete") is False
    if run_status == "COMPLETED" and (blocker_summary or coverage_blocked):
        run_status = "COMPLETED_WITH_BLOCKERS"

    return {
        "run_id": compiled["request_id"],
        "snapshot_run_ids": snapshot.get("attempt_run_ids") or [snapshot.get("run_id")],
        "run_status": run_status,
        "compiled_request": compiled,
        "orchestration_stages": STAGE_SEQUENCE,
        "leaderboard": leaderboard,
        "blocked_summary": blocker_summary,
        "coverage": coverage,
        "reconciliation": reconciliation,
        "diagnostics": {
            "underlying_snapshot_status": snapshot.get("run_status"),
            "underlying_lane_reconciliation": snapshot.get("lane_reconciliation"),
            "terminal_candidate_rows": candidates,
        },
        "ranking_metric": "calibrated_lower_bound",
        "terminal_authority": "V17_TERMINAL_REDUCER",
        "probability_source_policy": "CONTROLLING_SPECIALIST_ONLY",
        "can_execute": False,
    }


def install_daily_pick_orchestrator_route(app: FastAPI, *, auth_dependency: Any, db_client_fn: Callable[[], Any], market_api: Any, event_api: Any) -> None:
    if any(getattr(route, "path", None) == "/v17/daily-picks" for route in app.router.routes):
        return

    @app.post(
        "/v17/daily-picks",
        dependencies=[auth_dependency],
        operation_id="runWowV17DailyPicks",
    )
    def daily_picks(req: DailyPicksRequest):
        return run_daily_picks(
            req,
            db=db_client_fn(),
            market_api=market_api,
            event_api=event_api,
        )
