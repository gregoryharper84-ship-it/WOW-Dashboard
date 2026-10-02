from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence


CAN_EXECUTE = False
AUTO_PROMOTION_ALLOWED = False


@dataclass(frozen=True)
class ReplayPolicy:
    policy_id: str
    max_ece: float
    max_brier: float
    minimum_validation_rows: int
    ece_bins: int = 10


def _multiclass_brier(probabilities: Sequence[float], outcome_index: int) -> float:
    if not probabilities:
        raise ValueError("empty probability vector")
    if outcome_index < 0 or outcome_index >= len(probabilities):
        raise ValueError("outcome index outside probability vector")
    return sum((p - (1.0 if i == outcome_index else 0.0)) ** 2 for i, p in enumerate(probabilities))


def _confidence_ece(rows: Sequence[Mapping[str, Any]], bins: int) -> float:
    if not rows:
        return 1.0
    bins = max(1, int(bins))
    total = len(rows)
    weighted = 0.0
    for bucket in range(bins):
        lo = bucket / bins
        hi = (bucket + 1) / bins
        selected = [
            row
            for row in rows
            if lo <= row["confidence"] < hi or (bucket == bins - 1 and row["confidence"] == 1.0)
        ]
        if not selected:
            continue
        accuracy = sum(1.0 if row["correct"] else 0.0 for row in selected) / len(selected)
        confidence = sum(row["confidence"] for row in selected) / len(selected)
        weighted += (len(selected) / total) * abs(accuracy - confidence)
    return weighted


def run_walk_forward_replay(
    *,
    challenger_id: str,
    historical_games: Iterable[Mapping[str, Any]],
    hydrate_features: Callable[[Mapping[str, Any], datetime], Mapping[str, Any]],
    predict_distribution: Callable[[Mapping[str, Any]], Sequence[float]],
    policy: ReplayPolicy,
) -> Dict[str, Any]:
    """Evaluate a Class C challenger chronologically without promotion authority.

    Feature hydration must return max_source_timestamp proving every source row
    used by the feature payload existed no later than the game's prediction cutoff.
    """
    games = sorted(historical_games, key=lambda row: row["game_timestamp"])
    scored: List[Dict[str, Any]] = []
    leakage_failures: List[Dict[str, Any]] = []

    for game in games:
        cutoff = game["game_timestamp"]
        if not isinstance(cutoff, datetime):
            raise TypeError("game_timestamp must be datetime")
        features = hydrate_features(game, cutoff)
        max_source_timestamp = features.get("max_source_timestamp")
        if not isinstance(max_source_timestamp, datetime) or max_source_timestamp > cutoff:
            leakage_failures.append(
                {
                    "game_id": game.get("id"),
                    "cutoff": cutoff.isoformat(),
                    "max_source_timestamp": (
                        max_source_timestamp.isoformat()
                        if isinstance(max_source_timestamp, datetime)
                        else None
                    ),
                }
            )
            continue

        probabilities = [float(x) for x in predict_distribution(features)]
        if not probabilities or any(p < 0.0 or p > 1.0 for p in probabilities):
            raise ValueError("predicted probabilities must be within [0, 1]")
        if abs(sum(probabilities) - 1.0) > 0.0001:
            raise ValueError("predicted distribution must normalize to 1")

        outcome_index = int(game["outcome_index"])
        predicted_index = max(range(len(probabilities)), key=probabilities.__getitem__)
        scored.append(
            {
                "game_id": game.get("id"),
                "brier": _multiclass_brier(probabilities, outcome_index),
                "confidence": max(probabilities),
                "correct": predicted_index == outcome_index,
            }
        )

    if leakage_failures:
        return {
            "challenger_id": challenger_id,
            "status": "REPLAY_INVALID_LOOKAHEAD_DETECTED",
            "typed_failure": "HISTORICAL_FEATURE_LOOKAHEAD_DETECTED",
            "promotion_allowed": False,
            "can_execute": CAN_EXECUTE,
            "leakage_failures": leakage_failures,
        }

    n_samples = len(scored)
    brier = sum(row["brier"] for row in scored) / n_samples if n_samples else 1.0
    ece = _confidence_ece(scored, policy.ece_bins)
    thresholds_passed = (
        n_samples >= policy.minimum_validation_rows
        and brier <= policy.max_brier
        and ece <= policy.max_ece
    )

    receipt_body = {
        "challenger_id": challenger_id,
        "policy_id": policy.policy_id,
        "sample_size": n_samples,
        "ece_score": ece,
        "brier_score": brier,
        "thresholds_passed": thresholds_passed,
    }
    replay_digest = sha256(
        json.dumps(receipt_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return {
        **receipt_body,
        "status": (
            "CANDIDATE_PASSED_THRESHOLDS_PENDING_GOVERNED_REVIEW"
            if thresholds_passed
            else "CANDIDATE_REJECTED"
        ),
        "typed_failure": None if thresholds_passed else "CLASS_C_QUALIFICATION_POLICY_FAILED",
        "replay_digest": replay_digest,
        "promotion_allowed": AUTO_PROMOTION_ALLOWED,
        "can_execute": CAN_EXECUTE,
    }
