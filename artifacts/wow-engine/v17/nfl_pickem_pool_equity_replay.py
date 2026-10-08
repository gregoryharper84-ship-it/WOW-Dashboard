"""Point-in-time, research-only replay of governed NFL Pick'em pool strategy.

No probability production, pick mutation, automatic strategy promotion or orders.
Ownership snapshots and competitor submissions must predate the league's lock.
Settled outcomes are used only to score already-frozen decisions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from math import isclose, isfinite
from statistics import mean
from typing import Any, Mapping, Sequence

from v17.nfl_pickem_pool_win_equity_shadow import (
    SHADOW_BLOCKED,
    build_pool_win_equity_shadow,
)
from nfl_event_model_contract import CONTROLLING_SPECIALIST

SERVING_MODE = "RESEARCH_REPLAY_ONLY"
REPLAY_BLOCKED = "REPLAY_BLOCKED"
REPLAY_EVIDENCE_INSUFFICIENT = "REPLAY_EVIDENCE_INSUFFICIENT"
REPLAY_RESEARCH_COMPLETE = "REPLAY_RESEARCH_COMPLETE"
POLICY_VERSION = "POOL_WIN_EQUITY_SHADOW_V1"
CAN_EXECUTE = False


class ReplayInputError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _required_text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReplayInputError(code)
    return value.strip()


def _timestamp(value: Any, code: str) -> datetime:
    token = _required_text(value, code)
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReplayInputError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReplayInputError(code)
    return parsed.astimezone(timezone.utc)


def _probability(value: Any, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReplayInputError(code)
    result = float(value)
    if not isfinite(result) or not 0 <= result <= 1:
        raise ReplayInputError(code)
    return result


def _event_set(mapping: Any, expected: set[str], code: str) -> None:
    if not isinstance(mapping, Mapping) or set(mapping) != expected:
        raise ReplayInputError(code)


def _grade(card: Mapping[str, str], winners: Mapping[str, str],
           opposing_cards: Mapping[str, Mapping[str, str]]) -> dict[str, Any]:
    score = sum(card[e] == winners[e] for e in winners)
    opponent_scores = {
        key: sum(picks[e] == winners[e] for e in winners)
        for key, picks in opposing_cards.items()
    }
    best_opponent_score = max(opponent_scores.values())
    tied_opponents = sum(v == score for v in opponent_scores.values())
    sole_first = score > best_opponent_score
    tied_first = score == best_opponent_score
    first_or_tied = score >= best_opponent_score
    tie_share_proxy = (
        1.0 / (1 + tied_opponents) if first_or_tied else 0.0
    )
    return {
        "correct": score,
        "opponents_best_correct": best_opponent_score,
        "sole_first": sole_first,
        "tied_first": tied_first,
        "first_or_tied": first_or_tied,
        "equal_tie_share_proxy": tie_share_proxy,
        "rank_by_correct": 1 + sum(v > score for v in opponent_scores.values()),
    }


def replay_one_week(week: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed unless both cards can be reconstructed from pre-lock evidence."""
    week_id = _required_text(week.get("week_id"), "PICKEM_REPLAY_WEEK_ID_REQUIRED")
    fold = _required_text(week.get("fold"), "PICKEM_REPLAY_FOLD_REQUIRED")
    if fold not in {"DISCOVERY", "HOLDOUT"}:
        raise ReplayInputError("PICKEM_REPLAY_FOLD_INVALID")
    lock = _timestamp(week.get("lock_at"), "PICKEM_REPLAY_LOCK_INVALID")
    settled = _timestamp(week.get("settled_at"), "PICKEM_REPLAY_SETTLEMENT_INVALID")
    if settled <= lock:
        raise ReplayInputError("PICKEM_REPLAY_SETTLEMENT_PRECEDES_LOCK")
    settlement_source = _required_text(
        week.get("settlement_source"), "PICKEM_REPLAY_SETTLEMENT_SOURCE_REQUIRED"
    )
    manifest = _required_text(
        week.get("manifest_id"), "PICKEM_REPLAY_MANIFEST_REQUIRED"
    )
    raw_picks = week.get("governed_picks")
    if not isinstance(raw_picks, list) or not raw_picks:
        raise ReplayInputError("PICKEM_REPLAY_GOVERNED_PICKS_REQUIRED")
    rows: list[Mapping[str, Any]] = []
    events: set[str] = set()
    predictions: set[str] = set()
    allowed_sides: dict[str, set[str]] = {}
    baseline: dict[str, str] = {}
    expected_baseline_correct = 0.0
    for pick in raw_picks:
        if not isinstance(pick, Mapping):
            raise ReplayInputError("PICKEM_REPLAY_GOVERNED_ROW_INVALID")
        event = _required_text(pick.get("official_event_id"), "PICKEM_REPLAY_EVENT_ID_REQUIRED")
        if event in events:
            raise ReplayInputError("PICKEM_REPLAY_DUPLICATE_EVENT")
        events.add(event)
        selected = _required_text(pick.get("pool_pick"), "PICKEM_REPLAY_SELECTED_SIDE_REQUIRED")
        opponent = _required_text(pick.get("opponent"), "PICKEM_REPLAY_OPPONENT_REQUIRED")
        home = _required_text(pick.get("home_team"), "PICKEM_REPLAY_HOME_REQUIRED")
        away = _required_text(pick.get("away_team"), "PICKEM_REPLAY_AWAY_REQUIRED")
        if {selected, opponent} != {home, away} or home == away:
            raise ReplayInputError("PICKEM_REPLAY_PARTICIPANT_IDENTITY_INVALID")
        if pick.get("status") != "PICKEM_READY" or pick.get("can_execute") is not False:
            raise ReplayInputError("PICKEM_REPLAY_UNAPPROVED_SOURCE_ROW")
        if pick.get("controlling_specialist") != CONTROLLING_SPECIALIST:
            raise ReplayInputError("PICKEM_REPLAY_SPECIALIST_OWNERSHIP_MISMATCH")
        prediction = _required_text(
            pick.get("source_prediction_id"), "PICKEM_REPLAY_PREDICTION_RECEIPT_REQUIRED"
        )
        if prediction in predictions:
            raise ReplayInputError("PICKEM_REPLAY_DUPLICATE_PREDICTION_RECEIPT")
        predictions.add(prediction)
        _required_text(pick.get("source_snapshot_id"), "PICKEM_REPLAY_SOURCE_SNAPSHOT_REQUIRED")
        if _timestamp(pick.get("immutable_model_timestamp"),
                      "PICKEM_REPLAY_MODEL_TIMESTAMP_INVALID") > lock:
            raise ReplayInputError("PICKEM_REPLAY_MODEL_RECEIPT_AFTER_LOCK")
        home_p = _probability(pick.get("home_probability"), "PICKEM_REPLAY_MODEL_PROBABILITY_INVALID")
        away_p = _probability(pick.get("away_probability"), "PICKEM_REPLAY_MODEL_PROBABILITY_INVALID")
        selected_p = _probability(pick.get("selected_probability"),
                                  "PICKEM_REPLAY_MODEL_PROBABILITY_INVALID")
        if not isclose(home_p + away_p, 1.0, abs_tol=1e-9):
            raise ReplayInputError("PICKEM_REPLAY_MODEL_OUTCOMES_NOT_NORMALIZED")
        source_p = home_p if selected == home else away_p
        if not isclose(selected_p, source_p, abs_tol=1e-9) or selected_p + 1e-9 < max(home_p, away_p):
            raise ReplayInputError("PICKEM_REPLAY_BASELINE_NOT_GOVERNED_MAX")
        baseline[event] = selected
        allowed_sides[event] = {home, away}
        expected_baseline_correct += selected_p
        rows.append(pick)

    winners = week.get("settled_winners")
    _event_set(winners, events, "PICKEM_REPLAY_SETTLED_EVENT_SET_MISMATCH")
    for event, side in winners.items():
        if side not in allowed_sides[event]:
            raise ReplayInputError("PICKEM_REPLAY_SETTLED_SIDE_INVALID")

    raw_ownership = week.get("ownership_snapshots")
    _event_set(raw_ownership, events, "PICKEM_REPLAY_OWNERSHIP_EVENT_SET_MISMATCH")
    ownership_shares: dict[str, Mapping[str, float]] = {}
    ownership_receipts: dict[str, str] = {}
    for event, snapshot in raw_ownership.items():
        if not isinstance(snapshot, Mapping):
            raise ReplayInputError("PICKEM_REPLAY_OWNERSHIP_SNAPSHOT_INVALID")
        snapshot_id = _required_text(
            snapshot.get("snapshot_id"), "PICKEM_REPLAY_OWNERSHIP_SNAPSHOT_ID_REQUIRED"
        )
        _required_text(snapshot.get("source"), "PICKEM_REPLAY_OWNERSHIP_SOURCE_REQUIRED")
        if snapshot.get("audience") != "OPPONENT_ENTRIES":
            raise ReplayInputError("PICKEM_REPLAY_OWNERSHIP_AUDIENCE_INVALID")
        observed = _timestamp(snapshot.get("observed_at"), "PICKEM_REPLAY_OWNERSHIP_TIMESTAMP_INVALID")
        if observed > lock:
            raise ReplayInputError("PICKEM_REPLAY_POST_LOCK_OWNERSHIP")
        shares = snapshot.get("shares")
        _event_set(shares, allowed_sides[event], "PICKEM_REPLAY_OWNERSHIP_SIDES_MISMATCH")
        values = [_probability(shares[side], "PICKEM_REPLAY_OWNERSHIP_SHARE_INVALID")
                  for side in sorted(allowed_sides[event])]
        if not isclose(sum(values), 1.0, abs_tol=1e-6):
            raise ReplayInputError("PICKEM_REPLAY_OWNERSHIP_NOT_NORMALIZED")
        ownership_shares[event] = shares
        ownership_receipts[event] = snapshot_id

    raw_entries = week.get("opponent_entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ReplayInputError("PICKEM_REPLAY_OPPONENT_ENTRIES_REQUIRED")
    entry_ids: set[str] = set()
    opponents: dict[str, Mapping[str, str]] = {}
    for entry in raw_entries:
        if not isinstance(entry, Mapping):
            raise ReplayInputError("PICKEM_REPLAY_OPPONENT_ENTRY_INVALID")
        entry_id = _required_text(entry.get("entry_id"), "PICKEM_REPLAY_OPPONENT_ID_REQUIRED")
        if entry_id in entry_ids:
            raise ReplayInputError("PICKEM_REPLAY_DUPLICATE_OPPONENT")
        entry_ids.add(entry_id)
        if _timestamp(entry.get("submitted_at"),
                      "PICKEM_REPLAY_OPPONENT_SUBMISSION_INVALID") > lock:
            raise ReplayInputError("PICKEM_REPLAY_POST_LOCK_OPPONENT_CARD")
        _required_text(entry.get("receipt_id"), "PICKEM_REPLAY_OPPONENT_RECEIPT_REQUIRED")
        card = entry.get("picks")
        _event_set(card, events, "PICKEM_REPLAY_OPPONENT_CARD_EVENT_SET_MISMATCH")
        for event, side in card.items():
            if side not in allowed_sides[event]:
                raise ReplayInputError("PICKEM_REPLAY_OPPONENT_CARD_SIDE_INVALID")
        opponents[entry_id] = card
    size = week.get("pool_size")
    if isinstance(size, bool) or not isinstance(size, int) or size != len(opponents) + 1:
        raise ReplayInputError("PICKEM_REPLAY_POOL_SIZE_MISMATCH")

    shadow = build_pool_win_equity_shadow(
        rows, pool_pick_shares=ownership_shares, pool_size=size
    )
    if shadow["blocked_count"]:
        raise ReplayInputError("PICKEM_REPLAY_SHADOW_INPUT_BLOCKED")
    shadow_card = {row["official_event_id"]: row["shadow_pool_pick"] for row in shadow["rows"]}
    _event_set(shadow_card, events, "PICKEM_REPLAY_SHADOW_EVENT_SET_MISMATCH")
    for event, side in shadow_card.items():
        if side not in allowed_sides[event]:
            raise ReplayInputError("PICKEM_REPLAY_SHADOW_SIDE_INVALID")

    baseline_score = _grade(baseline, winners, opponents)
    shadow_score = _grade(shadow_card, winners, opponents)
    shadow_expected_correct = sum(
        next(float(r["home_probability"]) if side == r["home_team"] else
             float(r["away_probability"]) for r in rows if r["official_event_id"] == event)
        for event, side in shadow_card.items()
    )
    return {
        "week_id": week_id,
        "fold": fold,
        "manifest_id": manifest,
        "event_count": len(events),
        "pool_size": size,
        "settlement_source": settlement_source,
        "ownership_snapshot_ids": ownership_receipts,
        "baseline": baseline_score,
        "shadow": shadow_score,
        "expected_correct_baseline": expected_baseline_correct,
        "expected_correct_shadow": shadow_expected_correct,
        "expected_correct_sacrifice": expected_baseline_correct - shadow_expected_correct,
        "changed_pick_count": sum(baseline[e] != shadow_card[e] for e in events),
        "correct_delta": shadow_score["correct"] - baseline_score["correct"],
        "first_place_tie_share_delta": (
            shadow_score["equal_tie_share_proxy"] - baseline_score["equal_tie_share_proxy"]
        ),
        "automatic_promotion": False,
        "can_execute": False,
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"weeks": 0, "games": 0}
    return {
        "weeks": len(rows),
        "games": sum(r["event_count"] for r in rows),
        "average_correct_baseline": mean(r["baseline"]["correct"] for r in rows),
        "average_correct_shadow": mean(r["shadow"]["correct"] for r in rows),
        "baseline_sole_first_weeks": sum(r["baseline"]["sole_first"] for r in rows),
        "shadow_sole_first_weeks": sum(r["shadow"]["sole_first"] for r in rows),
        "baseline_first_or_tied_weeks": sum(r["baseline"]["first_or_tied"] for r in rows),
        "shadow_first_or_tied_weeks": sum(r["shadow"]["first_or_tied"] for r in rows),
        "baseline_equal_tie_share_proxy": sum(
            r["baseline"]["equal_tie_share_proxy"] for r in rows),
        "shadow_equal_tie_share_proxy": sum(
            r["shadow"]["equal_tie_share_proxy"] for r in rows),
        "total_expected_correct_sacrifice": sum(r["expected_correct_sacrifice"] for r in rows),
        "changed_picks": sum(r["changed_pick_count"] for r in rows),
    }


def replay_pool_weeks(weeks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Produce a paired diagnostic, never a certification or an optimized sporting P."""
    if not isinstance(weeks, (list, tuple)) or not weeks:
        return {
            "status": REPLAY_BLOCKED,
            "blockers": ["PICKEM_REPLAY_WEEKS_REQUIRED"],
            "automatic_promotion": False, "can_execute": False,
        }
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    for index, week in enumerate(weeks):
        try:
            if not isinstance(week, Mapping):
                raise ReplayInputError("PICKEM_REPLAY_WEEK_RECORD_INVALID")
            record = replay_one_week(week)
            if record["week_id"] in seen:
                raise ReplayInputError("PICKEM_REPLAY_DUPLICATE_WEEK")
            seen.add(record["week_id"])
            results.append(record)
        except ReplayInputError as exc:
            blockers.append({"index": str(index), "code": exc.code})
    if blockers:
        return {
            "status": REPLAY_BLOCKED, "blockers": blockers,
            "accepted_weeks": 0, "rows": [],
            "automatic_promotion": False, "can_execute": False,
        }
    discovery = [row for row in results if row["fold"] == "DISCOVERY"]
    holdout = [row for row in results if row["fold"] == "HOLDOUT"]
    status = REPLAY_RESEARCH_COMPLETE if len(discovery) > 0 and len(holdout) > 0 else REPLAY_EVIDENCE_INSUFFICIENT
    return {
        "status": status,
        "serving_mode": SERVING_MODE,
        "policy_version": POLICY_VERSION,
        "weeks": len(results),
        "rows": results,
        "discovery": _aggregate(discovery),
        "holdout": _aggregate(holdout),
        "evidence_limits": [
            "SHADOW_RULE_IS_HEURISTIC_NOT_PROVEN_OPTIMAL",
            "POINT_IN_TIME_SNAPSHOT_PROVENANCE_REQUIRES_INDEPENDENT_AUDIT",
            "GAME_OUTCOME_REPLAY_DOES_NOT_VALIDATE_SPORTING_CALIBRATION",
            "TIEBREAKER_WIN_PROBABILITY_NOT_MODELED",
            "SMALL_SAMPLES_MUST_NOT_BE_TREATED_AS_CERTIFICATION",
        ],
        "sporting_probability_modified": False,
        "production_pick_mutation_allowed": False,
        "automatic_promotion": False,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE", "POLICY_VERSION", "REPLAY_BLOCKED",
    "REPLAY_EVIDENCE_INSUFFICIENT", "REPLAY_RESEARCH_COMPLETE",
    "replay_one_week", "replay_pool_weeks",
]
