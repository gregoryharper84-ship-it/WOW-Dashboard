"""Out-of-band capture and grading runtime for LLP V17.1 shadow ranking evidence.

The runtime reads immutable governed team/event predictions, materializes both
sides across a lambda grid, and appends settlement grades later. It is research
only: failures here must never alter or block production scoring/publication.
"""
from __future__ import annotations

from math import log
from typing import Any, Callable, Iterable, Mapping, Sequence

from fastapi import Depends, FastAPI, Query

from v17.llp_v17_1_sharpness_challenger import HARD_BLOCK_CODES, SOFT_UNCERTAINTY_CODES
from v17.llp_v17_1_shadow_observer import (
    CAN_EXECUTE,
    ShadowObservation,
    build_lambda_grid_observations,
)

DEFAULT_LAMBDAS = (0.0, 0.25, 0.50, 0.75, 1.0)
DEFAULT_MAX_PREDICTIONS = 500
DEFAULT_MAX_OUTCOMES = 1000
IN_FILTER_CHUNK_SIZE = 200


class LLPShadowRuntimeBoundaryError(RuntimeError):
    def __init__(self, boundary: str, error: BaseException) -> None:
        super().__init__(f"{boundary}: {type(error).__name__}")
        self.boundary = boundary
        self.error_type = type(error).__name__
        self.__cause__ = error


def _db_call(boundary: str, call: Callable[[], Any]) -> Any:
    try:
        return call()
    except LLPShadowRuntimeBoundaryError:
        raise
    except Exception as exc:
        raise LLPShadowRuntimeBoundaryError(boundary, exc) from exc


def _chunks(values: list[str], size: int = IN_FILTER_CHUNK_SIZE) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def _text(value: Any) -> str | None:
    token = str(value or "").strip()
    return token or None


def _codes(*values: Any) -> tuple[str, ...]:
    out: list[str] = []
    for value in values:
        if isinstance(value, (list, tuple, set)):
            tokens = value
        elif value in (None, ""):
            tokens = ()
        else:
            tokens = (value,)
        for token in tokens:
            normalized = str(token or "").strip().upper()
            if normalized and normalized not in out:
                out.append(normalized)
    return tuple(out)


def classify_shadow_reasons(source: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Map only already-registered reason codes into hard/soft shadow classes."""
    reasons = _codes(
        source.get("blockers"),
        source.get("terminal_reasons"),
        source.get("rank_eligibility_reasons"),
        source.get("data_gaps"),
    )
    hard = [code for code in reasons if code in HARD_BLOCK_CODES]
    soft = [code for code in reasons if code in SOFT_UNCERTAINTY_CODES]
    if source.get("probability_invalidated") is True or source.get("rerun_required") is True:
        if "STALE_PROBABILITY_AFTER_MATERIAL_UPDATE" not in hard:
            hard.append("STALE_PROBABILITY_AFTER_MATERIAL_UPDATE")
    return tuple(hard), tuple(soft)


def _side_value(source: Mapping[str, Any], side: str, suffix: str) -> Any:
    prefix = "home" if side == "HOME" else "away"
    return source.get(f"calibrated_{prefix}_{suffix}")


def _favorite_role(source: Mapping[str, Any], side: str, selection: str) -> str | None:
    favorite = _text(source.get("favorite_side"))
    if favorite is None:
        return _text(source.get("selected_market_role"))
    if favorite.upper() in {"HOME", "AWAY"}:
        return "FAVORITE" if favorite.upper() == side else "UNDERDOG"
    return "FAVORITE" if favorite.casefold() == selection.casefold() else "UNDERDOG"


def event_prediction_side_rows(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Normalize both event sides into the generic observer contract."""
    prediction_id = _text(source.get("event_prediction_id")) or _text(source.get("prediction_id"))
    official_event_id = _text(source.get("official_event_id"))
    sport = _text(source.get("sport"))
    if not prediction_id or not official_event_id or not sport:
        return []

    hard, soft = classify_shadow_reasons(source)
    rows: list[dict[str, Any]] = []
    for side in ("HOME", "AWAY"):
        selection = _text(source.get("home_team" if side == "HOME" else "away_team"))
        opponent = _text(source.get("away_team" if side == "HOME" else "home_team"))
        probability = _side_value(source, side, "probability")
        lower = _side_value(source, side, "lower_bound")
        upper = _side_value(source, side, "upper_bound")
        if selection is None or probability is None or lower is None:
            continue
        market_probability = source.get(
            "market_prior_home_probability" if side == "HOME" else "market_prior_away_probability"
        )
        rows.append(
            {
                "prediction_id": prediction_id,
                "candidate_id": f"{prediction_id}:{side}",
                "official_event_id": official_event_id,
                "sport": sport,
                "league": source.get("league"),
                "selection": selection,
                "opponent_or_field": opponent,
                "scheduled_start_utc": source.get("event_start_time") or source.get("scheduled_start_utc"),
                "requested_slate_date": source.get("requested_slate_date"),
                "research_run_id": source.get("research_run_id"),
                "scan_stage": source.get("scan_stage"),
                "market_role": _favorite_role(source, side, selection),
                "controlling_specialist": source.get("controlling_specialist"),
                "model_artifact_id": source.get("model_artifact_id") or source.get("model_artifact_version"),
                "model_timestamp": source.get("model_timestamp") or source.get("immutable_model_timestamp"),
                "calibrated_probability": probability,
                "calibrated_lower_bound": lower,
                "calibrated_upper_bound": upper,
                "market_no_vig_probability": market_probability,
                "hard_blockers": hard,
                "soft_uncertainties": soft,
            }
        )
    return rows


def serialize_observation(observation: ShadowObservation) -> dict[str, Any]:
    payload = observation.as_dict()
    payload["hard_blockers"] = list(observation.hard_blockers)
    payload["soft_uncertainties"] = list(observation.soft_uncertainties)
    return payload


def materialize_event_prediction_shadows(
    source: Mapping[str, Any],
    *,
    lambdas: Sequence[float] = DEFAULT_LAMBDAS,
    observed_at: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for side_row in event_prediction_side_rows(source):
        market_probability = side_row.pop("market_no_vig_probability", None)
        hard = side_row.pop("hard_blockers", ())
        soft = side_row.pop("soft_uncertainties", ())
        observations = build_lambda_grid_observations(
            side_row,
            lambdas=lambdas,
            hard_blockers=hard,
            soft_uncertainties=soft,
            market_no_vig_probability=market_probability,
            observed_at=observed_at,
        )
        rows.extend(serialize_observation(row) for row in observations)
    return rows


def _event_prediction_select_fields() -> str:
    return (
        "event_prediction_id,research_run_id,official_event_id,requested_slate_date,scan_stage,event_start_time,"
        "sport,league,home_team,away_team,controlling_specialist,model_artifact_id,model_timestamp,"
        "calibrated_home_probability,calibrated_home_lower_bound,calibrated_home_upper_bound,"
        "calibrated_away_probability,calibrated_away_lower_bound,calibrated_away_upper_bound,"
        "market_prior_home_probability,market_prior_away_probability,favorite_side,selected_market_role,"
        "blockers,terminal_reasons,rank_eligibility_reasons,data_gaps,probability_invalidated,rerun_required,"
        "probability_publishable,rank_eligible,terminal_label,can_execute"
    )


def capture_event_prediction_shadows(
    db: Any,
    *,
    prediction_ids: Sequence[str] | None = None,
    max_predictions: int = DEFAULT_MAX_PREDICTIONS,
    lambdas: Sequence[float] = DEFAULT_LAMBDAS,
) -> dict[str, Any]:
    """Append shadow rows for both sides of governed event predictions."""
    if max_predictions < 1:
        raise ValueError("INVALID_MAX_PREDICTIONS")
    sources: list[dict[str, Any]] = []
    ids = [str(value) for value in (prediction_ids or ()) if str(value).strip()]
    if ids:
        for chunk in _chunks(ids):
            fetched = _db_call(
                "wow_event_predictions.select_llp_shadow_ids",
                lambda chunk=chunk: db.table("wow_event_predictions")
                .select(_event_prediction_select_fields())
                .in_("event_prediction_id", chunk)
                .execute().data or [],
            )
            sources.extend(dict(row) for row in fetched)
    else:
        fetched = _db_call(
            "wow_event_predictions.select_llp_shadow_recent",
            lambda: db.table("wow_event_predictions")
            .select(_event_prediction_select_fields())
            .order("created_at", desc=True)
            .limit(max_predictions)
            .execute().data or [],
        )
        sources = [dict(row) for row in fetched]

    observations: list[dict[str, Any]] = []
    skipped_sources = 0
    for source in sources[:max_predictions]:
        if source.get("can_execute") is True:
            raise ValueError("CAN_EXECUTE_INVARIANT_VIOLATION")
        materialized = materialize_event_prediction_shadows(source, lambdas=lambdas)
        if not materialized:
            skipped_sources += 1
            continue
        observations.extend(materialized)

    if observations:
        for offset in range(0, len(observations), 250):
            chunk = observations[offset:offset + 250]
            _db_call(
                "wow_llp_v17_1_shadow_observations.append",
                lambda chunk=chunk: db.table("wow_llp_v17_1_shadow_observations")
                .upsert(chunk, on_conflict="observation_id", ignore_duplicates=True)
                .execute(),
            )

    return {
        "status": "PASS",
        "source_prediction_n": len(sources[:max_predictions]),
        "skipped_source_n": skipped_sources,
        "shadow_observation_n": len(observations),
        "lambda_grid": tuple(float(value) for value in lambdas),
        "serving_mode": "SHADOW_ONLY",
        "production_mutated": False,
        "can_execute": CAN_EXECUTE,
    }


def winner_team(source: Mapping[str, Any], outcome: Mapping[str, Any]) -> str | None:
    if outcome.get("void") is True:
        return None
    home_team = _text(source.get("home_team"))
    away_team = _text(source.get("away_team"))
    home_score = outcome.get("home_score")
    away_score = outcome.get("away_score")
    if (
        home_team
        and away_team
        and isinstance(home_score, int)
        and not isinstance(home_score, bool)
        and isinstance(away_score, int)
        and not isinstance(away_score, bool)
        and home_score != away_score
    ):
        return home_team if home_score > away_score else away_team
    official = _text(outcome.get("official_winner"))
    if official and home_team and official.casefold() == home_team.casefold():
        return home_team
    if official and away_team and official.casefold() == away_team.casefold():
        return away_team
    return None


def build_shadow_grade_rows(
    observations: Iterable[Mapping[str, Any]],
    *,
    winner: str,
    settlement_source: str | None,
    settled_at: str,
) -> list[dict[str, Any]]:
    grades: list[dict[str, Any]] = []
    for observation in observations:
        observation_id = _text(observation.get("observation_id"))
        selection = _text(observation.get("selection"))
        probability = observation.get("calibrated_probability")
        if observation_id is None or selection is None or probability is None:
            continue
        p = float(probability)
        if not 0.0 < p < 1.0:
            continue
        outcome_target = 1 if selection.casefold() == winner.casefold() else 0
        clipped = min(max(p, 1e-12), 1.0 - 1e-12)
        brier = (p - outcome_target) ** 2
        log_loss = -(outcome_target * log(clipped) + (1 - outcome_target) * log(1.0 - clipped))
        grades.append(
            {
                "observation_id": observation_id,
                "outcome_target": outcome_target,
                "settlement_source": settlement_source,
                "settled_at": settled_at,
                "point_brier": brier,
                "point_log_loss": log_loss,
                "can_execute": False,
            }
        )
    return grades


def grade_settled_event_shadows(
    db: Any,
    *,
    max_outcomes: int = DEFAULT_MAX_OUTCOMES,
) -> dict[str, Any]:
    """Append immutable outcome grades for already-captured shadow observations."""
    if max_outcomes < 1:
        raise ValueError("INVALID_MAX_OUTCOMES")
    outcomes = _db_call(
        "wow_event_outcomes.select_llp_shadow_grades",
        lambda: db.table("wow_event_outcomes")
        .select("event_prediction_id,official_winner,home_score,away_score,void,settlement_source,settlement_timestamp")
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
        return {"status": "PASS", "candidate_n": 0, "grade_n": 0, "can_execute": False}

    source_by_id: dict[str, dict[str, Any]] = {}
    observation_by_prediction: dict[str, list[dict[str, Any]]] = {}
    for chunk in _chunks(prediction_ids):
        source_rows = _db_call(
            "wow_event_predictions.select_llp_shadow_grade_sources",
            lambda chunk=chunk: db.table("wow_event_predictions")
            .select("event_prediction_id,home_team,away_team")
            .in_("event_prediction_id", chunk)
            .execute().data or [],
        )
        for row in source_rows:
            if row.get("event_prediction_id"):
                source_by_id[str(row["event_prediction_id"])] = dict(row)

        shadow_rows = _db_call(
            "wow_llp_v17_1_shadow_observations.select_ungraded_candidates",
            lambda chunk=chunk: db.table("wow_llp_v17_1_shadow_observations")
            .select("observation_id,prediction_id,selection,calibrated_probability")
            .in_("prediction_id", chunk)
            .execute().data or [],
        )
        for row in shadow_rows:
            if row.get("prediction_id"):
                observation_by_prediction.setdefault(str(row["prediction_id"]), []).append(dict(row))

    grades: list[dict[str, Any]] = []
    unresolved = 0
    for prediction_id, outcome in outcome_by_id.items():
        source = source_by_id.get(prediction_id)
        if source is None:
            unresolved += 1
            continue
        winner = winner_team(source, outcome)
        settled_at = _text(outcome.get("settlement_timestamp"))
        if winner is None or settled_at is None:
            unresolved += 1
            continue
        grades.extend(
            build_shadow_grade_rows(
                observation_by_prediction.get(prediction_id, ()),
                winner=winner,
                settlement_source=_text(outcome.get("settlement_source")),
                settled_at=settled_at,
            )
        )

    if grades:
        for offset in range(0, len(grades), 250):
            chunk = grades[offset:offset + 250]
            _db_call(
                "wow_llp_v17_1_shadow_grades.append",
                lambda chunk=chunk: db.table("wow_llp_v17_1_shadow_grades")
                .upsert(chunk, on_conflict="observation_id", ignore_duplicates=True)
                .execute(),
            )

    return {
        "status": "PASS",
        "candidate_n": len(prediction_ids),
        "unresolved_n": unresolved,
        "grade_n": len(grades),
        "serving_mode": "SHADOW_ONLY",
        "production_mutated": False,
        "can_execute": False,
    }


def install_llp_v17_1_shadow_routes(
    app: FastAPI,
    *,
    get_client_fn: Callable[[], Any],
    auth_dependency: Any | None = None,
) -> bool:
    if getattr(app.state, "v17_llp_shadow_routes_installed", False):
        return True
    dependencies = [Depends(auth_dependency)] if callable(auth_dependency) else []

    @app.post(
        "/v17/llp/shadow/capture",
        operation_id="captureWowV17LLPSharpnessShadow",
        dependencies=dependencies,
    )
    def capture(max_predictions: int = Query(default=DEFAULT_MAX_PREDICTIONS, ge=1, le=5000)):
        return capture_event_prediction_shadows(get_client_fn(), max_predictions=max_predictions)

    @app.post(
        "/v17/llp/shadow/grade",
        operation_id="gradeWowV17LLPSharpnessShadow",
        dependencies=dependencies,
    )
    def grade(max_outcomes: int = Query(default=DEFAULT_MAX_OUTCOMES, ge=1, le=5000)):
        return grade_settled_event_shadows(get_client_fn(), max_outcomes=max_outcomes)

    app.state.v17_llp_shadow_routes_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "DEFAULT_LAMBDAS",
    "LLPShadowRuntimeBoundaryError",
    "build_shadow_grade_rows",
    "capture_event_prediction_shadows",
    "classify_shadow_reasons",
    "event_prediction_side_rows",
    "grade_settled_event_shadows",
    "install_llp_v17_1_shadow_routes",
    "materialize_event_prediction_shadows",
    "serialize_observation",
    "winner_team",
]
