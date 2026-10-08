"""Settled-week NFL Pick'em postmortem diagnostics.

This module grades contest outcomes and contest-construction behavior. It does not
reconstruct, mutate, or infer sporting probabilities. Model-quality attribution
still requires immutable pregame V17 prediction receipts.

The purpose is to detect reusable contest-strategy facts such as:
- consensus concentration;
- shared-consensus losses that did not hurt relative standing;
- whether minority selections were used at all;
- how many winner-vs-user disagreements actually created the weekly gap.

All learning output is diagnostic. It cannot modify the controlling NFL model,
calibration, terminal state, or production pool pick.
"""
from __future__ import annotations

from collections import Counter
from math import isfinite
from typing import Any, Mapping

CAN_EXECUTE = False
PRODUCTION_PROBABILITY_MUTATION_ALLOWED = False
AUTOMATIC_PROMOTION_ALLOWED = False
SERVING_MODE = "POSTMORTEM_DIAGNOSTIC_ONLY"

LEARNING_PRESERVE = "PRESERVE"
LEARNING_LOG_ONLY = "LOG_ONLY"
LEARNING_WATCH = "WATCH_HYPOTHESIS"
LEARNING_SHADOW = "SHADOW_CHALLENGER_EARNED"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if isfinite(parsed) else None


def _validate_inputs(
    participant_picks: Mapping[str, Mapping[str, str]],
    winners: Mapping[str, str],
    *,
    user_name: str,
) -> list[str]:
    if not participant_picks:
        raise ValueError("PICKEM_POSTMORTEM_PARTICIPANTS_REQUIRED")
    if user_name not in participant_picks:
        raise ValueError("PICKEM_POSTMORTEM_USER_NOT_FOUND")
    events = list(winners)
    if not events:
        raise ValueError("PICKEM_POSTMORTEM_WINNERS_REQUIRED")
    if len(set(events)) != len(events):
        raise ValueError("PICKEM_POSTMORTEM_DUPLICATE_EVENT_ID")
    winner_tokens = {_clean(event): _clean(side) for event, side in winners.items()}
    if any(not event or not side for event, side in winner_tokens.items()):
        raise ValueError("PICKEM_POSTMORTEM_WINNER_IDENTITY_INVALID")

    expected = set(winner_tokens)
    for participant, picks in participant_picks.items():
        if set(picks) != expected:
            raise ValueError(
                f"PICKEM_POSTMORTEM_EVENT_SET_MISMATCH:{_clean(participant) or 'UNKNOWN'}"
            )
        if any(not _clean(side) for side in picks.values()):
            raise ValueError(
                f"PICKEM_POSTMORTEM_EMPTY_PICK:{_clean(participant) or 'UNKNOWN'}"
            )
    return events


def analyze_settled_pickem_week(
    participant_picks: Mapping[str, Mapping[str, str]],
    winners: Mapping[str, str],
    *,
    user_name: str,
    tiebreaker_predictions: Mapping[str, float] | None = None,
    actual_tiebreaker_total: float | None = None,
) -> dict[str, Any]:
    """Analyze one fully settled pick'em week without hindsight probability mutation."""
    events = _validate_inputs(participant_picks, winners, user_name=user_name)
    participants = list(participant_picks)
    pool_size = len(participants)
    clean_winners = {event: _clean(winners[event]) for event in events}

    per_event: list[dict[str, Any]] = []
    wins_by_participant = {participant: 0 for participant in participants}

    for event in events:
        counts = Counter(_clean(participant_picks[p][event]) for p in participants)
        winner = clean_winners[event]
        for participant in participants:
            if _clean(participant_picks[participant][event]) == winner:
                wins_by_participant[participant] += 1

        max_count = max(counts.values())
        leaders = sorted(side for side, count in counts.items() if count == max_count)
        has_strict_majority = max_count > pool_size / 2
        majority_side = leaders[0] if has_strict_majority and len(leaders) == 1 else None
        split_top = len(leaders) > 1 or max_count * 2 == pool_size

        user_pick = _clean(participant_picks[user_name][event])
        user_pick_count = counts[user_pick]
        user_correct = user_pick == winner
        user_is_minority = user_pick_count < max_count
        user_is_majority = majority_side == user_pick
        user_is_split_top = not user_is_minority and majority_side is None
        unanimous_side = next(iter(counts)) if len(counts) == 1 else None
        unanimous_shared_loss = unanimous_side is not None and unanimous_side != winner
        majority_side_lost = majority_side is not None and majority_side != winner

        per_event.append(
            {
                "event_id": event,
                "winner": winner,
                "user_pick": user_pick,
                "user_correct": user_correct,
                "pool_pick_counts": dict(sorted(counts.items())),
                "user_pick_count": user_pick_count,
                "user_pick_share": user_pick_count / pool_size,
                "strict_majority_side": majority_side,
                "strict_majority_count": max_count if majority_side else None,
                "split_top": split_top,
                "user_followed_strict_majority": user_is_majority,
                "user_on_split_top": user_is_split_top,
                "user_made_minority_pick": user_is_minority,
                "unanimous_shared_loss": unanimous_shared_loss,
                "majority_side_lost": majority_side_lost,
            }
        )

    user_wins = wins_by_participant[user_name]
    best_wins = max(wins_by_participant.values())
    weekly_winners = sorted(
        participant
        for participant, wins in wins_by_participant.items()
        if wins == best_wins
    )

    standings = sorted(
        (
            {
                "participant": participant,
                "wins": wins,
                "losses": len(events) - wins,
            }
            for participant, wins in wins_by_participant.items()
        ),
        key=lambda item: (-item["wins"], item["participant"]),
    )
    wins_rank = 1 + sum(item["wins"] > user_wins for item in standings)

    majority_follow_count = sum(
        1 for row in per_event if row["user_followed_strict_majority"]
    )
    split_top_count = sum(1 for row in per_event if row["user_on_split_top"])
    minority_pick_count = sum(1 for row in per_event if row["user_made_minority_pick"])
    unanimous_shared_loss_count = sum(
        1 for row in per_event if row["unanimous_shared_loss"]
    )
    majority_side_loss_count = sum(
        1
        for row in per_event
        if row["majority_side_lost"] and row["user_followed_strict_majority"]
    )

    winner_comparisons: list[dict[str, Any]] = []
    for winner_name in weekly_winners:
        disagreements = []
        winner_gain_events = []
        user_gain_events = []
        for event in events:
            user_pick = _clean(participant_picks[user_name][event])
            other_pick = _clean(participant_picks[winner_name][event])
            if user_pick == other_pick:
                continue
            disagreements.append(event)
            actual = clean_winners[event]
            if other_pick == actual and user_pick != actual:
                winner_gain_events.append(event)
            elif user_pick == actual and other_pick != actual:
                user_gain_events.append(event)
        winner_comparisons.append(
            {
                "weekly_winner": winner_name,
                "disagreement_count": len(disagreements),
                "disagreement_events": disagreements,
                "winner_gain_events": winner_gain_events,
                "winner_gain_count": len(winner_gain_events),
                "user_gain_events": user_gain_events,
                "user_gain_count": len(user_gain_events),
                "net_disagreement_swing": len(winner_gain_events) - len(user_gain_events),
            }
        )

    tiebreaker = {
        "available": False,
        "user_prediction": None,
        "actual_total": None,
        "absolute_error": None,
        "model_attribution_available": False,
    }
    if tiebreaker_predictions is not None and actual_tiebreaker_total is not None:
        predicted = _finite_number(tiebreaker_predictions.get(user_name))
        actual = _finite_number(actual_tiebreaker_total)
        if predicted is not None and actual is not None:
            tiebreaker = {
                "available": True,
                "user_prediction": predicted,
                "actual_total": actual,
                "absolute_error": abs(predicted - actual),
                "model_attribution_available": False,
            }

    no_minority_card = minority_pick_count == 0
    not_weekly_winner = user_wins < best_wins
    pool_strategy_shadow_earned = no_minority_card and not_weekly_winner

    learning_actions: list[dict[str, str]] = [
        {
            "lane": "SPORTING_MODEL",
            "learning_state": LEARNING_LOG_ONLY,
            "action": "NO_PROBABILITY_OR_CALIBRATION_CHANGE_FROM_POOL_STANDINGS_ALONE",
        },
        {
            "lane": "SHARED_CONSENSUS_LOSS",
            "learning_state": LEARNING_PRESERVE,
            "action": "DO_NOT_DOWNGRADE_STRONG_SIDE_SOLELY_BECAUSE_THE_ENTIRE_POOL_MISSED",
        },
    ]
    if pool_strategy_shadow_earned:
        learning_actions.append(
            {
                "lane": "POOL_CONSTRUCTION",
                "learning_state": LEARNING_SHADOW,
                "action": "RUN_POOL_WIN_EQUITY_SHADOW_ON_FRAGILE_OR_TOSS_UP_ROWS",
            }
        )
    elif not_weekly_winner:
        learning_actions.append(
            {
                "lane": "POOL_CONSTRUCTION",
                "learning_state": LEARNING_WATCH,
                "action": "REVIEW_DIFFERENTIATION_QUALITY_WITHOUT_FORCING_CONTRARIAN_PICKS",
            }
        )

    return {
        "serving_mode": SERVING_MODE,
        "user_name": user_name,
        "pool_size": pool_size,
        "event_count": len(events),
        "user_wins": user_wins,
        "user_losses": len(events) - user_wins,
        "best_wins": best_wins,
        "gap_to_best": best_wins - user_wins,
        "wins_rank": wins_rank,
        "weekly_winners": weekly_winners,
        "standings": standings,
        "majority_follow_count": majority_follow_count,
        "split_top_count": split_top_count,
        "minority_pick_count": minority_pick_count,
        "all_consensus_or_split_top": minority_pick_count == 0,
        "unanimous_shared_loss_count": unanimous_shared_loss_count,
        "majority_side_loss_count": majority_side_loss_count,
        "winner_comparisons": winner_comparisons,
        "events": per_event,
        "tiebreaker": tiebreaker,
        "pool_strategy_shadow_review_earned": pool_strategy_shadow_earned,
        "production_probability_patch_earned": False,
        "learning_actions": learning_actions,
        "automatic_promotion": False,
        "production_probability_mutation_allowed": False,
        "can_execute": False,
    }


__all__ = [
    "AUTOMATIC_PROMOTION_ALLOWED",
    "CAN_EXECUTE",
    "PRODUCTION_PROBABILITY_MUTATION_ALLOWED",
    "SERVING_MODE",
    "analyze_settled_pickem_week",
]
