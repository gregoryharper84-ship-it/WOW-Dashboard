"""Server-owned persistence/runtime for WOW V17 Core Intelligence.

Runs out of band from scoring. Settlement remains authoritative; this module only
copies frozen prediction + settlement facts into the append-only intelligence
ledger and derives cohort diagnostics/hypotheses for review.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import fields
from decimal import Decimal
import os
from typing import Any, Callable

from fastapi import Depends, FastAPI, Query

from v17.core_intelligence import (
    AUTHORITY,
    CAN_EXECUTE,
    LearningObservation,
    build_learning_observation,
    cohort_key,
    detect_learning_hypotheses,
    summarize_cohort,
)
from v17.independent_research_audit import (
    MODEL_FAMILY as RESEARCH_MLB_SO_MODEL,
    audit_mlb_strikeout_forecasts,
)
from v17.llp_v17_1_shadow_internal_routes import install_llp_v17_1_shadow_internal_routes
from v17.postmortem_learning_ledger import signed_distance_to_threshold

PAGE_SIZE = 1000
IN_FILTER_CHUNK_SIZE = 200
DEFAULT_MAX_OUTCOMES = 1000
DEFAULT_MAX_OBSERVATIONS = 5000


class CoreIntelligenceBoundaryError(RuntimeError):
    def __init__(self, boundary: str, error: BaseException) -> None:
        super().__init__(f"{boundary}: {type(error).__name__}")
        self.boundary = boundary
        self.error_type = type(error).__name__
        self.__cause__ = error


def _db_call(boundary: str, call: Callable[[], Any]) -> Any:
    try:
        return call()
    except CoreIntelligenceBoundaryError:
        raise
    except Exception as exc:
        raise CoreIntelligenceBoundaryError(boundary, exc) from exc


def _chunks(values: list[str], size: int = IN_FILTER_CHUNK_SIZE) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return None


def _observation_payload(observation: LearningObservation) -> dict[str, Any]:
    return observation.as_dict()


def _observation_from_row(row: dict[str, Any]) -> LearningObservation:
    payload = dict(row)
    payload.pop("created_at", None)
    payload["diagnostic_tags"] = tuple(payload.get("diagnostic_tags") or ())
    for name in (
        "probability",
        "calibrated_lower_bound",
        "residual",
        "brier_score",
        "log_loss",
        "actual_value",
        "signed_distance_to_threshold",
    ):
        payload[name] = _as_float(payload.get(name))
    allowed = {field.name for field in fields(LearningObservation)}
    return LearningObservation(**{key: value for key, value in payload.items() if key in allowed})


def capture_settled_prop_observations(
    db: Any,
    *,
    max_outcomes: int = DEFAULT_MAX_OUTCOMES,
) -> dict[str, Any]:
    """Append learning rows for authoritative settled prop predictions."""
    if max_outcomes < 1:
        raise ValueError("INVALID_MAX_OUTCOMES")
    outcomes = _db_call(
        "wow_outcomes.select_core_intelligence_candidates",
        lambda: db.table("wow_outcomes")
        .select(
            "prediction_id,official_result,actual_stat,hit,push,void,"
            "settlement_source,settlement_timestamp,failure_category"
        )
        .order("settlement_timestamp", desc=True)
        .limit(max_outcomes)
        .execute().data or [],
    )
    outcome_by_id = {
        str(row["prediction_id"]): dict(row)
        for row in outcomes
        if row.get("prediction_id") and row.get("settlement_timestamp")
    }
    prediction_ids = list(outcome_by_id)
    if not prediction_ids:
        return {
            "status": "PASS",
            "candidate_n": 0,
            "observation_n": 0,
            "authority": AUTHORITY,
            "can_execute": False,
        }

    prediction_by_id: dict[str, dict[str, Any]] = {}
    select_fields = (
        "prediction_id,sport,market_type,stat_type,line,direction,"
        "model_family,model_artifact_version,calibration_version,"
        "calibrated_probability,calibrated_probability_lower_bound,"
        "raw_model_probability,failure_cause_tags"
    )
    for chunk in _chunks(prediction_ids):
        rows = _db_call(
            "wow_predictions.select_core_intelligence_sources",
            lambda chunk=chunk: db.table("wow_predictions")
            .select(select_fields)
            .in_("prediction_id", chunk)
            .execute().data or [],
        )
        for row in rows:
            if row.get("prediction_id"):
                prediction_by_id[str(row["prediction_id"])] = dict(row)

    observations: list[dict[str, Any]] = []
    skipped_n = 0
    for prediction_id, outcome in outcome_by_id.items():
        prediction = prediction_by_id.get(prediction_id)
        if prediction is None:
            skipped_n += 1
            continue
        enriched = dict(outcome)
        enriched["signed_distance_to_threshold"] = signed_distance_to_threshold(
            actual_value=outcome.get("actual_stat"),
            line=prediction.get("line"),
            direction=prediction.get("direction"),
        )
        if not enriched.get("process_classification"):
            enriched["process_classification"] = enriched.get("failure_category")
        observation = build_learning_observation(
            prediction,
            enriched,
            source_prediction_kind="PROP",
        )
        observations.append(_observation_payload(observation))

    if observations:
        _db_call(
            "wow_intelligence_observations.insert",
            lambda: db.table("wow_intelligence_observations").upsert(
                observations,
                on_conflict="observation_id",
                ignore_duplicates=True,
            ).execute(),
        )
    return {
        "status": "PASS",
        "candidate_n": len(prediction_ids),
        "observation_n": len(observations),
        "skipped_n": skipped_n,
        "authority": AUTHORITY,
        "can_execute": False,
    }


def load_observations(
    db: Any,
    *,
    max_observations: int = DEFAULT_MAX_OBSERVATIONS,
) -> list[LearningObservation]:
    if max_observations < 1:
        raise ValueError("INVALID_MAX_OBSERVATIONS")
    rows: list[dict[str, Any]] = []
    start = 0
    while len(rows) < max_observations:
        page_end = min(start + PAGE_SIZE, max_observations) - 1
        page = _db_call(
            "wow_intelligence_observations.select",
            lambda start=start, page_end=page_end: db.table("wow_intelligence_observations")
            .select("*")
            .order("settlement_timestamp", desc=True)
            .range(start, page_end)
            .execute().data or [],
        )
        batch = [dict(row) for row in page]
        rows.extend(batch)
        if len(batch) < (page_end - start + 1):
            break
        start = page_end + 1
    return [_observation_from_row(row) for row in rows]


def _retrospective_research_for_cohort(
    db: Any,
    observations: list[LearningObservation],
) -> dict[str, Any] | None:
    """Read-only official-game diagnostic; never certifies independent evidence.

    Historic observations omit event/player/line/as-of identity. Re-fetch the
    frozen prediction sources; never create identity by inference.
    """
    if not observations or not all(
        row.source_prediction_kind == "PROP"
        and (row.sport or "").upper() == "MLB"
        and (row.stat_type or "").upper() == "PITCHER_STRIKEOUTS"
        and row.model_family == RESEARCH_MLB_SO_MODEL
        for row in observations
    ):
        return None

    ids = sorted({row.source_prediction_id for row in observations})
    source_by_id: dict[str, dict[str, Any]] = {}
    fields = (
        "prediction_id,event_id,event_start_time,created_at,model_timestamp,locked_at,"
        "sport,player,stat_type,line,direction,model_family,model_artifact_version,"
        "calibration_version,calibration_status,calibrated_probability,"
        "probability_publishable"
    )
    for chunk in _chunks(ids):
        records = _db_call(
            "wow_predictions.select_core_intelligence_research_identity",
            lambda chunk=chunk: db.table("wow_predictions")
            .select(fields).in_("prediction_id", chunk)
            .execute().data or [],
        )
        for source in records:
            pid = str(source.get("prediction_id") or "")
            if pid:
                source_by_id[pid] = dict(source)

    verified_inputs: list[dict[str, Any]] = []
    missing_n = 0
    probability_conflicts_n = 0
    calibration_states: dict[str, int] = defaultdict(int)
    for row in observations:
        source = source_by_id.get(row.source_prediction_id)
        if source is None:
            missing_n += 1
            continue
        original_probability = _as_float(source.get("calibrated_probability"))
        if (
            row.probability is None or original_probability is None
            or abs(original_probability - row.probability) > 1e-10
        ):
            probability_conflicts_n += 1
            continue
        calibration_states[str(source.get("calibration_status") or "MISSING")] += 1
        record = dict(source)
        record.update({
            "hit": None if row.outcome_target is None else bool(row.outcome_target),
            "push": row.official_result in {"PUSH", "TIE"},
            "void": row.official_result in {"VOID", "REFUND", "VOID_NOT_STARTER"},
            "settlement_source": row.settlement_source,
            "settlement_timestamp": row.settlement_timestamp,
            "actual_stat": row.actual_value,
        })
        verified_inputs.append(record)
    result = audit_mlb_strikeout_forecasts(verified_inputs)
    result["source_prediction_missing_n"] = missing_n
    result["observation_source_probability_conflict_n"] = probability_conflicts_n
    result["calibration_status_counts"] = dict(sorted(calibration_states.items()))
    result["hypothesis_authority"] = "NO_GOVERNED_HYPOTHESIS_AUTHORITY"
    result["can_execute"] = False
    return result


def intelligence_summary(
    db: Any,
    *,
    min_samples: int = 30,
    max_observations: int = DEFAULT_MAX_OBSERVATIONS,
    persist_hypotheses: bool = False,
) -> dict[str, Any]:
    observations = load_observations(db, max_observations=max_observations)
    grouped: dict[str, list[LearningObservation]] = defaultdict(list)
    for observation in observations:
        grouped[cohort_key(observation)].append(observation)

    cohort_rows: list[dict[str, Any]] = []
    hypothesis_rows: list[dict[str, Any]] = []
    for key in sorted(grouped):
        summary = summarize_cohort(grouped[key], min_samples=min_samples)
        result = summary.as_dict()
        result["raw_observation_hypothesis_threshold_met"] = summary.ready_for_hypothesis
        result["metric_population"] = "RAW_OBSERVATIONS_DIAGNOSTIC_ONLY"
        # Source observations lack certified official player/line/market and
        # original as-of/holdout identity. Row count cannot authorize learning.
        result["ready_for_hypothesis"] = False
        result["hypothesis_blocker"] = "CANONICAL_EFFECTIVE_SAMPLE_AND_HOLDOUT_UNVERIFIED"
        research = _retrospective_research_for_cohort(db, grouped[key])
        if research is not None:
            result["retrospective_research_audit"] = research
        cohort_rows.append(result)

    # Do not persist review hypotheses from dependent raw observation rows.
    # The separate governed effective-N + holdout policy remains fail-closed.
    if persist_hypotheses and hypothesis_rows:
        _db_call(
            "wow_intelligence_hypotheses.insert",
            lambda: db.table("wow_intelligence_hypotheses").upsert(
                hypothesis_rows,
                on_conflict="hypothesis_id",
                ignore_duplicates=True,
            ).execute(),
        )

    return {
        "schema_version": "WOW17_CORE_INTELLIGENCE_V1",
        "authority": AUTHORITY,
        "can_execute": CAN_EXECUTE,
        "observation_n": len(observations),
        "cohort_n": len(cohort_rows),
        "hypothesis_n": len(hypothesis_rows),
        "cohorts": cohort_rows,
        "hypotheses": hypothesis_rows,
    }


def run_core_intelligence_cycle(
    db: Any,
    *,
    max_outcomes: int = DEFAULT_MAX_OUTCOMES,
    min_samples: int = 30,
    max_observations: int = DEFAULT_MAX_OBSERVATIONS,
) -> dict[str, Any]:
    capture = capture_settled_prop_observations(db, max_outcomes=max_outcomes)
    summary = intelligence_summary(
        db,
        min_samples=min_samples,
        max_observations=max_observations,
        persist_hypotheses=True,
    )
    return {
        "status": "PASS",
        "capture": capture,
        "intelligence": summary,
        "authority": AUTHORITY,
        "can_execute": False,
    }


def install_core_intelligence_routes(
    app: FastAPI,
    *,
    auth_dependency: Any | None = None,
    get_client_fn: Callable[[], Any],
) -> bool:
    """Install authenticated read/capture routes on a V17 app."""
    if getattr(app.state, "v17_core_intelligence_routes_installed", False):
        return True

    dependencies = [Depends(auth_dependency)] if callable(auth_dependency) else []

    @app.get(
        "/v17/intelligence/summary",
        operation_id="getWowV17CoreIntelligenceSummary",
        dependencies=dependencies,
    )
    def get_summary(
        min_samples: int = Query(default=30, ge=5, le=1000),
        max_observations: int = Query(default=DEFAULT_MAX_OBSERVATIONS, ge=1, le=20000),
    ):
        return intelligence_summary(
            get_client_fn(),
            min_samples=min_samples,
            max_observations=max_observations,
            persist_hypotheses=False,
        )

    @app.post(
        "/v17/intelligence/cycle",
        operation_id="runWowV17CoreIntelligenceCycle",
        dependencies=dependencies,
    )
    def run_cycle(
        max_outcomes: int = Query(default=DEFAULT_MAX_OUTCOMES, ge=1, le=5000),
        min_samples: int = Query(default=30, ge=5, le=1000),
    ):
        return run_core_intelligence_cycle(
            get_client_fn(),
            max_outcomes=max_outcomes,
            min_samples=min_samples,
        )

    if os.getenv("WOW_V17_LLP_SHADOW_AUTOMATION_ACTIVE", "0") == "1":
        install_llp_v17_1_shadow_internal_routes(
            app,
            get_client_fn=get_client_fn,
            existing_auth_dependency=auth_dependency,
        )

    app.state.v17_core_intelligence_routes_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "CoreIntelligenceBoundaryError",
    "capture_settled_prop_observations",
    "load_observations",
    "intelligence_summary",
    "run_core_intelligence_cycle",
    "install_core_intelligence_routes",
]
