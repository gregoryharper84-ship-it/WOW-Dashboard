"""Research-only, complete-week accuracy audit for governed NFL ML -> Pick'em.

Reads immutable pregame Pick'em outputs and independent final settlements.
Never fits probabilities, changes selections, certifies a challenger or executes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isclose, isfinite, log
from typing import Any, Mapping, Sequence

from nfl_event_model_contract import CONTROLLING_SPECIALIST

SERVING_MODE = "ACCURACY_AUDIT_ONLY"
AUDIT_STATUS = "GOVERNED_NFL_PICKEM_WEEK_AUDITED"
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
TARGET_ACCURACY = 0.90  # operator's aspiration, not a fitted model threshold
CAN_EXECUTE = False

_PROBABILITY_BEARING = frozenset({
    "MODEL_QUALIFIED_HOLD", "MARKET_VERIFIED_HOLD",
    "MONEY_QUALIFIED", "FINAL_APPROVED",
})


class AccuracyAuditError(ValueError):
    """Typed, fail-closed audit evidence failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AccuracyAuditError(code)
    return value.strip()


def _utc(value: Any, code: str) -> datetime:
    v = _text(value, code)
    try:
        result = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AccuracyAuditError(code) from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise AccuracyAuditError(code)
    return result.astimezone(timezone.utc)


def _prob(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AccuracyAuditError("PICKEM_ACCURACY_PROBABILITY_INVALID")
    p = float(value)
    if not isfinite(p) or not 0.0 <= p <= 1.0:
        raise AccuracyAuditError("PICKEM_ACCURACY_PROBABILITY_INVALID")
    return p


def _event_map(items: Any, *, name: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(items, (list, tuple)) or not items:
        raise AccuracyAuditError(f"PICKEM_ACCURACY_{name}_REQUIRED")
    result: dict[str, Mapping[str, Any]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            raise AccuracyAuditError(f"PICKEM_ACCURACY_{name}_ROW_INVALID")
        key = _text(item.get("official_event_id"), f"PICKEM_ACCURACY_{name}_EVENT_ID_REQUIRED")
        if key in result:
            raise AccuracyAuditError(f"PICKEM_ACCURACY_{name}_DUPLICATE_EVENT")
        result[key] = item
    return result


def audit_governed_pickem_week(
    *,
    season: int,
    week: int,
    expected_game_count: int,
    manifest_receipt_id: str,
    manifest_frozen_at: str,
    manifest: Sequence[Mapping[str, Any]],
    picks: Sequence[Mapping[str, Any]],
    settlements: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Audit *all* games without choosing only high-confidence correct-looking rows.

    A game cannot be counted unless the fitted NFL specialist produced a complete
    Pick'em probability receipt before kickoff and the independently sourced
    settlement has a unique final winner. Missing evidence blocks the entire week.
    """
    if (type(season) is not int or not 2020 <= season <= 2100 or
        type(week) is not int or not 1 <= week <= 22):
        raise AccuracyAuditError("PICKEM_ACCURACY_SEASON_WEEK_INVALID")
    if type(expected_game_count) is not int or not 1 <= expected_game_count <= 20:
        raise AccuracyAuditError("PICKEM_ACCURACY_EXPECTED_COUNT_INVALID")
    _text(manifest_receipt_id, "PICKEM_ACCURACY_MANIFEST_RECEIPT_MISSING")
    frozen_at = _utc(manifest_frozen_at, "PICKEM_ACCURACY_MANIFEST_TIME_INVALID")

    games = _event_map(manifest, name="MANIFEST")
    governed = _event_map(picks, name="PICKS")
    settled = _event_map(settlements, name="SETTLEMENTS")
    if len(games) != expected_game_count:
        raise AccuracyAuditError("PICKEM_ACCURACY_SOURCE_GAME_COUNT_MISMATCH")
    if games.keys() != governed.keys():
        raise AccuracyAuditError("PICKEM_ACCURACY_PICK_EVENT_SET_MISMATCH")
    if games.keys() != settled.keys():
        raise AccuracyAuditError("PICKEM_ACCURACY_SETTLEMENT_EVENT_SET_MISMATCH")

    correct = 0
    expected_correct = 0.0
    selected_probabilities: list[float] = []
    brier_sum = 0.0
    logloss_sum = 0.0
    per_game: list[dict[str, Any]] = []
    receipts: set[str] = set()
    settlement_ids: set[str] = set()
    for event_id in sorted(games):
        game, pick, result = games[event_id], governed[event_id], settled[event_id]
        home = _text(game.get("home_team"), "PICKEM_ACCURACY_HOME_MISSING")
        away = _text(game.get("away_team"), "PICKEM_ACCURACY_AWAY_MISSING")
        if home == away:
            raise AccuracyAuditError("PICKEM_ACCURACY_PARTICIPANTS_INVALID")
        if game.get("season") != season or game.get("week") != week:
            raise AccuracyAuditError("PICKEM_ACCURACY_MANIFEST_SEASON_WEEK_MISMATCH")
        kickoff = _utc(game.get("event_start_time_utc"), "PICKEM_ACCURACY_KICKOFF_INVALID")
        if frozen_at > kickoff:
            raise AccuracyAuditError("PICKEM_ACCURACY_MANIFEST_FROZEN_AFTER_KICKOFF")
        if (pick.get("home_team") != home or pick.get("away_team") != away or
            pick.get("status") != "PICKEM_READY"):
            raise AccuracyAuditError("PICKEM_ACCURACY_INVALID_PICK_IDENTITY_OR_STATUS")
        if pick.get("controlling_specialist") != CONTROLLING_SPECIALIST:
            raise AccuracyAuditError("PICKEM_ACCURACY_SPECIALIST_MISMATCH")
        if pick.get("source_terminal_label") not in _PROBABILITY_BEARING:
            raise AccuracyAuditError("PICKEM_ACCURACY_SOURCE_TERMINAL_NOT_PROBABILITY_BEARING")
        if pick.get("source_terminal_upgraded") is not False:
            raise AccuracyAuditError("PICKEM_ACCURACY_SOURCE_TERMINAL_UPGRADED")
        if pick.get("can_execute") is not False:
            raise AccuracyAuditError("PICKEM_ACCURACY_EXECUTION_CONTRACT_INVALID")
        receipt = _text(pick.get("source_prediction_id"), "PICKEM_ACCURACY_PREDICTION_RECEIPT_MISSING")
        if receipt in receipts:
            raise AccuracyAuditError("PICKEM_ACCURACY_DUPLICATE_PREDICTION_RECEIPT")
        receipts.add(receipt)
        _text(pick.get("source_snapshot_id"), "PICKEM_ACCURACY_SOURCE_SNAPSHOT_MISSING")
        prediction_time = _utc(
            pick.get("immutable_model_timestamp"), "PICKEM_ACCURACY_PREDICTION_TIME_INVALID"
        )
        if prediction_time >= kickoff:
            raise AccuracyAuditError("PICKEM_ACCURACY_PREDICTION_NOT_PREGAME")
        home_p = _prob(pick.get("home_probability"))
        away_p = _prob(pick.get("away_probability"))
        selected_p = _prob(pick.get("selected_probability"))
        if not isclose(home_p + away_p, 1.0, rel_tol=0.0, abs_tol=1e-9):
            raise AccuracyAuditError("PICKEM_ACCURACY_TWO_SIDED_PROBABILITY_NOT_NORMALIZED")
        selection = _text(pick.get("pool_pick"), "PICKEM_ACCURACY_SELECTION_MISSING")
        if selection not in {home, away}:
            raise AccuracyAuditError("PICKEM_ACCURACY_SELECTION_IDENTITY_INVALID")
        selected_expected = home_p if selection == home else away_p
        if (not isclose(selected_p, selected_expected, rel_tol=0.0, abs_tol=1e-9) or
            selected_p < max(home_p, away_p) - 1e-9):
            raise AccuracyAuditError("PICKEM_ACCURACY_SELECTION_NOT_GOVERNED_MAX")
        _text(result.get("settlement_source"), "PICKEM_ACCURACY_SETTLEMENT_SOURCE_MISSING")
        settlement_id = _text(
            result.get("settlement_receipt_id"), "PICKEM_ACCURACY_SETTLEMENT_RECEIPT_MISSING"
        )
        if settlement_id in settlement_ids:
            raise AccuracyAuditError("PICKEM_ACCURACY_DUPLICATE_SETTLEMENT_RECEIPT")
        settlement_ids.add(settlement_id)
        settlement_time = _utc(
            result.get("settled_at"), "PICKEM_ACCURACY_SETTLEMENT_TIME_INVALID"
        )
        if settlement_time <= kickoff:
            raise AccuracyAuditError("PICKEM_ACCURACY_PREMATURE_SETTLEMENT")
        winner = _text(result.get("winner"), "PICKEM_ACCURACY_WINNER_MISSING")
        if winner not in {home, away}:
            raise AccuracyAuditError("PICKEM_ACCURACY_WINNER_INVALID_OR_TIE_NEEDS_RULE")
        hit = selection == winner
        correct += int(hit)
        y_home = float(winner == home)
        brier_sum += (home_p - y_home) ** 2
        logloss_sum += -(log(max(home_p, 1e-15)) if y_home else log(max(away_p, 1e-15)))
        per_game.append({
            "official_event_id": event_id,
            "selected_participant": selection,
            "winner": winner,
            "correct": hit,
            "selected_probability": selected_p,
            "source_prediction_id": receipt,
            "source_terminal_label": pick["source_terminal_label"],
            "settlement_receipt_id": settlement_id,
            "controlling_specialist": CONTROLLING_SPECIALIST,
        })
    accuracy = correct / expected_game_count
    return {
        "status": AUDIT_STATUS,
        "serving_mode": SERVING_MODE,
        "season": season, "week": week,
        "manifest_receipt_id": manifest_receipt_id,
        "event_count": expected_game_count,
        "scored_count": expected_game_count,
        "unreconciled_count": 0,
        "correct": correct,
        "incorrect": expected_game_count - correct,
        "accuracy": accuracy,
        "target_accuracy": TARGET_ACCURACY,
        "operator_target_met": accuracy >= TARGET_ACCURACY,
        "brier_home": brier_sum / expected_game_count,
        "log_loss": logloss_sum / expected_game_count,
        "games": per_game,
        "production_probability_changed": False,
        "production_pick_changed": False,
        "automatic_promotion": False,
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
    }


__all__ = [
    "AUDIT_STATUS", "AccuracyAuditError", "CAN_EXECUTE", "SERVING_MODE",
    "TARGET_ACCURACY", "audit_governed_pickem_week",
]
