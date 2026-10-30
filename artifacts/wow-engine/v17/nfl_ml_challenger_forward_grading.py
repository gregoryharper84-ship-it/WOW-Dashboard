"""Settlement grading for the research-only NFL ML stationary challenger.

The challenger ledger is intentionally separate from champion forward grades.
This module may fill only settlement/grade fields on immutable pregame challenger
rows. It never changes a probability, lower bound, model identity, publication
state, promotion authority, or execution authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
CHALLENGER_TABLE = "wow_nfl_ml_challenger_forward_shadow"
OUTCOME_TABLE = "wow_nfl_training_games"
MIN_FORWARD_GRADED = 100


class NFLChallengerForwardGradeError(ValueError):
    pass


def _probability(value: Any) -> float:
    if isinstance(value, bool):
        raise NFLChallengerForwardGradeError("NFL_CHALLENGER_PROBABILITY_INVALID")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise NFLChallengerForwardGradeError("NFL_CHALLENGER_PROBABILITY_INVALID") from exc
    if not math.isfinite(parsed) or not 0.0 < parsed < 1.0:
        raise NFLChallengerForwardGradeError("NFL_CHALLENGER_PROBABILITY_INVALID")
    return parsed


def _safe_log_loss(probability: float, outcome: int) -> float:
    p = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    y = int(outcome)
    return float(-(y * math.log(p) + (1 - y) * math.log(1.0 - p)))


def _outcome_index(rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    index: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        game_id = str(row.get("game_id") or "").strip()
        if not game_id or row.get("home_score") is None or row.get("away_score") is None:
            continue
        if game_id in index:
            raise NFLChallengerForwardGradeError(f"DUPLICATE_SETTLED_OUTCOME:{game_id}")
        index[game_id] = row
    return index


def gradeable_updates(
    shadow_rows: Sequence[Mapping[str, Any]],
    outcome_rows: Sequence[Mapping[str, Any]],
    *,
    graded_at: str | None = None,
) -> list[dict[str, Any]]:
    """Build idempotent grade updates from exact official-event settlement only."""
    outcomes = _outcome_index(outcome_rows)
    grade_time = graded_at or datetime.now(timezone.utc).isoformat()
    updates: list[dict[str, Any]] = []

    for row in shadow_rows:
        if str(row.get("lifecycle_state") or "") != "FORWARD_SHADOW":
            continue
        if row.get("probability_publishable") is not False:
            raise NFLChallengerForwardGradeError("NFL_CHALLENGER_PUBLICATION_STATE_INVALID")
        if row.get("promotion_authorized") is not False:
            raise NFLChallengerForwardGradeError("NFL_CHALLENGER_PROMOTION_STATE_INVALID")
        if row.get("can_execute") is not False:
            raise NFLChallengerForwardGradeError("CAN_EXECUTE_MUST_BE_FALSE")

        event_id = str(row.get("official_event_id") or "").strip()
        shadow_id = str(row.get("shadow_id") or "").strip()
        if not event_id or not shadow_id:
            raise NFLChallengerForwardGradeError("NFL_CHALLENGER_FORWARD_IDENTITY_MISSING")

        outcome = outcomes.get(event_id)
        if outcome is None or outcome.get("home_win") is None:
            continue
        if outcome.get("tie") is True:
            # A standard moneyline tie is not a binary win/loss observation.
            continue

        home = str(row.get("home_team") or "").strip()
        away = str(row.get("away_team") or "").strip()
        selected = str(row.get("selected_participant") or "").strip()
        if selected not in {home, away}:
            raise NFLChallengerForwardGradeError(
                f"NFL_CHALLENGER_SELECTION_IDENTITY_INVALID:{event_id}"
            )

        selected_home = selected == home
        won = bool(outcome.get("home_win")) if selected_home else not bool(outcome.get("home_win"))
        y = int(won)
        p = _probability(row.get("calibrated_probability"))
        updates.append(
            {
                "shadow_id": shadow_id,
                "official_event_id": event_id,
                "lifecycle_state": "GRADED",
                "outcome": y,
                "graded_at": grade_time,
                "brier": float((p - y) ** 2),
                "log_loss": _safe_log_loss(p, y),
                "hit": bool(y),
                # Re-state the immutable safety fields on update so any drift
                # fails at the database constraints rather than being hidden.
                "probability_publishable": False,
                "promotion_authorized": False,
                "can_execute": False,
                "terminal_authority": "V17_TERMINAL_REDUCER",
            }
        )
    return updates


def _paginate(db: Any, table: str, columns: str, *, page_size: int = 1000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        result = db.table(table).select(columns).range(start, start + page_size - 1).execute()
        batch = list(result.data or [])
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def load_shadow_rows(db: Any) -> list[dict[str, Any]]:
    return _paginate(
        db,
        CHALLENGER_TABLE,
        "shadow_id,challenger_id,official_event_id,home_team,away_team,selected_participant,"
        "calibrated_probability,lifecycle_state,probability_publishable,promotion_authorized,"
        "can_execute,terminal_authority",
    )


def load_outcome_rows(db: Any) -> list[dict[str, Any]]:
    return _paginate(
        db,
        OUTCOME_TABLE,
        "game_id,home_score,away_score,home_win,tie,locked_at,can_execute",
    )


def _persist_updates(db: Any, updates: Sequence[Mapping[str, Any]]) -> int:
    written = 0
    for update in updates:
        shadow_id = str(update["shadow_id"])
        payload = {key: value for key, value in update.items() if key not in {"shadow_id", "official_event_id"}}
        result = (
            db.table(CHALLENGER_TABLE)
            .update(payload)
            .eq("shadow_id", shadow_id)
            .eq("lifecycle_state", "FORWARD_SHADOW")
            .execute()
        )
        written += len(list(getattr(result, "data", None) or []))
    return written


def challenger_grade_metrics(shadow_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    graded = [row for row in shadow_rows if str(row.get("lifecycle_state") or "") == "GRADED"]
    n = len(graded)
    base = {
        "graded_n": n,
        "minimum_forward_required": MIN_FORWARD_GRADED,
        "promotion_authorized": False,
        "probability_publishable": False,
        "can_execute": False,
    }
    if not graded:
        return {
            **base,
            "status": "INSUFFICIENT_FORWARD_EVIDENCE",
            "blockers": [f"CHALLENGER_FORWARD_GRADED_N_0_LT_{MIN_FORWARD_GRADED}"],
        }

    brier = sum(float(row["brier"]) for row in graded) / n
    log_loss = sum(float(row["log_loss"]) for row in graded) / n
    hit_rate = sum(1 for row in graded if row.get("hit") is True) / n
    blockers = [] if n >= MIN_FORWARD_GRADED else [
        f"CHALLENGER_FORWARD_GRADED_N_{n}_LT_{MIN_FORWARD_GRADED}"
    ]
    return {
        **base,
        "status": "REVIEW_READY" if not blockers else "INSUFFICIENT_FORWARD_EVIDENCE",
        "brier": float(brier),
        "log_loss": float(log_loss),
        "observed_hit_rate": float(hit_rate),
        "blockers": blockers,
    }


def run_challenger_forward_grading(db: Any) -> dict[str, Any]:
    before = load_shadow_rows(db)
    updates = gradeable_updates(before, load_outcome_rows(db))
    written = _persist_updates(db, updates)
    after = load_shadow_rows(db)
    return {
        "status": "COMPLETED",
        "challenger_rows_seen": len(before),
        "grade_updates_eligible": len(updates),
        "grade_updates_persisted": written,
        "metrics": challenger_grade_metrics(after),
        "automatic_certification": False,
        "automatic_promotion": False,
        "probability_publishable": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "CHALLENGER_TABLE",
    "MIN_FORWARD_GRADED",
    "NFLChallengerForwardGradeError",
    "challenger_grade_metrics",
    "gradeable_updates",
    "run_challenger_forward_grading",
]
