"""Research-candidate-aware NCAAF team/event failure refinement.

NCAAF has a governed challenger pipeline but no certified production team/event
bridge. Returning MODEL_UNAVAILABLE for that state loses important typed truth:
a fitted research candidate may exist while source review, fresh current-season
history, calibration/certification, or production registration is still missing.

This overlay never scores a stale/uncertified candidate and never promotes it.
It only converts the exact generic NCAAF adapter-absence failure into
MODEL_INPUTS_INSUFFICIENT with durable, inspectable blockers when a real fitted
candidate exists in the governed registry.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from fastapi import HTTPException

CAN_EXECUTE = False
SPORT = "NCAAF"
GENERIC_BLOCKER = "NCAAF_TEAM_EVENT_FITTED_MODEL_OR_ADAPTER_UNAVAILABLE"
SOURCE_REVIEW_BLOCKER = "NCAAF_CANDIDATE_SOURCE_REVIEW_REQUIRED"
CERTIFICATION_BLOCKER = "NCAAF_CANDIDATE_NOT_CERTIFIED"
HISTORY_STALE_BLOCKER = "NCAAF_CURRENT_SEASON_HISTORY_STALE"
HISTORY_MAX_GAP_DAYS = 8


def _sport(req: Any) -> str:
    return str(getattr(req, "sport", None) or getattr(req, "league", None) or "").strip().upper()


def _dt(value: Any) -> datetime | None:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _request_start(req: Any) -> datetime | None:
    for field in ("commence_time", "event_start_time", "start_time", "event_time"):
        value = _dt(getattr(req, field, None))
        if value is not None:
            return value
    return None


def _client(event_api: Any) -> Any | None:
    getter = getattr(event_api, "get_client", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return None
    return None


def _latest_candidate(db: Any) -> dict[str, Any] | None:
    try:
        result = (
            db.table("wow_d1_candidate_artifacts")
            .select(
                "candidate_id,model_family,model_artifact_version,feature_schema_version,"
                "source_review_status,lifecycle_state,research_screen_pass,"
                "training_rows,calibration_rows,test_rows,probability_publishable,can_execute,created_at"
            )
            .eq("sport", SPORT)
            .eq("lifecycle_state", "CANDIDATE")
            .eq("probability_publishable", False)
            .eq("can_execute", False)
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )
    except Exception:
        return None
    rows = [dict(row) for row in (getattr(result, "data", None) or []) if isinstance(row, Mapping)]
    if not rows:
        return None
    passing = [row for row in rows if row.get("research_screen_pass") is True]
    return passing[0] if passing else rows[0]


def _latest_current_season_game(db: Any, *, season: int) -> datetime | None:
    try:
        result = (
            db.table("wow_ncaaf_training_games")
            .select("event_start_time")
            .eq("season", season)
            .order("event_start_time", desc=True)
            .limit(1)
            .execute()
        )
    except Exception:
        return None
    rows = list(getattr(result, "data", None) or [])
    if not rows or not isinstance(rows[0], Mapping):
        return None
    return _dt(rows[0].get("event_start_time"))


def _generic_ncaaf_unavailable(detail: Mapping[str, Any]) -> bool:
    if str(detail.get("code") or "").strip().upper() != "MODEL_UNAVAILABLE":
        return False
    tokens = {str(value) for value in (detail.get("blockers") or [])}
    tokens.add(str(detail.get("blocker_code") or ""))
    return GENERIC_BLOCKER in tokens or str(detail.get("backend_route_status") or "") == "SPORT_SPECIFIC_TEAM_EVENT_ADAPTER_NOT_REGISTERED"


def _candidate_hold(req: Any, event_api: Any, original: Mapping[str, Any]) -> dict[str, Any] | None:
    db = _client(event_api)
    if db is None:
        return None
    candidate = _latest_candidate(db)
    if candidate is None:
        return None

    target_start = _request_start(req)
    season = target_start.year if target_start is not None else datetime.now(timezone.utc).year
    latest_history = _latest_current_season_game(db, season=season)

    blockers: list[str] = []
    source_review = str(candidate.get("source_review_status") or "REQUIRED").strip().upper()
    if source_review != "PASS":
        blockers.append(SOURCE_REVIEW_BLOCKER)
    blockers.append(CERTIFICATION_BLOCKER)

    stale_gap_days: float | None = None
    if target_start is not None:
        if latest_history is None:
            blockers.append(HISTORY_STALE_BLOCKER)
        else:
            stale_gap_days = (target_start - latest_history).total_seconds() / 86400.0
            if stale_gap_days > HISTORY_MAX_GAP_DAYS:
                blockers.append(HISTORY_STALE_BLOCKER)

    failed_scope = ["CONTROLLING_SPECIALIST", "CERTIFICATION"]
    if SOURCE_REVIEW_BLOCKER in blockers:
        failed_scope.append("SOURCE_REVIEW")
    if HISTORY_STALE_BLOCKER in blockers:
        failed_scope.append("CURRENT_SEASON_HISTORY")

    out = dict(original)
    out.update({
        "code": "MODEL_INPUTS_INSUFFICIENT",
        "blocker_code": blockers[0] if blockers else CERTIFICATION_BLOCKER,
        "blockers": list(dict.fromkeys(blockers or [CERTIFICATION_BLOCKER])),
        "backend_route_status": "NCAAF_RESEARCH_CANDIDATE_PRESENT_NOT_PRODUCTION_ADMISSIBLE",
        "candidate_family": "TEAM_EVENT",
        "candidate_id": candidate.get("candidate_id"),
        "candidate_model_family": candidate.get("model_family"),
        "candidate_model_artifact_version": candidate.get("model_artifact_version"),
        "candidate_feature_schema_version": candidate.get("feature_schema_version"),
        "candidate_lifecycle_state": candidate.get("lifecycle_state"),
        "candidate_research_screen_pass": candidate.get("research_screen_pass"),
        "candidate_source_review_status": source_review,
        "candidate_training_rows": candidate.get("training_rows"),
        "candidate_calibration_rows": candidate.get("calibration_rows"),
        "candidate_test_rows": candidate.get("test_rows"),
        "latest_current_season_history_at": latest_history.isoformat() if latest_history else None,
        "target_event_start_time": target_start.isoformat() if target_start else None,
        "current_season_history_gap_days": stale_gap_days,
        "history_max_gap_days": HISTORY_MAX_GAP_DAYS,
        "model_candidate_present": True,
        "model_supported_for_research": True,
        "production_specialist_registered": False,
        "specialist_invoked": False,
        "model_evaluated": False,
        "scoring_attempted": False,
        "failed_contract_scope": failed_scope,
        "market_probability_substitution_allowed": False,
        "generic_reasoning_substitution_allowed": False,
        "probability_publishable": False,
        "rank_eligible": False,
        "can_execute": False,
    })
    return out


def install_ncaaf_team_event_candidate_hold() -> bool:
    import v17.team_event_bridge_runtime as bridges
    import v17.team_event_request_runtime as base_runtime

    if getattr(bridges, "_v17_ncaaf_candidate_hold_installed", False):
        return True

    original_score = bridges.score_registered_team_event_request

    def score_with_ncaaf_candidate_hold(
        req: Any,
        *,
        event_api: Any,
        canonical_hydration_required: bool = False,
    ) -> dict[str, Any]:
        try:
            return original_score(
                req,
                event_api=event_api,
                canonical_hydration_required=canonical_hydration_required,
            )
        except HTTPException as exc:
            if _sport(req) != SPORT or not isinstance(exc.detail, Mapping):
                raise
            detail = dict(exc.detail)
            if not _generic_ncaaf_unavailable(detail):
                raise
            hold = _candidate_hold(req, event_api, detail)
            if hold is None:
                raise
            raise HTTPException(status_code=422, detail=hold, headers=exc.headers) from exc

    bridges.score_registered_team_event_request = score_with_ncaaf_candidate_hold
    base_runtime.score_team_event_request = score_with_ncaaf_candidate_hold
    bridges._v17_ncaaf_candidate_hold_original_score = original_score
    bridges._v17_ncaaf_candidate_hold_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "CERTIFICATION_BLOCKER",
    "GENERIC_BLOCKER",
    "HISTORY_STALE_BLOCKER",
    "SOURCE_REVIEW_BLOCKER",
    "install_ncaaf_team_event_candidate_hold",
]
