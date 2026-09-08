"""V17 immutable postmortem learning helpers.

Settled outcomes remain exact wins/losses/pushes under the recorded pregame market.
Near-miss metadata is diagnostic only and MUST NOT rewrite the official result,
model probability, calibration, or historical win/loss record.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import isfinite
from typing import Any, Mapping

CAN_EXECUTE = False

WIN_RESULTS = {"WIN", "WON", "SETTLED_WIN"}
LOSS_RESULTS = {"LOSS", "LOST", "SETTLED_LOSS"}
PUSH_RESULTS = {"PUSH", "TIE", "VOID", "REFUND"}
MORE_DIRECTIONS = {"MORE", "OVER", "YES_MORE"}
LESS_DIRECTIONS = {"LESS", "UNDER", "YES_LESS"}


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if isfinite(value) else None


def signed_distance_to_threshold(*, actual_value: Any, line: Any, direction: Any) -> float | None:
    """Return selected-side clearance distance for scalar exact-line outcomes.

    Positive means the selected direction cleared the recorded line, negative means
    it missed, and zero means the outcome landed exactly on the numerical boundary.
    Non-scalar markets such as moneyline winners return ``None``.
    """
    actual = _number(actual_value)
    threshold = _number(line)
    side = _norm(direction)
    if actual is None or threshold is None:
        return None
    if side in MORE_DIRECTIONS:
        return actual - threshold
    if side in LESS_DIRECTIONS:
        return threshold - actual
    return None


def close_miss_flag(
    *,
    official_result: Any,
    signed_distance: float | None,
    close_miss_tolerance: float | None,
) -> bool | None:
    """Classify a numerical loss as close only when a lane supplies a tolerance.

    A universal tolerance is intentionally prohibited because one unit has different
    meaning across strikeouts, pitches, games, points, yards and other markets.
    """
    if close_miss_tolerance is None or signed_distance is None:
        return None
    tolerance = _number(close_miss_tolerance)
    if tolerance is None or tolerance < 0:
        raise ValueError("INVALID_CLOSE_MISS_TOLERANCE")
    if _norm(official_result) not in LOSS_RESULTS:
        return False
    return signed_distance < 0 and abs(signed_distance) <= tolerance


@dataclass(frozen=True)
class PostmortemOutcome:
    prediction_id: str
    official_result: str
    settlement_source: str
    settlement_timestamp: str
    closing_market_probability: float | None
    observed_path: str | None
    process_classification: str | None
    actual_value: float | None
    signed_distance_to_threshold: float | None
    close_miss_flag: bool | None
    close_miss_tolerance: float | None
    duplicate_thesis_count: int
    cards_killed: int
    critical_leg_rank: int | None
    can_execute: bool = CAN_EXECUTE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_postmortem_outcome(
    prediction: Mapping[str, Any],
    *,
    official_result: str,
    settlement_source: str,
    settlement_timestamp: str,
    actual_value: Any = None,
    closing_market_probability: Any = None,
    observed_path: str | None = None,
    process_classification: str | None = None,
    close_miss_tolerance: float | None = None,
    duplicate_thesis_count: int = 1,
    cards_killed: int = 0,
    critical_leg_rank: int | None = None,
) -> PostmortemOutcome:
    """Append learning metadata while preserving immutable pregame grading."""
    prediction_id = str(prediction.get("prediction_id") or "").strip()
    if not prediction_id:
        raise ValueError("PREDICTION_ID_REQUIRED")
    if not str(settlement_source or "").strip():
        raise ValueError("SETTLEMENT_SOURCE_REQUIRED")
    if not str(settlement_timestamp or "").strip():
        raise ValueError("SETTLEMENT_TIMESTAMP_REQUIRED")
    result = _norm(official_result)
    if result not in WIN_RESULTS | LOSS_RESULTS | PUSH_RESULTS:
        raise ValueError("OFFICIAL_RESULT_UNRECOGNIZED")
    if isinstance(duplicate_thesis_count, bool) or int(duplicate_thesis_count) < 1:
        raise ValueError("INVALID_DUPLICATE_THESIS_COUNT")
    if isinstance(cards_killed, bool) or int(cards_killed) < 0:
        raise ValueError("INVALID_CARDS_KILLED")
    if critical_leg_rank is not None and (
        isinstance(critical_leg_rank, bool) or int(critical_leg_rank) < 1
    ):
        raise ValueError("INVALID_CRITICAL_LEG_RANK")

    distance = signed_distance_to_threshold(
        actual_value=actual_value,
        line=prediction.get("exact_line", prediction.get("line")),
        direction=prediction.get("direction", prediction.get("side")),
    )
    close = close_miss_flag(
        official_result=result,
        signed_distance=distance,
        close_miss_tolerance=close_miss_tolerance,
    )
    actual = _number(actual_value)
    closing = _number(closing_market_probability)
    if closing is not None and not 0.0 <= closing <= 1.0:
        raise ValueError("INVALID_CLOSING_MARKET_PROBABILITY")

    # Governance invariant: close-miss information is metadata only. It never changes
    # the settlement result supplied by the official source.
    return PostmortemOutcome(
        prediction_id=prediction_id,
        official_result=result,
        settlement_source=str(settlement_source).strip(),
        settlement_timestamp=str(settlement_timestamp).strip(),
        closing_market_probability=closing,
        observed_path=str(observed_path).strip() if observed_path else None,
        process_classification=str(process_classification).strip() if process_classification else None,
        actual_value=actual,
        signed_distance_to_threshold=distance,
        close_miss_flag=close,
        close_miss_tolerance=_number(close_miss_tolerance),
        duplicate_thesis_count=int(duplicate_thesis_count),
        cards_killed=int(cards_killed),
        critical_leg_rank=int(critical_leg_rank) if critical_leg_rank is not None else None,
        can_execute=False,
    )


__all__ = [
    "CAN_EXECUTE",
    "PostmortemOutcome",
    "build_postmortem_outcome",
    "close_miss_flag",
    "signed_distance_to_threshold",
]
