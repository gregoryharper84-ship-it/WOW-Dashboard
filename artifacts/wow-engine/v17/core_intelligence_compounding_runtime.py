"""Persistence/runtime for WOW V17 compounding intelligence.

This runtime is deliberately out of band from scoring. It reads immutable Core
Intelligence observations plus frozen source receipts, appends market/signal/
specialist memory, opens challenger proposals, and audits inert D1 candidates.
"""
from __future__ import annotations

from dataclasses import fields
from decimal import Decimal
from typing import Any, Callable, Mapping

from fastapi import Depends, FastAPI, Query

from v17.core_intelligence import LearningHypothesis
from v17.core_intelligence_compounding import (
    AUTHORITY,
    CAN_EXECUTE,
    ChallengerProposal,
    MarketMemoryObservation,
    PromotionReview,
    SignalMemoryObservation,
    build_challenger_proposals,
    build_market_memory,
    evaluate_promotion_review,
    extract_signal_memories,
    summarize_markets,
    summarize_signals,
    summarize_specialists,
)
from v17.core_intelligence_event_runtime import capture_settled_event_observations
from v17.core_intelligence_runtime import (
    capture_settled_prop_observations,
    intelligence_summary,
    load_observations,
)

PAGE_SIZE = 1000
IN_FILTER_CHUNK_SIZE = 200
DEFAULT_MAX_OBSERVATIONS = 5000


class CompoundingIntelligenceBoundaryError(RuntimeError):
    def __init__(self, boundary: str, error: BaseException) -> None:
        super().__init__(f"{boundary}: {type(error).__name__}")
        self.boundary = boundary
        self.error_type = type(error).__name__
        self.__cause__ = error


def _db_call(boundary: str, call: Callable[[], Any]) -> Any:
    try:
        return call()
    except CompoundingIntelligenceBoundaryError:
        raise
    except Exception as exc:
        raise CompoundingIntelligenceBoundaryError(boundary, exc) from exc


def _chunks(values: list[str], size: int = IN_FILTER_CHUNK_SIZE) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return None


def _text(value: Any) -> str | None:
    value = str(value or "").strip()
    return value or None


def _upsert_append_only(db: Any, table: str, rows: list[dict[str, Any]], conflict: str) -> int:
    if not rows:
        return 0
    for offset in range(0, len(rows), 250):
        chunk = rows[offset:offset + 250]
        _db_call(
            f"{table}.append",
            lambda chunk=chunk: db.table(table).upsert(
                chunk, on_conflict=conflict, ignore_duplicates=True
            ).execute(),
        )
    return len(rows)


def _fetch_by_ids(db: Any, table: str, id_field: str, ids: list[str], select_fields: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(ids):
        rows = _db_call(
            f"{table}.source_lookup",
            lambda chunk=chunk: db.table(table)
            .select(select_fields)
            .in_(id_field, chunk)
            .execute().data or [],
        )
        for raw in rows:
            if raw.get(id_field):
                out[str(raw[id_field])] = dict(raw)
    return out


def _source_receipts(db: Any, observations: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    prop_ids = [str(r["source_prediction_id"]) for r in observations if r.get("source_prediction_kind") == "PROP"]
    event_ids = [str(r["source_prediction_id"]) for r in observations if r.get("source_prediction_kind") == "EVENT"]

    prop_predictions = _fetch_by_ids(
        db, "wow_predictions", "prediction_id", prop_ids,
        "prediction_id,sport,market_type,market_family,stat_type,direction,controlling_specialist,"
        "model_family,model_artifact_version,raw_model_probability,independent_model_probability,"
        "calibrated_probability,market_prior_probability,market_prior_quality,market_prior_weight,"
        "reference_market_probability_raw,effective_sample_size,regime_probability_sum,"
        "regime_probabilities_json,primary_failure_path,failure_cause_tags"
    ) if prop_ids else {}
    prop_outcomes = _fetch_by_ids(
        db, "wow_outcomes", "prediction_id", prop_ids,
        "prediction_id,closing_market_probability"
    ) if prop_ids else {}

    event_predictions = _fetch_by_ids(
        db, "wow_event_predictions", "event_prediction_id", event_ids,
        "event_prediction_id,sport,league,market_family,controlling_specialist,model_version,model_artifact_id,"
        "projected_runs_home,projected_runs_away,tie_after_9_probability,home_wins_extras_given_tie,"
        "away_wins_extras_given_tie,market_prior_home_probability,market_prior_weight,market_prior_quality,"
        "favorite_side,favorite_failure_paths_json,largest_favorite_loss_path,favorite_failure_path_probability,"
        "underdog_upset_path_json,home_starter_status,away_starter_status,home_lineup_status,away_lineup_status"
    ) if event_ids else {}
    event_outcomes = _fetch_by_ids(
        db, "wow_event_outcomes", "event_prediction_id", event_ids,
        "event_prediction_id,closing_market_home_probability"
    ) if event_ids else {}
    return prop_predictions, prop_outcomes, event_predictions, event_outcomes


def _hypotheses_from_summary(summary: Mapping[str, Any]) -> list[LearningHypothesis]:
    allowed = {field.name for field in fields(LearningHypothesis)}
    out = []
    for raw in summary.get("hypotheses") or []:
        payload = {key: value for key, value in dict(raw).items() if key in allowed}
        out.append(LearningHypothesis(**payload))
    return out


def _candidate_promotion_reviews(db: Any) -> list[PromotionReview]:
    rows = _db_call(
        "wow_d1_candidate_artifacts.intelligence_review",
        lambda: db.table("wow_d1_candidate_artifacts")
        .select(
            "candidate_id,sport,league,market_family,model_family,model_artifact_version,validation_metrics,"
            "test_rows,research_screen_pass,source_review_status,lifecycle_state,promoted,active,"
            "automatic_certification,automatic_promotion,probability_publishable,can_execute"
        )
        .order("created_at", desc=True)
        .limit(1000)
        .execute().data or [],
    )
    reviews: list[PromotionReview] = []
    for raw in rows:
        metrics = raw.get("validation_metrics") if isinstance(raw.get("validation_metrics"), Mapping) else {}
        challenger_brier = (
            metrics.get("challenger_brier")
            if metrics.get("challenger_brier") is not None
            else metrics.get("calibrated_brier", metrics.get("test_brier"))
        )
        challenger_log = (
            metrics.get("challenger_log_loss")
            if metrics.get("challenger_log_loss") is not None
            else metrics.get("calibrated_log_loss", metrics.get("test_log_loss"))
        )
        target_key = "|".join((
            str(raw.get("sport") or "UNKNOWN_SPORT"),
            str(raw.get("league") or "UNKNOWN_LEAGUE"),
            str(raw.get("market_family") or "UNKNOWN_MARKET"),
            str(raw.get("model_family") or "UNKNOWN_MODEL"),
        ))
        review = evaluate_promotion_review(
            challenger_id=str(raw.get("candidate_id") or raw.get("model_artifact_version") or "UNKNOWN_CANDIDATE"),
            target_key=target_key,
            holdout_n=int(raw.get("test_rows") or 0),
            champion_brier=metrics.get("champion_brier"),
            challenger_brier=challenger_brier,
            champion_log_loss=metrics.get("champion_log_loss"),
            challenger_log_loss=challenger_log,
            champion_calibration_error=metrics.get("champion_calibration_error", metrics.get("champion_ece")),
            challenger_calibration_error=metrics.get("challenger_calibration_error", metrics.get("ece", metrics.get("calibrated_ece"))),
            source_review_pass=str(raw.get("source_review_status") or "").upper() == "PASS",
            certification_replay_pass=bool(metrics.get("certification_replay_pass") or metrics.get("prospective_replay_pass")),
        )
        reviews.append(review)
    return reviews


def run_compounding_memory_cycle(
    db: Any,
    *,
    min_samples: int = 30,
    max_observations: int = DEFAULT_MAX_OBSERVATIONS,
    base_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    observations_dc = load_observations(db, max_observations=max_observations)
    observations = [row.as_dict() for row in observations_dc]
    prop_predictions, prop_outcomes, event_predictions, event_outcomes = _source_receipts(db, observations)

    market_rows: list[MarketMemoryObservation] = []
    signal_rows: list[SignalMemoryObservation] = []
    specialist_inputs: list[dict[str, Any]] = []

    for observation in observations:
        source_id = str(observation.get("source_prediction_id") or "")
        kind = str(observation.get("source_prediction_kind") or "").upper()
        if kind == "PROP":
            prediction = prop_predictions.get(source_id)
            if prediction is None:
                continue
            outcome = prop_outcomes.get(source_id, {})
            specialist_id = _text(prediction.get("controlling_specialist")) or _text(observation.get("model_family"))
            opening = prediction.get("market_prior_probability")
            if opening is None:
                opening = prediction.get("reference_market_probability_raw")
            closing = outcome.get("closing_market_probability")
        elif kind == "EVENT":
            prediction = event_predictions.get(source_id)
            if prediction is None:
                continue
            outcome = event_outcomes.get(source_id, {})
            specialist_id = _text(prediction.get("controlling_specialist")) or _text(observation.get("model_family"))
            opening = prediction.get("market_prior_home_probability")
            closing = outcome.get("closing_market_home_probability")
        else:
            continue

        market_rows.append(build_market_memory(
            observation,
            opening_market_probability=opening,
            closing_market_probability=closing,
            specialist_id=specialist_id,
        ))
        signal_rows.extend(extract_signal_memories(
            observation, prediction, specialist_id=specialist_id
        ))
        specialist = dict(observation)
        specialist["specialist_id"] = specialist_id
        specialist_inputs.append(specialist)

    market_scorecards = summarize_markets(market_rows, min_samples=min_samples)
    signal_scorecards = summarize_signals(signal_rows, min_samples=max(10, min_samples // 2))
    specialist_scorecards = summarize_specialists(
        specialist_inputs, market_rows, min_samples=min_samples
    )

    summary = base_summary or intelligence_summary(
        db,
        min_samples=min_samples,
        max_observations=max_observations,
        persist_hypotheses=True,
    )
    hypotheses = _hypotheses_from_summary(summary)
    proposals = build_challenger_proposals(
        hypotheses=hypotheses,
        signal_scorecards=signal_scorecards,
        market_scorecards=market_scorecards,
        specialist_scorecards=specialist_scorecards,
    )
    promotion_reviews = _candidate_promotion_reviews(db)

    persisted = {
        "market_observations": _upsert_append_only(
            db, "wow_intelligence_market_observations",
            [r.as_dict() for r in market_rows], "market_memory_id"
        ),
        "signal_observations": _upsert_append_only(
            db, "wow_intelligence_signal_observations",
            [r.as_dict() for r in signal_rows], "signal_memory_id"
        ),
        "market_scorecards": _upsert_append_only(
            db, "wow_intelligence_market_scorecards",
            [r.as_dict() for r in market_scorecards], "snapshot_id"
        ),
        "signal_scorecards": _upsert_append_only(
            db, "wow_intelligence_signal_scorecards",
            [r.as_dict() for r in signal_scorecards], "snapshot_id"
        ),
        "specialist_scorecards": _upsert_append_only(
            db, "wow_intelligence_specialist_scorecards",
            [r.as_dict() for r in specialist_scorecards], "snapshot_id"
        ),
        "challenger_proposals": _upsert_append_only(
            db, "wow_intelligence_challenger_proposals",
            [r.as_dict() for r in proposals], "proposal_id"
        ),
        "promotion_reviews": _upsert_append_only(
            db, "wow_intelligence_promotion_reviews",
            [r.as_dict() for r in promotion_reviews], "review_id"
        ),
    }

    return {
        "status": "PASS",
        "authority": AUTHORITY,
        "can_execute": CAN_EXECUTE,
        "observation_n": len(observations),
        "market_memory_n": len(market_rows),
        "signal_memory_n": len(signal_rows),
        "market_scorecard_n": len(market_scorecards),
        "signal_scorecard_n": len(signal_scorecards),
        "specialist_scorecard_n": len(specialist_scorecards),
        "challenger_proposal_n": len(proposals),
        "promotion_review_n": len(promotion_reviews),
        "promotion_eligible_n": sum(1 for row in promotion_reviews if row.eligible_for_governed_review),
        "persisted": persisted,
        "market_scorecards": [r.as_dict() for r in market_scorecards],
        "specialist_scorecards": [r.as_dict() for r in specialist_scorecards],
        "challenger_proposals": [r.as_dict() for r in proposals],
        "promotion_reviews": [r.as_dict() for r in promotion_reviews],
    }


def run_full_intelligence_cycle(
    db: Any,
    *,
    max_outcomes: int = 1000,
    max_observations: int = DEFAULT_MAX_OBSERVATIONS,
    min_samples: int = 30,
) -> dict[str, Any]:
    prop_capture = capture_settled_prop_observations(db, max_outcomes=max_outcomes)
    event_capture = capture_settled_event_observations(db, max_outcomes=max_outcomes)
    summary = intelligence_summary(
        db,
        min_samples=min_samples,
        max_observations=max_observations,
        persist_hypotheses=True,
    )
    compounding = run_compounding_memory_cycle(
        db,
        min_samples=min_samples,
        max_observations=max_observations,
        base_summary=summary,
    )
    return {
        "status": "PASS",
        "prop_capture": prop_capture,
        "event_capture": event_capture,
        "core": summary,
        "compounding": compounding,
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


def install_compounding_intelligence_routes(
    app: FastAPI,
    *,
    get_client_fn: Callable[[], Any],
    auth_dependency: Any | None = None,
) -> bool:
    if getattr(app.state, "v17_compounding_intelligence_routes_installed", False):
        return True
    dependencies = [Depends(auth_dependency)] if callable(auth_dependency) else []

    @app.post(
        "/v17/intelligence/full-cycle",
        operation_id="runWowV17FullIntelligenceCycle",
        dependencies=dependencies,
    )
    def full_cycle(
        max_outcomes: int = Query(default=1000, ge=1, le=5000),
        max_observations: int = Query(default=DEFAULT_MAX_OBSERVATIONS, ge=1, le=20000),
        min_samples: int = Query(default=30, ge=10, le=1000),
    ):
        return run_full_intelligence_cycle(
            get_client_fn(),
            max_outcomes=max_outcomes,
            max_observations=max_observations,
            min_samples=min_samples,
        )

    @app.get(
        "/v17/intelligence/compounding-summary",
        operation_id="getWowV17CompoundingIntelligenceSummary",
        dependencies=dependencies,
    )
    def compounding_summary(
        max_observations: int = Query(default=DEFAULT_MAX_OBSERVATIONS, ge=1, le=20000),
        min_samples: int = Query(default=30, ge=10, le=1000),
    ):
        return run_compounding_memory_cycle(
            get_client_fn(),
            max_observations=max_observations,
            min_samples=min_samples,
        )

    app.state.v17_compounding_intelligence_routes_installed = True
    return True


__all__ = [
    "CAN_EXECUTE",
    "CompoundingIntelligenceBoundaryError",
    "run_compounding_memory_cycle",
    "run_full_intelligence_cycle",
    "install_compounding_intelligence_routes",
]
