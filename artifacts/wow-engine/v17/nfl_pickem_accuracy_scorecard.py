"""Read-only, complete-slate NFL Pick'em accuracy/reliability calculator.

Consumes immutable fitted NFL specialist receipts and independently settled
results. Never runs a scorer, changes production picks, or publishes terminal
authority. A blocked row remains in the scheduled-game denominator.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isclose, isfinite, log
from typing import Any, Mapping, Sequence

from nfl_event_model_contract import CONTROLLING_SPECIALIST

CAN_EXECUTE = False
OBJECTIVE = "MAX_EXPECTED_CORRECT"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
CONTRACT = "NFL_PICKEM_COMPLETE_WEEK_SCORECARD_V1"


def _utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("TIMESTAMP_REQUIRED")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("TIMESTAMP_INVALID") from exc
    if result.tzinfo is None:
        raise ValueError("TIMESTAMP_TIMEZONE_REQUIRED")
    return result.astimezone(timezone.utc)


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("PROBABILITY_INVALID")
    n = float(value)
    if not isfinite(n) or not 0 < n < 1:
        raise ValueError("PROBABILITY_INVALID")
    return n


def _index(rows: Sequence[Mapping[str, Any]], label: str) -> dict[str, Mapping[str, Any]]:
    found: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        event_id = str(row.get("official_event_id") or "").strip()
        if not event_id:
            raise ValueError(label + "_EVENT_ID_MISSING")
        if event_id in found:
            raise ValueError(label + "_DUPLICATE_EVENT")
        found[event_id] = row
    return found


def poisson_binomial(picks: Sequence[float]) -> list[float]:
    """Distribution of correct picks assuming independent game outcomes."""
    dp = [1.0]
    for p in picks:
        p = _number(p)
        next_dp = [0.0] * (len(dp) + 1)
        for correct, mass in enumerate(dp):
            next_dp[correct] += mass * (1 - p)
            next_dp[correct + 1] += mass * p
        dp = next_dp
    return dp


def grade_week(
    scheduled_games: Sequence[Mapping[str, Any]],
    immutable_predictions: Sequence[Mapping[str, Any]],
    independent_settlements: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return complete-game ledger; never guess missing forecasts or results.

    Input contract:
      schedule: official_event_id, home_team, away_team, kickoff_utc, status
      prediction: official_event_id, prediction_id, controlling_specialist,
        model_timestamp, home_probability, away_probability, optional status
      settlement: official_event_id, winner_team, settlement_source,
        settled_at, status='FINAL'
    """
    schedule = _index(scheduled_games, "SCHEDULE")
    predictions = _index(immutable_predictions, "PREDICTION")
    settlements = _index(independent_settlements, "SETTLEMENT")
    if not schedule:
        raise ValueError("SCHEDULE_EMPTY")
    if set(predictions) - set(schedule):
        raise ValueError("PREDICTION_NOT_IN_SCHEDULE")
    if set(settlements) - set(schedule):
        raise ValueError("SETTLEMENT_NOT_IN_SCHEDULE")

    ledger: list[dict[str, Any]] = []
    probs: list[float] = []
    outcomes: list[int] = []
    correct = 0
    brier = 0.0
    logloss = 0.0
    expected_correct = 0.0
    probability_complete = True
    settlement_complete = True
    for event_id, game in schedule.items():
        home, away = str(game.get("home_team") or ""), str(game.get("away_team") or "")
        if not home or not away or home == away:
            raise ValueError("SCHEDULE_PARTICIPANTS_INVALID")
        kickoff = _utc(game.get("kickoff_utc"))
        row: dict[str, Any] = {"official_event_id": event_id, "home_team": home,
                               "away_team": away, "status": "UNRESOLVED"}
        if str(game.get("status") or "SCHEDULED").upper() in {"CANCELED", "CANCELLED", "NO_CONTEST"}:
            row["status"] = "SCHEDULE_CANCELED"
            ledger.append(row)
            continue
        prediction = predictions.get(event_id)
        if prediction is None:
            row["status"] = "PREDICTION_MISSING"
        else:
            try:
                if str(prediction.get("controlling_specialist")) != CONTROLLING_SPECIALIST:
                    raise ValueError("SPECIALIST_IDENTITY_INVALID")
                if not str(prediction.get("prediction_id") or "").strip():
                    raise ValueError("IMMUTABLE_PREDICTION_ID_MISSING")
                if _utc(prediction.get("model_timestamp")) >= kickoff:
                    raise ValueError("PREDICTION_NOT_PREGAME")
                material_at = prediction.get("latest_material_update_at")
                if material_at and _utc(material_at) > _utc(prediction["model_timestamp"]):
                    raise ValueError("PREDICTION_STALE")
                if prediction.get("probability_publishable") is False or prediction.get("model_status") in (
                    "MODEL_SCORER_FAILED", "MODEL_UNAVAILABLE", "MODEL_OUTPUT_INVALID",
                    "MODEL_INPUTS_INSUFFICIENT", "STALE_MODEL_OUTPUT"):
                    raise ValueError("PREDICTION_BLOCKED")
                ph = _number(prediction.get("home_probability"))
                pa = _number(prediction.get("away_probability"))
                if not isclose(ph + pa, 1.0, rel_tol=0, abs_tol=1e-6):
                    raise ValueError("PROBABILITIES_NOT_NORMALIZED")
                selected = home if ph >= pa else away
                p = max(ph, pa)
                row.update({"prediction_id": prediction["prediction_id"],
                            "selected_team": selected, "selected_probability": p,
                            "status": "PREDICTION_LOCKED"})
                probs.append(p)
                expected_correct += p
            except ValueError as exc:
                row["status"] = str(exc)
        if "selected_team" not in row:
            probability_complete = False
        settlement = settlements.get(event_id)
        if settlement is None:
            settlement_complete = False
            row["settlement_status"] = "SETTLEMENT_MISSING"
        elif str(settlement.get("status") or "").upper() != "FINAL":
            settlement_complete = False
            row["settlement_status"] = "SETTLEMENT_NOT_FINAL"
        elif not settlement.get("settlement_source") or not settlement.get("settled_at"):
            settlement_complete = False
            row["settlement_status"] = "SETTLEMENT_PROVENANCE_MISSING"
        elif _utc(settlement["settled_at"]) < kickoff:
            settlement_complete = False
            row["settlement_status"] = "SETTLEMENT_BEFORE_KICKOFF"
        elif settlement.get("winner_team") not in (home, away):
            settlement_complete = False
            row["settlement_status"] = "SETTLEMENT_WINNER_INVALID"
        else:
            row["settlement_status"] = "FINAL_VERIFIED"
            row["actual_winner"] = settlement["winner_team"]
            if "selected_team" in row:
                y = int(row["selected_team"] == settlement["winner_team"])
                row["correct"] = bool(y)
                correct += y
                outcomes.append(y)
                p = row["selected_probability"]
                brier += (p - y) ** 2
                logloss -= y * log(p) + (1 - y) * log(1 - p)
        ledger.append(row)

    eligible = [r for r in ledger if r["status"] != "SCHEDULE_CANCELED"]
    n = len(eligible)
    locked = sum("selected_team" in r for r in eligible)
    graded = len(outcomes)
    all_complete = bool(n and locked == n and graded == n and settlement_complete)
    distribution = poisson_binomial(probs) if locked == n and n else None
    return {
        "contract": CONTRACT, "objective": OBJECTIVE, "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
        "scheduled_count": len(schedule), "eligible_game_count": n,
        "prediction_count": locked, "settled_and_graded_count": graded,
        "prediction_coverage": locked / n if n else None,
        "actual_correct": correct, "actual_accuracy": correct / n if all_complete else None,
        "graded_only_accuracy": correct / graded if graded else None,
        "expected_correct": expected_correct if probability_complete and n else None,
        "expected_accuracy": expected_correct / n if probability_complete and n else None,
        "brier": brier / graded if graded else None,
        "log_loss": logloss / graded if graded else None,
        "correct_count_distribution_independence_assumption": distribution,
        "status": "COMPLETE" if all_complete else "INCOMPLETE",
        "rows": ledger,
    }
