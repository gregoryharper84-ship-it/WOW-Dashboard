"""Prospective NFL team/event shadow grading and calibration-health lane.

This module closes the evidence gap between a fitted/registered NFL outright-win
model and governed certification. It grades only immutable pregame predictions
already written by the NFL scorer, deduplicates repeated scoring of one event by
a deterministic pre-outcome rule, and joins outcomes only after settlement.

It never promotes a model, never changes a pregame prediction, never uses market
price as the model forecast, and never authorizes execution.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import os
from typing import Any, Iterable, Mapping, Sequence

from v17.nfl_forward_settlement_refresh import refresh_recent_settled_outcomes

CAN_EXECUTE = False
MIN_FORWARD_GRADED = 100
# V2 is required after the private verified-grade view migration; stale V1
# health snapshots cannot authorize the forward certification admission gate.
HEALTH_SCHEMA_VERSION = "NFL_FORWARD_CALIBRATION_HEALTH_V2"
GRADE_SCHEMA_VERSION = "NFL_FORWARD_SHADOW_GRADE_V1"
RECONCILIATION_SCHEMA_VERSION = "NFL_FORWARD_COHORT_RECONCILIATION_V1"

PREDICTION_TABLE = "wow_nfl_event_predictions"
OUTCOME_TABLE = "wow_nfl_training_games"
GRADE_TABLE = "wow_nfl_forward_shadow_grades"
HEALTH_TABLE = "wow_nfl_forward_calibration_health"


class NFLForwardShadowError(ValueError):
    pass


@dataclass(frozen=True)
class ForwardPrediction:
    event_prediction_id: str
    official_event_id: str
    event_start_time_utc: datetime
    prediction_created_at: datetime
    model_version: str
    home_team: str
    away_team: str
    selected_participant: str
    calibrated_probability: float
    calibrated_lower_bound: float
    calibrated_upper_bound: float
    model_package_valid: bool
    provenance_complete: bool
    model_probability_publishable: bool
    can_execute: bool


@dataclass(frozen=True)
class ForwardGrade:
    event_prediction_id: str
    official_event_id: str
    event_start_time_utc: str
    prediction_created_at: str
    model_version: str
    selected_participant: str
    calibrated_probability: float
    calibrated_lower_bound: float
    calibrated_upper_bound: float
    outcome: int
    brier: float
    log_loss: float
    source_game_id: str
    schema_version: str = GRADE_SCHEMA_VERSION
    can_execute: bool = CAN_EXECUTE


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise NFLForwardShadowError("TIMESTAMP_MUST_BE_TIMEZONE_AWARE")
    return parsed.astimezone(timezone.utc)


def _probability(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise NFLForwardShadowError(f"MODEL_OUTPUT_INVALID:{field}")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise NFLForwardShadowError(f"MODEL_OUTPUT_INVALID:{field}") from exc
    if not math.isfinite(parsed) or not 0.0 < parsed < 1.0:
        raise NFLForwardShadowError(f"MODEL_OUTPUT_INVALID:{field}")
    return parsed


def _safe_log_loss(probability: float, outcome: int) -> float:
    p = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    y = int(outcome)
    return float(-(y * math.log(p) + (1 - y) * math.log(1.0 - p)))


def _settlement_disposition(outcome: Mapping[str, Any] | None) -> str:
    """Classify a raw settlement without weakening binary grade eligibility.

    A present-but-missing or contradictory settlement is not the same as an
    outcome that has not been ingested. A tie is recognized only when the
    scores are equal, the tie flag is true, and the winner flag is false.
    """
    if outcome is None:
        return "ABSENT"
    home_score = outcome.get("home_score")
    away_score = outcome.get("away_score")
    if (
        type(home_score) is not int
        or type(away_score) is not int
        or home_score < 0
        or away_score < 0
    ):
        return "INCOMPLETE"
    home_win = outcome.get("home_win")
    tie = outcome.get("tie")
    if home_score == away_score:
        return "TIE" if tie is True and home_win is False else "INCOMPLETE"
    if tie is not False or type(home_win) is not bool:
        return "INCOMPLETE"
    if home_win != (home_score > away_score):
        return "INCOMPLETE"
    return "BINARY"


def _to_prediction(row: Mapping[str, Any]) -> ForwardPrediction:
    selected = str(row.get("selected_participant") or "").strip()
    home = str(row.get("home_team") or "").strip()
    away = str(row.get("away_team") or "").strip()
    if selected not in {home, away}:
        raise NFLForwardShadowError("PREDICTION_SELECTION_IDENTITY_INVALID")
    p = _probability(row.get("calibrated_selection_probability"), field="calibrated_selection_probability")
    lower = _probability(row.get("calibrated_selection_lower_bound"), field="calibrated_selection_lower_bound")
    upper = _probability(row.get("calibrated_selection_upper_bound"), field="calibrated_selection_upper_bound")
    if not lower <= p <= upper:
        raise NFLForwardShadowError("CALIBRATED_PROBABILITY_BOUNDS_INVALID")
    return ForwardPrediction(
        event_prediction_id=str(row.get("event_prediction_id") or "").strip(),
        official_event_id=str(row.get("official_event_id") or "").strip(),
        event_start_time_utc=_utc(row.get("event_start_time_utc")),
        prediction_created_at=_utc(row.get("created_at")),
        model_version=str(row.get("model_version") or "").strip(),
        home_team=home,
        away_team=away,
        selected_participant=selected,
        calibrated_probability=p,
        calibrated_lower_bound=lower,
        calibrated_upper_bound=upper,
        model_package_valid=row.get("model_package_valid") is True,
        provenance_complete=row.get("provenance_complete") is True,
        model_probability_publishable=row.get("model_probability_publishable") is True,
        can_execute=row.get("can_execute") is True,
    )


def select_canonical_forward_predictions(rows: Sequence[Mapping[str, Any]]) -> list[ForwardPrediction]:
    """Choose one immutable prediction per event without consulting outcomes.

    Repeated runs for an event are expected. The canonical prospective row is the
    latest *valid* prediction created before event start. This rule depends only
    on immutable pregame fields and is therefore outcome-independent.
    """
    by_event: dict[str, list[ForwardPrediction]] = {}
    for raw in rows:
        prediction = _to_prediction(raw)
        if not prediction.event_prediction_id or not prediction.official_event_id:
            raise NFLForwardShadowError("PREDICTION_IDENTITY_MISSING")
        if prediction.can_execute:
            raise NFLForwardShadowError("CAN_EXECUTE_MUST_BE_FALSE")
        if not (
            prediction.model_package_valid
            and prediction.provenance_complete
            and prediction.model_probability_publishable
        ):
            continue
        if prediction.prediction_created_at >= prediction.event_start_time_utc:
            continue
        by_event.setdefault(prediction.official_event_id, []).append(prediction)

    selected: list[ForwardPrediction] = []
    for event_id, candidates in by_event.items():
        candidates.sort(key=lambda row: (row.prediction_created_at, row.event_prediction_id))
        winner = candidates[-1]
        if winner.official_event_id != event_id:
            raise NFLForwardShadowError("PREDICTION_EVENT_RECONCILIATION_FAILED")
        selected.append(winner)
    return sorted(selected, key=lambda row: (row.event_start_time_utc, row.official_event_id))


def _outcome_index(rows: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        game_id = str(row.get("game_id") or "").strip()
        if not game_id:
            continue
        # Keep present-but-incomplete rows. Reconciliation must distinguish a
        # missing score from a genuinely un-ingested outcome; grading validates
        # score usability separately.
        if game_id in out:
            raise NFLForwardShadowError(f"DUPLICATE_SETTLED_OUTCOME:{game_id}")
        out[game_id] = row
    return out


def grade_forward_predictions(
    predictions: Sequence[ForwardPrediction],
    outcome_rows: Sequence[Mapping[str, Any]],
) -> list[ForwardGrade]:
    outcomes = _outcome_index(outcome_rows)
    grades: list[ForwardGrade] = []
    for prediction in predictions:
        outcome = outcomes.get(prediction.official_event_id)
        if outcome is None:
            continue
        # Use the same disposition helper as reconciliation so a malformed
        # score, contradictory tie flag, or winner-flag mismatch can never
        # enter the binary cohort or be mislabeled as a validated tie.
        if _settlement_disposition(outcome) != "BINARY":
            continue
        home_win = outcome["home_win"]
        selected_home = prediction.selected_participant == prediction.home_team
        won = home_win if selected_home else not home_win
        y = int(won)
        p = prediction.calibrated_probability
        grades.append(
            ForwardGrade(
                event_prediction_id=prediction.event_prediction_id,
                official_event_id=prediction.official_event_id,
                event_start_time_utc=prediction.event_start_time_utc.isoformat(),
                prediction_created_at=prediction.prediction_created_at.isoformat(),
                model_version=prediction.model_version,
                selected_participant=prediction.selected_participant,
                calibrated_probability=p,
                calibrated_lower_bound=prediction.calibrated_lower_bound,
                calibrated_upper_bound=prediction.calibrated_upper_bound,
                outcome=y,
                brier=float((p - y) ** 2),
                log_loss=_safe_log_loss(p, y),
                source_game_id=str(outcome.get("game_id") or prediction.official_event_id),
            )
        )
    return grades


def reconcile_forward_cohort(
    predictions: Sequence[ForwardPrediction],
    outcome_rows: Sequence[Mapping[str, Any]],
    grades: Sequence[ForwardGrade],
) -> dict[str, Any]:
    """Account for every canonical forward event exactly once.

    This is an audit receipt, not a grading input. It never admits a tied or
    unsettled event into the binary cohort and never changes a probability.
    Each canonical event lands in exactly one bucket:

    * ``GRADED`` -- a verified binary grade matches the canonical prediction;
    * ``OUTCOME_TIE`` -- a validated settled tie, excluded from binary scoring;
    * ``OUTCOME_PENDING`` -- no outcome row has been ingested yet;
    * ``OUTCOME_INCOMPLETE`` -- an outcome row exists but its scores or flags
      are missing or contradictory.

    A grade is accepted only when its event, prediction UUID, model version,
    and selected participant match the canonical prediction. Any duplicate,
    orphan, non-binary, or otherwise unaccounted combination fails closed.
    """
    outcomes = _outcome_index(outcome_rows)
    prediction_by_event: dict[str, ForwardPrediction] = {}
    for prediction in predictions:
        event_id = prediction.official_event_id
        if event_id in prediction_by_event:
            raise NFLForwardShadowError("RECONCILIATION_DUPLICATE_EVENT")
        prediction_by_event[event_id] = prediction

    # Detect duplicate and orphan grades before validating settlement state so
    # the failure code identifies the structural defect first.
    grade_events = [grade.official_event_id for grade in grades]
    if len(set(grade_events)) != len(grade_events):
        raise NFLForwardShadowError("RECONCILIATION_DUPLICATE_GRADE")
    for grade in grades:
        if grade.official_event_id not in prediction_by_event:
            raise NFLForwardShadowError(
                "RECONCILIATION_GRADE_WITHOUT_PREDICTION:"
                + grade.official_event_id
            )

    grade_by_event: dict[str, ForwardGrade] = {}
    for grade in grades:
        event_id = grade.official_event_id
        prediction = prediction_by_event[event_id]
        if grade.event_prediction_id != prediction.event_prediction_id:
            raise NFLForwardShadowError(
                f"RECONCILIATION_GRADE_PREDICTION_MISMATCH:{event_id}"
            )
        if grade.model_version != prediction.model_version:
            raise NFLForwardShadowError(
                f"RECONCILIATION_GRADE_MODEL_MISMATCH:{event_id}"
            )
        if grade.selected_participant != prediction.selected_participant:
            raise NFLForwardShadowError(
                f"RECONCILIATION_GRADE_SELECTION_MISMATCH:{event_id}"
            )
        if _settlement_disposition(outcomes.get(event_id)) != "BINARY":
            raise NFLForwardShadowError(
                f"RECONCILIATION_GRADE_OUTCOME_MISMATCH:{event_id}"
            )
        grade_by_event[event_id] = grade

    buckets: dict[str, list[str]] = {
        "GRADED": [],
        "OUTCOME_TIE": [],
        "OUTCOME_PENDING": [],
        "OUTCOME_INCOMPLETE": [],
    }
    for event_id in prediction_by_event:
        disposition = _settlement_disposition(outcomes.get(event_id))
        if disposition == "ABSENT":
            buckets["OUTCOME_PENDING"].append(event_id)
        elif disposition == "TIE":
            buckets["OUTCOME_TIE"].append(event_id)
        elif disposition == "INCOMPLETE":
            buckets["OUTCOME_INCOMPLETE"].append(event_id)
        elif disposition == "BINARY" and event_id in grade_by_event:
            buckets["GRADED"].append(event_id)
        else:
            raise NFLForwardShadowError(
                f"RECONCILIATION_UNACCOUNTED_EVENT:{event_id}"
            )

    unaccounted_grades = set(grade_by_event) - set(prediction_by_event)
    if unaccounted_grades:
        raise NFLForwardShadowError(
            "RECONCILIATION_GRADE_WITHOUT_PREDICTION:"
            + ",".join(sorted(unaccounted_grades))
        )
    if len(buckets["GRADED"]) != len(grade_by_event):
        raise NFLForwardShadowError("RECONCILIATION_GRADE_COUNT_MISMATCH")

    total = sum(len(events) for events in buckets.values())
    if total != len(prediction_by_event):
        raise NFLForwardShadowError("RECONCILIATION_NOT_BALANCED")

    return {
        "schema_version": RECONCILIATION_SCHEMA_VERSION,
        "canonical_events": len(prediction_by_event),
        "graded": len(buckets["GRADED"]),
        "outcome_tie_excluded": len(buckets["OUTCOME_TIE"]),
        "outcome_pending": len(buckets["OUTCOME_PENDING"]),
        "outcome_incomplete": len(buckets["OUTCOME_INCOMPLETE"]),
        "ungraded_events": [
            {"official_event_id": event_id, "reason": reason}
            for reason in (
                "OUTCOME_TIE",
                "OUTCOME_PENDING",
                "OUTCOME_INCOMPLETE",
            )
            for event_id in buckets[reason]
        ],
        "balanced": True,
        "can_execute": False,
    }


def _ece(grades: Sequence[ForwardGrade], bins: int = 10) -> float:
    if not grades:
        return float("nan")
    total = len(grades)
    error = 0.0
    for index in range(bins):
        lo = index / bins
        hi = (index + 1) / bins
        bucket = [
            row for row in grades
            if (lo <= row.calibrated_probability < hi)
            or (index == bins - 1 and row.calibrated_probability == 1.0)
        ]
        if not bucket:
            continue
        mean_p = sum(row.calibrated_probability for row in bucket) / len(bucket)
        hit = sum(row.outcome for row in bucket) / len(bucket)
        error += (len(bucket) / total) * abs(mean_p - hit)
    return float(error)


def calibration_health(
    grades: Sequence[ForwardGrade],
    *,
    min_forward: int = MIN_FORWARD_GRADED,
) -> dict[str, Any]:
    n = len(grades)
    base = {
        "schema_version": HEALTH_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sport": "NFL",
        "market_family": "OUTRIGHT_WINNER",
        "graded_n": n,
        "minimum_forward_required": int(min_forward),
        "can_execute": False,
    }
    if n == 0:
        return {
            **base,
            "status": "INSUFFICIENT_FORWARD_EVIDENCE",
            "certification_recommendation": "DO_NOT_CERTIFY_YET",
            "blockers": [f"FORWARD_GRADED_N_0_LT_{int(min_forward)}"],
        }

    mean_p = sum(row.calibrated_probability for row in grades) / n
    hit_rate = sum(row.outcome for row in grades) / n
    brier = sum(row.brier for row in grades) / n
    log_loss = sum(row.log_loss for row in grades) / n
    ece = _ece(grades)
    bias = mean_p - hit_rate
    mean_lower = sum(row.calibrated_lower_bound for row in grades) / n
    lower_gap = hit_rate - mean_lower

    metrics = {
        "brier": float(brier),
        "log_loss": float(log_loss),
        "ece": float(ece),
        "calibration_bias": float(bias),
        "mean_predicted_probability": float(mean_p),
        "observed_hit_rate": float(hit_rate),
        "mean_calibrated_lower_bound": float(mean_lower),
        "lower_bound_reliability_gap": float(lower_gap),
    }
    if n < int(min_forward):
        return {
            **base,
            **metrics,
            "status": "INSUFFICIENT_FORWARD_EVIDENCE",
            "certification_recommendation": "DO_NOT_CERTIFY_YET",
            "blockers": [f"FORWARD_GRADED_N_{n}_LT_{int(min_forward)}"],
        }

    blockers: list[str] = []
    if not brier < 0.25:
        blockers.append("FORWARD_BRIER_NOT_BELOW_0_25")
    if not log_loss < math.log(2):
        blockers.append("FORWARD_LOG_LOSS_NOT_BELOW_LOG2")
    if not ece <= 0.12:
        blockers.append("FORWARD_ECE_ABOVE_0_12")
    if not abs(bias) <= 0.08:
        blockers.append("FORWARD_CALIBRATION_BIAS_ABOVE_0_08")
    # A calibrated lower bound should not systematically exceed the realized
    # hit rate. A small prospective tolerance avoids treating sampling noise as
    # a structural lower-bound failure.
    if lower_gap < -0.03:
        blockers.append("FORWARD_LOWER_BOUND_RELIABILITY_FAILED")

    return {
        **base,
        **metrics,
        "status": "PASS" if not blockers else "FAIL",
        "certification_recommendation": (
            "FORWARD_EVIDENCE_PASS" if not blockers else "DO_NOT_CERTIFY_YET"
        ),
        "blockers": blockers,
    }


# Each supported source needs a stable, unique database ordering key. PostgREST
# ranges without an explicit order are not deterministic once the forward
# cohort exceeds a page, which can silently change the grading denominator.
_FORWARD_PAGE_KEYS = {
    PREDICTION_TABLE: "event_prediction_id",
    OUTCOME_TABLE: "game_id",
    GRADE_TABLE: "grade_id",
}


# A broken/hostile PostgREST client could otherwise return endlessly full pages.
# The production default allows 128,000 source rows, well above the normal NFL
# cohort, but never loops without a finite bound.
MAX_FORWARD_PAGES = 128


def _paginate(
    db: Any,
    table: str,
    columns: str,
    *,
    page_size: int = 1000,
    max_pages: int = MAX_FORWARD_PAGES,
) -> list[dict[str, Any]]:
    """Read an entire supported source with deterministic ordered bounded pages.

    Page sizes and limits must be exact positive Python integers (not bool,
    float or string). An empty one-row probe after a full final page proves an
    exact-length dataset; any extra row fails before grades/health are stored.
    """
    if (
        table not in _FORWARD_PAGE_KEYS
        or type(page_size) is not int or page_size <= 0
        or type(max_pages) is not int or max_pages <= 0
    ):
        raise NFLForwardShadowError("NFL_FORWARD_PAGINATION_INVALID:TABLE_OR_PAGE_SIZE")
    key = _FORWARD_PAGE_KEYS[table]
    fields = [part.strip() for part in columns.split(",")]
    # Projection names must be plain trusted identifiers. Reject malformed
    # strings rather than composing accidental PostgREST expressions.
    if not fields or any(not field.isidentifier() for field in fields):
        raise NFLForwardShadowError("NFL_FORWARD_PAGINATION_INVALID:PROJECTION_INVALID")
    if key not in fields:
        fields.append(key)
    projection = ",".join(dict.fromkeys(fields))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    start = 0
    for _ in range(max_pages):
        result = (
            db.table(table)
            .select(projection)
            .order(key)
            .range(start, start + page_size - 1)
            .execute()
        )
        batch = list(result.data or [])
        if len(batch) > page_size:
            raise NFLForwardShadowError(f"NFL_FORWARD_PAGINATION_INVALID:{table}:PAGE_LENGTH_INVALID")
        for row in batch:
            if not isinstance(row, Mapping):
                raise NFLForwardShadowError(f"NFL_FORWARD_PAGINATION_INVALID:{table}:ID_MISSING")
            raw_id = row.get(key)
            if raw_id is None or raw_id == "":
                raise NFLForwardShadowError(f"NFL_FORWARD_PAGINATION_INVALID:{table}:ID_MISSING")
            # PostgREST emits text/UUID primary keys as JSON strings. Do not
            # stringify integers/bools: mixing ID types can change dedup.
            if not isinstance(raw_id, str):
                raise NFLForwardShadowError(f"NFL_FORWARD_PAGINATION_INVALID:{table}:ID_TYPE_INVALID")
            if not raw_id.strip():
                raise NFLForwardShadowError(f"NFL_FORWARD_PAGINATION_INVALID:{table}:ID_MISSING")
            if raw_id in seen:
                raise NFLForwardShadowError(f"NFL_FORWARD_PAGINATION_INVALID:{table}:ID_DUPLICATED")
            seen.add(raw_id)
            rows.append(dict(row))
        if len(batch) < page_size:
            return rows
        start += page_size
    # A full final page can be the exact dataset boundary. Probe ONE additional
    # ordered primary-key row, without permitting another data page. This
    # distinguishes an exact-size valid corpus from truncation, while retaining
    # the hard query/row bound and a fail-closed response for over-limit sources.
    probe = (
        db.table(table)
        .select(key)
        .order(key)
        .range(start, start)
        .execute()
    )
    probe_rows = list(probe.data or [])
    if len(probe_rows) == 0:
        return rows
    raise NFLForwardShadowError(f"NFL_FORWARD_PAGINATION_INVALID:{table}:PAGE_LIMIT_EXCEEDED")


def load_prediction_rows(db: Any) -> list[dict[str, Any]]:
    return _paginate(
        db,
        PREDICTION_TABLE,
        "event_prediction_id,official_event_id,event_start_time_utc,created_at,model_version,home_team,away_team,selected_participant,calibrated_selection_probability,calibrated_selection_lower_bound,calibrated_selection_upper_bound,model_package_valid,provenance_complete,model_probability_publishable,can_execute",
    )


def load_outcome_rows(db: Any) -> list[dict[str, Any]]:
    return _paginate(
        db,
        OUTCOME_TABLE,
        "game_id,home_score,away_score,home_win,tie,locked_at,can_execute",
    )


def persist_new_grades(db: Any, grades: Sequence[ForwardGrade]) -> int:
    if not grades:
        return 0
    existing = _paginate(db, GRADE_TABLE, "official_event_id")
    seen = {str(row.get("official_event_id") or "") for row in existing}
    payloads = [asdict(row) for row in grades if row.official_event_id not in seen]
    for offset in range(0, len(payloads), 250):
        db.table(GRADE_TABLE).insert(payloads[offset:offset + 250]).execute()
    return len(payloads)


def persist_health(db: Any, health: Mapping[str, Any]) -> None:
    db.table(HEALTH_TABLE).insert({
        "schema_version": health["schema_version"],
        "generated_at": health["generated_at"],
        "graded_n": health["graded_n"],
        "minimum_forward_required": health["minimum_forward_required"],
        "brier": health.get("brier"),
        "log_loss": health.get("log_loss"),
        "ece": health.get("ece"),
        "calibration_bias": health.get("calibration_bias"),
        "mean_predicted_probability": health.get("mean_predicted_probability"),
        "observed_hit_rate": health.get("observed_hit_rate"),
        "mean_calibrated_lower_bound": health.get("mean_calibrated_lower_bound"),
        "lower_bound_reliability_gap": health.get("lower_bound_reliability_gap"),
        "status": health["status"],
        "certification_recommendation": health["certification_recommendation"],
        "blockers": list(health.get("blockers") or []),
        "can_execute": False,
    }).execute()


def run_forward_shadow(
    db: Any,
    *,
    min_forward: int = MIN_FORWARD_GRADED,
    settlement_refresh_fn: Any = refresh_recent_settled_outcomes,
    feature_refresh_fn: Any = None,
) -> dict[str, Any]:
    # Forward grading is only meaningful when its settled-outcome producer is
    # at least as fresh as the prediction cohort.  Refresh the narrow schedules
    # source first; if acquisition/provenance fails, fail the run instead of
    # silently recording another zero-grade "success".
    settlement_refresh = settlement_refresh_fn(db)
    prediction_rows = load_prediction_rows(db)
    canonical = select_canonical_forward_predictions(prediction_rows)
    outcome_rows = load_outcome_rows(db)
    grades = grade_forward_predictions(canonical, outcome_rows)
    reconciliation = reconcile_forward_cohort(canonical, outcome_rows, grades)
    if reconciliation["graded"] != len(grades):
        raise NFLForwardShadowError("RECONCILIATION_GRADE_COUNT_MISMATCH")
    inserted = persist_new_grades(db, grades)
    health = calibration_health(grades, min_forward=min_forward)
    # The production health contract always reports graded_n; only assert the
    # denominator when a test double has not replaced that mapping wholesale.
    if "graded_n" in health and health["graded_n"] != reconciliation["graded"]:
        raise NFLForwardShadowError("RECONCILIATION_HEALTH_COUNT_MISMATCH")
    persist_health(db, health)
    feature_refresh = _refresh_feature_table(db, settlement_refresh, feature_refresh_fn)
    return {
        "status": "COMPLETED",
        "settlement_refresh": settlement_refresh,
        "feature_table_refresh": feature_refresh,
        "prediction_rows_seen": len(prediction_rows),
        "canonical_events": len(canonical),
        "graded_events": len(grades),
        "new_grades_persisted": inserted,
        "cohort_reconciliation": reconciliation,
        "calibration_health": health,
        "automatic_certification": False,
        "automatic_promotion": False,
        "can_execute": False,
    }


def active_nfl_season(now: Any = None) -> int:
    """NFL season label: January/February belong to the prior year's season."""
    from datetime import datetime, timezone

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return current.year - 1 if current.month <= 2 else current.year


def _refresh_feature_table(db: Any, settlement_refresh: Mapping[str, Any], fn: Any) -> dict[str, Any]:
    """Keep the training/replay feature table current. Never fails grading."""
    refreshed = {int(s) for s in (settlement_refresh or {}).get("seasons") or []}
    active = active_nfl_season()
    if active not in refreshed:
        return {"status": "SKIPPED", "reason_code": "ACTIVE_SEASON_NOT_REFRESHED", "can_execute": False}
    try:
        if fn is None:
            from v17.nfl_feature_table_refresh import refresh_feature_rows as fn
        # Active season only: re-downloading a finished season's PBP daily
        # would add cost without changing any row.
        return fn(db, seasons=[active])
    except Exception as exc:  # noqa: BLE001 - typed, isolated from grading
        return {"status": "FAILED", "reason_code": f"NFL_FEATURE_TABLE_REFRESH_{type(exc).__name__.upper()}", "can_execute": False}


def _client() -> Any:
    from supabase import create_client

    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
    if not key:
        raise RuntimeError("SUPABASE service credential unavailable")
    return create_client(os.environ["SUPABASE_URL"], key)


def main() -> None:
    print(json.dumps(run_forward_shadow(_client()), sort_keys=True, default=str))


if __name__ == "__main__":
    main()
