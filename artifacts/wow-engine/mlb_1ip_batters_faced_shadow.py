"""WOW V17 MLB first-inning batters-faced shadow challenger.

This module deliberately reuses the governed MLB 1IP event-tree's batter-faced
probability distribution instead of inventing a new probability source.  It is
an offline/shadow research adapter for the PrizePicks-style
"1st Inning Batters Faced" market family.

Important governance properties:
- the upstream 1IP specialist remains the probability-producing model;
- this adapter only maps an already-produced P(BF) distribution to an exact BF line;
- no calibration or calibrated lower bound is manufactured here;
- no row is rank-eligible or probability-publishable from this module alone;
- unsupported BF lines fail closed rather than interpolating P(BF>=5);
- can_execute is permanently false.

Initial exact support is intentionally narrow.  The current 1IP contract exposes
P(BF=3), P(BF=4), and P(BF>=5).  That is sufficient to score 3.5 and 4.5 lines
(and integer 3.0/4.0 for shadow grading with push mass), but it cannot identify
P(BF=5) separately from P(BF>=6), so 5.5+ is out of distribution until the
upstream event tree exposes a fuller BF PMF.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

CAN_EXECUTE = False
CANONICAL_STAT_TYPE = "1ST_INNING_BATTERS_FACED"
CONTROLLING_SPECIALIST = "wow.mlb-first-inning-pitch-count-expert"
SHADOW_ADAPTER = "mlb_1ip_batters_faced_shadow_v1"
UPSTREAM_BF_SCHEMA = "BF_3_4_5PLUS_V1"
SUPPORTED_LINES = frozenset({3.0, 3.5, 4.0, 4.5})
_NORMALIZATION_TOLERANCE = 0.02


def _finite_probability(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("BF_DISTRIBUTION_NON_NUMERIC")
    result = float(value)
    if not math.isfinite(result) or result < 0.0 or result > 1.0:
        raise ValueError("BF_DISTRIBUTION_INVALID_PROBABILITY")
    return result


def _extract_bf_distribution(upstream_package: dict[str, Any]) -> tuple[float, float, float]:
    """Extract and safely normalize the three-bucket upstream BF distribution."""
    if not isinstance(upstream_package, dict):
        raise ValueError("UPSTREAM_1IP_PACKAGE_INVALID")

    if upstream_package.get("model_evaluated") is False:
        raise ValueError("UPSTREAM_1IP_MODEL_NOT_EVALUATED")

    # Accept either public skill-style keys or ledger-style keys.  We do not
    # accept raw recent hit rates or generic averages as substitutes.
    if all(k in upstream_package for k in ("P_BF_3", "P_BF_4", "P_BF_GE_5")):
        p3 = _finite_probability(upstream_package["P_BF_3"])
        p4 = _finite_probability(upstream_package["P_BF_4"])
        p5 = _finite_probability(upstream_package["P_BF_GE_5"])
    elif all(k in upstream_package for k in ("p_bf_3", "p_bf_4", "p_bf_gte5")):
        p3 = _finite_probability(upstream_package["p_bf_3"])
        p4 = _finite_probability(upstream_package["p_bf_4"])
        p5 = _finite_probability(upstream_package["p_bf_gte5"])
    else:
        raise ValueError("UPSTREAM_BF_DISTRIBUTION_MISSING")

    total = p3 + p4 + p5
    if total <= 0.0:
        raise ValueError("UPSTREAM_BF_DISTRIBUTION_EMPTY")
    if abs(total - 1.0) > _NORMALIZATION_TOLERANCE:
        raise ValueError("UPSTREAM_BF_DISTRIBUTION_NOT_NORMALIZED")

    # The event-tree output is rounded for transport, so normalize only within
    # the narrow tolerance above.  This is not calibration or model repair.
    return p3 / total, p4 / total, p5 / total


def _score_line(*, p3: float, p4: float, p5plus: float, line_value: float) -> tuple[float, float, float]:
    """Return (P_MORE, P_LESS, P_PUSH) for an exactly supported BF line."""
    if line_value not in SUPPORTED_LINES:
        raise ValueError("BF_LINE_UNSUPPORTED_BY_COARSE_BUCKETS")

    if line_value == 3.0:
        return p4 + p5plus, 0.0, p3
    if line_value == 3.5:
        return p4 + p5plus, p3, 0.0
    if line_value == 4.0:
        return p5plus, p3, p4
    # 4.5
    return p5plus, p3 + p4, 0.0


def score_batters_faced_shadow(
    *,
    upstream_1ip_package: dict[str, Any],
    line_value: float,
    side: str,
) -> dict[str, Any]:
    """Map a governed upstream P(BF) package to one exact BF shadow prediction.

    This function never returns a governed calibrated probability or lower
    bound.  Its selected probability is a *shadow derivative* of the upstream
    1IP event-tree distribution and is suitable only for immutable forward
    logging, settlement, and subsequent calibration research.
    """
    try:
        line = float(line_value)
    except (TypeError, ValueError) as exc:
        raise ValueError("BF_LINE_INVALID") from exc
    if not math.isfinite(line):
        raise ValueError("BF_LINE_INVALID")

    direction = str(side or "").strip().upper()
    if direction not in {"MORE", "LESS"}:
        raise ValueError("BF_DIRECTION_INVALID")

    p3, p4, p5plus = _extract_bf_distribution(upstream_1ip_package)
    p_more, p_less, p_push = _score_line(
        p3=p3,
        p4=p4,
        p5plus=p5plus,
        line_value=line,
    )
    selected = p_more if direction == "MORE" else p_less

    return {
        "stat_type": CANONICAL_STAT_TYPE,
        "shadow_adapter": SHADOW_ADAPTER,
        "controlling_specialist": CONTROLLING_SPECIALIST,
        "upstream_bf_schema": UPSTREAM_BF_SCHEMA,
        "upstream_model_used": upstream_1ip_package.get("model_used"),
        "upstream_model_timestamp": upstream_1ip_package.get("model_timestamp"),
        "line_value": line,
        "direction": direction,
        "P_BF_3": round(p3, 6),
        "P_BF_4": round(p4, 6),
        "P_BF_GE_5": round(p5plus, 6),
        "P_MORE": round(p_more, 6),
        "P_LESS": round(p_less, 6),
        "prob_push": round(p_push, 6),
        "selected_probability": round(selected, 6),
        "calibrated_probability": None,
        "calibrated_lower_bound": None,
        "calibration_status": "SHADOW_UNCALIBRATED",
        "model_status": "SHADOW_CHALLENGER",
        "terminal_label": "RESEARCH_INTEREST",
        "rank_eligible": False,
        "probability_publishable": False,
        "immutable_forward_log_required": True,
        "promotion_required_before_governed_use": True,
        "can_execute": False,
    }


def grade_shadow_records(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Grade settled immutable shadow predictions without promoting them.

    Each record must contain ``selected_probability`` and binary ``outcome``
    (1 = selected side hit, 0 = selected side missed).  Push/void rows should be
    excluded by the caller under the settlement contract; this helper refuses
    non-binary outcomes rather than silently treating them as losses.
    """
    probs: list[float] = []
    outcomes: list[int] = []
    for record in records:
        p = _finite_probability(record.get("selected_probability"))
        y = record.get("outcome")
        if isinstance(y, bool):
            y = int(y)
        if y not in {0, 1}:
            raise ValueError("BF_SHADOW_OUTCOME_MUST_BE_BINARY")
        probs.append(p)
        outcomes.append(int(y))

    n = len(probs)
    if n == 0:
        return {
            "n": 0,
            "mean_probability": None,
            "observed_hit_rate": None,
            "brier": None,
            "log_loss": None,
            "calibration_claim_allowed": False,
            "can_execute": False,
        }

    eps = 1e-12
    brier = sum((p - y) ** 2 for p, y in zip(probs, outcomes)) / n
    log_loss = -sum(
        y * math.log(max(eps, min(1.0 - eps, p)))
        + (1 - y) * math.log(max(eps, min(1.0 - eps, 1.0 - p)))
        for p, y in zip(probs, outcomes)
    ) / n

    return {
        "n": n,
        "mean_probability": sum(probs) / n,
        "observed_hit_rate": sum(outcomes) / n,
        "brier": brier,
        "log_loss": log_loss,
        "calibration_claim_allowed": False,
        "promotion_status": "VALIDATION_REQUIRED",
        "can_execute": False,
    }
