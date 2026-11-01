"""Pre-registered NFL moneyline shadow challenger: confidence shrink.

Evidence (#1562, 2026-10-09 replay with the champion's exact stored
parameters): on the unseen 2025 season the champion's favourites won 60.6%
vs 64.7% predicted. Shrinking the calibrated home logit 25% toward the base
home rate improved Brier 0.2312 -> 0.2298 and log loss 0.6529 -> 0.6502.

PRE-REGISTERED 2026-10-09, before any forward evaluation. Do not retune:
- SHRINK = 0.75 (fixed in the replay above)
- BASE_HOME_RATE = 0.541301 (pooled 2024+2025 holdout home-win rate, n=569)

The challenger is a deterministic transform of the champion's stored forward
predictions, so both are graded on identical games with no change to live
scoring. Results go to the advisory-only, immutable
wow_intelligence_challenger_evaluations table. Class C research: never
published, never promoted automatically; promotion needs governed review.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

CAN_EXECUTE = False
PROBABILITY_PUBLISHABLE = False
CHALLENGER_ID = "NFL_ML_SHRINK075_V1"
PREREGISTERED_ON = "2026-10-09"
SHRINK = 0.75
BASE_HOME_RATE = 0.541301
MIN_REVIEW_N = 100
TABLE = "wow_intelligence_challenger_evaluations"


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def challenger_home_probability(champion_home_probability: float) -> float:
    p = min(max(float(champion_home_probability), 1e-9), 1 - 1e-9)
    z = SHRINK * _logit(p) + (1.0 - SHRINK) * _logit(BASE_HOME_RATE)
    return 1.0 / (1.0 + math.exp(-z))


def _log_loss(p: float, y: int) -> float:
    p = min(max(p, 1e-12), 1 - 1e-12)
    return -math.log(p if y == 1 else 1.0 - p)


def evaluate(predictions: Sequence[Any], grades: Sequence[Any]) -> dict[str, Any]:
    """Champion vs challenger on the same graded games (selected-side outcome)."""
    by_id = {str(getattr(p, "event_prediction_id", "")): p for p in predictions}
    rows: list[tuple[float, float, int]] = []
    home_selected_rows: list[tuple[float, float, int]] = []
    away_selected_rows: list[tuple[float, float, int]] = []
    for grade in grades:
        prediction = by_id.get(str(grade.event_prediction_id))
        if prediction is None:
            continue
        if prediction.selected_participant not in (prediction.home_team, prediction.away_team):
            raise ValueError("NFL_SHADOW_CHALLENGER_INVALID_SELECTED_TEAM")
        if int(grade.outcome) not in (0, 1):
            raise ValueError("NFL_SHADOW_CHALLENGER_INVALID_BINARY_OUTCOME")
        selected_home = prediction.selected_participant == prediction.home_team
        p_sel = float(grade.calibrated_probability)
        if not (math.isfinite(p_sel) and 0.0 < p_sel < 1.0):
            raise ValueError("NFL_SHADOW_CHALLENGER_INVALID_PROBABILITY")
        p_home = p_sel if selected_home else 1.0 - p_sel
        ch_home = challenger_home_probability(p_home)
        ch_sel = ch_home if selected_home else 1.0 - ch_home
        item = (p_sel, ch_sel, int(grade.outcome))
        rows.append(item)
        (home_selected_rows if selected_home else away_selected_rows).append(item)
    n = len(rows)
    if n == 0:
        return {"challenger_id": CHALLENGER_ID, "n": 0, "status": "NO_GRADED_GAMES", "can_execute": False}
    champ_brier = sum((p - y) ** 2 for p, _c, y in rows) / n
    ch_brier = sum((c - y) ** 2 for _p, c, y in rows) / n
    champ_ll = sum(_log_loss(p, y) for p, _c, y in rows) / n
    ch_ll = sum(_log_loss(c, y) for _p, c, y in rows) / n
    observed = sum(y for _p, _c, y in rows) / n
    champion_mean = sum(p for p, _c, _y in rows) / n
    challenger_mean = sum(c for _p, c, _y in rows) / n

    def _ece_for(index: int) -> float:
        total = 0.0
        for bin_i in range(10):
            lo, hi = bin_i / 10.0, (bin_i + 1) / 10.0
            items = [r for r in rows if lo <= r[index] < hi or (bin_i == 9 and r[index] == 1)]
            if items:
                total += len(items) / n * abs(sum(r[index] - r[2] for r in items) / len(items))
        return total

    challenger_ece = _ece_for(1)
    challenger_bias = challenger_mean - observed

    def _side_diagnostic(items: Sequence[tuple[float, float, int]]) -> dict[str, Any]:
        count = len(items)
        if count == 0:
            return {"n": 0, "observed_win_rate": None, "champion_mean": None,
                    "challenger_mean": None, "champion_bias": None, "challenger_bias": None}
        observed_side = sum(y for _p, _c, y in items) / count
        champion_side = sum(p for p, _c, _y in items) / count
        challenger_side = sum(c for _p, c, _y in items) / count
        return {
            "n": count,
            "observed_win_rate": observed_side,
            "champion_mean": champion_side,
            "challenger_mean": challenger_side,
            "champion_bias": champion_side - observed_side,
            "challenger_bias": challenger_side - observed_side,
        }

    side_diagnostics = {
        "selected_home": _side_diagnostic(home_selected_rows),
        "selected_away": _side_diagnostic(away_selected_rows),
    }
    # Diagnostic failures are reported even before the 100-game minimum.
    # These are *not* an early certificate, a validated lower bound, or
    # permission for a consumer to use the transformed probability.
    provisional_failures = []
    if not ch_brier < 0.25:
        provisional_failures.append("FORWARD_BRIER_NOT_BELOW_0_25")
    if not ch_ll < math.log(2):
        provisional_failures.append("FORWARD_LOG_LOSS_NOT_BELOW_LOG2")
    if not challenger_ece <= 0.12:
        provisional_failures.append("FORWARD_ECE_ABOVE_0_12")
    if not abs(challenger_bias) <= 0.08:
        provisional_failures.append("FORWARD_CALIBRATION_BIAS_ABOVE_0_08")
    provisional_failures.append("CHALLENGER_LOWER_BOUND_NOT_INDEPENDENTLY_VALIDATED")
    if n < MIN_REVIEW_N:
        provisional_failures.insert(0, f"FORWARD_GRADED_N_{n}_LT_{MIN_REVIEW_N}")
    return {
        "challenger_id": CHALLENGER_ID,
        "status": "EVALUATED",
        "n": n,
        "champion_brier": champ_brier,
        "challenger_brier": ch_brier,
        "brier_improvement": champ_brier - ch_brier,
        "champion_log_loss": champ_ll,
        "challenger_log_loss": ch_ll,
        "mean_champion_selected_probability": champion_mean,
        "mean_challenger_selected_probability": challenger_mean,
        "selected_hit_rate": observed,
        "selection_side_diagnostics": side_diagnostics,
        "champion_ece_10": _ece_for(0),
        "challenger_ece_10": challenger_ece,
        "champion_calibration_bias": champion_mean - observed,
        "challenger_calibration_bias": challenger_bias,
        "provisional_certification_failures": provisional_failures,
        "challenger_lower_bound_certified": False,
        "certification_recommendation": "DO_NOT_CERTIFY_YET",
        "eligible_for_review": n >= MIN_REVIEW_N,
        "probability_publishable": False,
        "automatic_promotion_allowed": False,
        "can_execute": False,
    }


def persist(db: Any, evaluation: Mapping[str, Any]) -> bool:
    """Append one advisory row per new graded-cohort size (idempotent)."""
    if evaluation.get("status") != "EVALUATED":
        return False
    cohort_key = f"NFL_ML_FORWARD_SHADOW_N{int(evaluation['n'])}"
    existing = (
        db.table(TABLE).select("evaluation_id").eq("challenger_id", CHALLENGER_ID)
        .eq("cohort_key", cohort_key).limit(1).execute()
    )
    if getattr(existing, "data", None):
        return False
    db.table(TABLE).insert({
        "challenger_id": CHALLENGER_ID,
        "cohort_key": cohort_key,
        "holdout_n": int(evaluation["n"]),
        "champion_brier": evaluation["champion_brier"],
        "challenger_brier": evaluation["challenger_brier"],
        "brier_improvement": evaluation["brier_improvement"],
        "champion_log_loss": evaluation["champion_log_loss"],
        "challenger_log_loss": evaluation["challenger_log_loss"],
        "eligible_for_review": bool(evaluation["eligible_for_review"]),
        "review_status": "ELIGIBLE_FOR_GOVERNED_REVIEW" if evaluation["eligible_for_review"] else "SHADOW_VALIDATING",
        "automatic_promotion_allowed": False,
        "authority": "ADVISORY_ONLY",
        "can_execute": False,
    }).execute()
    return True


__all__ = [
    "BASE_HOME_RATE", "CAN_EXECUTE", "CHALLENGER_ID", "SHRINK",
    "challenger_home_probability", "evaluate", "persist",
]
