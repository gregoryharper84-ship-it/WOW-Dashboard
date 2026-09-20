"""Team/event adapter for the WOW V17 Core Intelligence ledger.

The event ledger stores two-sided probabilities. Core Intelligence evaluates the
HOME side as a canonical binary forecast so every settled event contributes one
unambiguous probability/outcome pair without depending on recommendation choice.
This module is advisory only and can_execute=false.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import Depends, FastAPI, Query

from v17.core_intelligence import AUTHORITY, build_learning_observation

CAN_EXECUTE = False
IN_FILTER_CHUNK_SIZE = 200
DEFAULT_MAX_OUTCOMES = 1000


class CoreIntelligenceEventBoundaryError(RuntimeError):
    def __init__(self, boundary: str, error: BaseException) -> None:
        super().__init__(f"{boundary}: {type(error).__name__}")
        self.boundary = boundary
        self.error_type = type(error).__name__
        self.__cause__ = error


def _db_call(boundary: str, call: Callable[[], Any]) -> Any:
    try:
        return call()
    except CoreIntelligenceEventBoundaryError:
        raise
    except Exception as exc:
        raise CoreIntelligenceEventBoundaryError(boundary, exc) from exc


def _chunks(values: list[str], size: int = IN_FILTER_CHUNK_SIZE) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def capture_settled_event_observations(
    db: Any,
    *,
    max_outcomes: int = DEFAULT_MAX_OUTCOMES,
) -> dict[str, Any]:
    """Append canonical HOME-side learning rows from authoritative event outcomes."""
    if max_outcomes < 1:
        raise ValueError("INVALID_MAX_OUTCOMES")

    outcomes = _db_call(
        "wow_event_outcomes.select_core_intelligence_candidates",
        lambda: db.table("wow_event_outcomes")
        .select(
            "event_prediction_id,official_winner,void,settlement_source,"
            "settlement_timestamp,failure_category"
        )
        .order("settlement_timestamp", desc=True)
        .limit(max_outcomes)
        .execute().data or [],
    )
    outcome_by_id = {
        str(row["event_prediction_id"]): dict(row)
        for row in outcomes
        if row.get("event_prediction_id") and row.get("settlement_timestamp")
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
        "event_prediction_id,sport,league,market_family,home_team,away_team,"
        "controlling_specialist,model_version,calibration_version,"
        "calibrated_home_probability,calibrated_home_lower_bound,"
        "raw_home_probability"
    )
    for chunk in _chunks(prediction_ids):
        rows = _db_call(
            "wow_event_predictions.select_core_intelligence_sources",
            lambda chunk=chunk: db.table("wow_event_predictions")
            .select(select_fields)
            .in_("event_prediction_id", chunk)
            .execute().data or [],
        )
        for row in rows:
            if row.get("event_prediction_id"):
                prediction_by_id[str(row["event_prediction_id"])] = dict(row)

    observations: list[dict[str, Any]] = []
    skipped_n = 0
    for prediction_id, outcome in outcome_by_id.items():
        source = prediction_by_id.get(prediction_id)
        if source is None:
            skipped_n += 1
            continue

        home_team = str(source.get("home_team") or "").strip()
        winner = str(outcome.get("official_winner") or "").strip()
        if outcome.get("void") is True:
            official_result = "VOID"
        elif home_team and winner:
            official_result = "WIN" if winner.casefold() == home_team.casefold() else "LOSS"
        else:
            skipped_n += 1
            continue

        canonical_prediction = {
            "event_prediction_id": prediction_id,
            "sport": source.get("sport"),
            "league": source.get("league"),
            "market_family": source.get("market_family"),
            "direction": "HOME",
            "model_family": source.get("controlling_specialist"),
            "model_version": source.get("model_version"),
            "calibration_version": source.get("calibration_version"),
            "calibrated_probability": source.get("calibrated_home_probability"),
            "calibrated_probability_lower_bound": source.get("calibrated_home_lower_bound"),
            "raw_model_probability": source.get("raw_home_probability"),
        }
        canonical_outcome = {
            "event_prediction_id": prediction_id,
            "official_result": official_result,
            "settlement_source": outcome.get("settlement_source"),
            "settlement_timestamp": outcome.get("settlement_timestamp"),
            "process_classification": outcome.get("failure_category"),
        }
        observations.append(
            build_learning_observation(
                canonical_prediction,
                canonical_outcome,
                source_prediction_kind="EVENT",
            ).as_dict()
        )

    if observations:
        _db_call(
            "wow_intelligence_observations.insert_event",
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


def install_core_intelligence_event_routes(
    app: FastAPI,
    *,
    get_client_fn: Callable[[], Any],
    auth_dependency: Any | None = None,
) -> bool:
    if getattr(app.state, "v17_core_intelligence_event_routes_installed", False):
        return True
    dependencies = [Depends(auth_dependency)] if callable(auth_dependency) else []

    @app.post(
        "/v17/intelligence/event-capture",
        operation_id="captureWowV17CoreIntelligenceEvents",
        dependencies=dependencies,
    )
    def capture_events(
        max_outcomes: int = Query(default=DEFAULT_MAX_OUTCOMES, ge=1, le=5000),
    ):
        return capture_settled_event_observations(
            get_client_fn(),
            max_outcomes=max_outcomes,
        )

    app.state.v17_core_intelligence_event_routes_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "CoreIntelligenceEventBoundaryError",
    "capture_settled_event_observations",
    "install_core_intelligence_event_routes",
]
