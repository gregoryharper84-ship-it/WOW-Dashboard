"""Read-only database adapter for LLP V17.1 shadow ranking scorecards."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import Depends, FastAPI, Query

from v17.llp_v17_1_shadow_scorecard import CAN_EXECUTE, evaluate_shadow_rankings

DEFAULT_MAX_GRADES = 5000
IN_FILTER_CHUNK_SIZE = 200


class LLPShadowScorecardBoundaryError(RuntimeError):
    def __init__(self, boundary: str, error: BaseException) -> None:
        super().__init__(f"{boundary}: {type(error).__name__}")
        self.boundary = boundary
        self.error_type = type(error).__name__
        self.__cause__ = error


def _db_call(boundary: str, call: Callable[[], Any]) -> Any:
    try:
        return call()
    except LLPShadowScorecardBoundaryError:
        raise
    except Exception as exc:
        raise LLPShadowScorecardBoundaryError(boundary, exc) from exc


def _chunks(values: list[str], size: int = IN_FILTER_CHUNK_SIZE) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def load_graded_shadow_rows(db: Any, *, max_grades: int = DEFAULT_MAX_GRADES) -> list[dict[str, Any]]:
    if max_grades < 1:
        raise ValueError("INVALID_MAX_GRADES")
    grades = _db_call(
        "wow_llp_v17_1_shadow_grades.select_scorecard",
        lambda: db.table("wow_llp_v17_1_shadow_grades")
        .select("observation_id,outcome_target,settlement_source,settled_at,point_brier,point_log_loss")
        .order("settled_at", desc=True)
        .limit(max_grades)
        .execute().data or [],
    )
    grade_by_id = {
        str(row["observation_id"]): dict(row)
        for row in grades
        if row.get("observation_id") is not None
    }
    observation_ids = list(grade_by_id)
    if not observation_ids:
        return []

    select_fields = (
        "observation_id,prediction_id,candidate_id,official_event_id,sport,league,selection,opponent_or_field,"
        "scheduled_start_utc,requested_slate_date,research_run_id,scan_stage,market_role,controlling_specialist,"
        "model_artifact_id,model_timestamp,observed_at,calibrated_probability,calibrated_lower_bound,"
        "calibrated_upper_bound,lower_bound_width,point_rank_score,lower_bound_rank_score,"
        "uncertainty_adjusted_score,lambda_penalty,governance_class,hard_blockers,soft_uncertainties,"
        "rank_eligible_shadow,market_no_vig_probability,market_divergence,market_divergence_status,"
        "market_prior_weight,can_execute"
    )
    rows: list[dict[str, Any]] = []
    for chunk in _chunks(observation_ids):
        fetched = _db_call(
            "wow_llp_v17_1_shadow_observations.select_scorecard",
            lambda chunk=chunk: db.table("wow_llp_v17_1_shadow_observations")
            .select(select_fields)
            .in_("observation_id", chunk)
            .execute().data or [],
        )
        for raw in fetched:
            observation_id = str(raw.get("observation_id") or "")
            grade = grade_by_id.get(observation_id)
            if grade is None:
                continue
            if raw.get("can_execute") is True:
                raise ValueError("CAN_EXECUTE_INVARIANT_VIOLATION")
            merged = dict(raw)
            merged.update(
                {
                    "outcome_target": grade.get("outcome_target"),
                    "settlement_source": grade.get("settlement_source"),
                    "settled_at": grade.get("settled_at"),
                    "stored_point_brier": grade.get("point_brier"),
                    "stored_point_log_loss": grade.get("point_log_loss"),
                }
            )
            rows.append(merged)
    return rows


def shadow_scorecard(
    db: Any,
    *,
    max_grades: int = DEFAULT_MAX_GRADES,
    min_cohort_events: int = 30,
) -> dict[str, Any]:
    rows = load_graded_shadow_rows(db, max_grades=max_grades)
    report = evaluate_shadow_rankings(rows, min_cohort_events=min_cohort_events)
    report["source"] = "WOW_LLP_V17_1_IMMUTABLE_SHADOW_LEDGER"
    report["max_grades"] = max_grades
    report["can_execute"] = CAN_EXECUTE
    return report


def install_llp_v17_1_shadow_scorecard_routes(
    app: FastAPI,
    *,
    get_client_fn: Callable[[], Any],
    auth_dependency: Any | None = None,
) -> bool:
    if getattr(app.state, "v17_llp_shadow_scorecard_routes_installed", False):
        return True
    dependencies = [Depends(auth_dependency)] if callable(auth_dependency) else []

    @app.get(
        "/v17/llp/shadow/scorecard",
        operation_id="getWowV17LLPSharpnessShadowScorecard",
        dependencies=dependencies,
    )
    def get_scorecard(
        max_grades: int = Query(default=DEFAULT_MAX_GRADES, ge=1, le=20000),
        min_cohort_events: int = Query(default=30, ge=1, le=10000),
    ):
        return shadow_scorecard(
            get_client_fn(),
            max_grades=max_grades,
            min_cohort_events=min_cohort_events,
        )

    app.state.v17_llp_shadow_scorecard_routes_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "DEFAULT_MAX_GRADES",
    "LLPShadowScorecardBoundaryError",
    "install_llp_v17_1_shadow_scorecard_routes",
    "load_graded_shadow_rows",
    "shadow_scorecard",
]
