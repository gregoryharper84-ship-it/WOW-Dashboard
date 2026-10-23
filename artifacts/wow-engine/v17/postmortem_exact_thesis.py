"""Read-only exact-thesis attribution for V17 immutable pregame predictions.

Only an exact, time-valid, structured record can be model-graded. This module
never changes pregame records, historical probabilities or settlement results,
and it is not a production certification or deployment hook.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

CAN_EXECUTE = False
_SUCCESS = "MATCHED_IMMUTABLE_PREGAME_PREDICTION"
_MISSING = "EXACT_THESIS_EVIDENCE_INSUFFICIENT"
_MISMATCH = "EXACT_THESIS_IDENTITY_MISMATCH"
_UNATTRIBUTED = "MODEL_ATTRIBUTION_UNAVAILABLE"
_INVALID_TIME = "PREGAME_TIME_PROOF_INVALID"
_NO_LINE_MARKETS = frozenset({"OUTRIGHT_WINNER", "MONEYLINE", "GAME_WINNER", "MATCH_WINNER"})
_FIELDS = ("sport", "official_event_id", "participant", "market", "period", "direction", "settlement_basis")


@dataclass(frozen=True)
class ExactThesisMatch:
    status: str
    reason: str | None
    prediction_id: str | None
    canonical_thesis_key: tuple[str, ...] | None
    eligible_for_model_grading: bool
    can_execute: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    result = " ".join(value.strip().upper().split())
    return result or None


def _line(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result.normalize() if result.is_finite() else None


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        return None
    return dt.astimezone(timezone.utc)


def match_exact_pregame_thesis(
    prediction: Mapping[str, Any] | None,
    settled: Mapping[str, Any],
) -> ExactThesisMatch:
    """Require exact structured identity and strictly pre-start immutable model proof.

    This function returns diagnostic states, not an official V17 terminal label.
    It does not infer lines/directions by parsing ambiguous selection prose.
    Multiple settled slip instances of the same thesis share the returned
    `canonical_thesis_key`; they are not independent model observations.
    """
    if not prediction or prediction.get("immutable_pregame") is not True:
        return ExactThesisMatch(_UNATTRIBUTED, "IMMUTABLE_PREGAME_PREDICTION_MISSING", None, None, False)

    prediction_id_raw = prediction.get("prediction_id")
    prediction_id = prediction_id_raw.strip() if isinstance(prediction_id_raw, str) else ""
    if not prediction_id:
        return ExactThesisMatch(_UNATTRIBUTED, "PREDICTION_ID_MISSING", None, None, False)

    values: dict[str, str] = {}
    for field in _FIELDS:
        source = _text(prediction.get(field))
        outcome = _text(settled.get(field))
        if source is None or outcome is None:
            return ExactThesisMatch(_MISSING, f"{field.upper()}_MISSING", None, None, False)
        if source != outcome:
            return ExactThesisMatch(_MISMATCH, f"{field.upper()}_MISMATCH", None, None, False)
        values[field] = source

    before = _line(prediction.get("exact_line"))
    after = _line(settled.get("exact_line"))
    if values["market"] in _NO_LINE_MARKETS:
        if prediction.get("exact_line") is not None or settled.get("exact_line") is not None:
            # Do not silently recast a spread as a moneyline.
            return ExactThesisMatch(_MISMATCH, "WINNER_MARKET_LINE_PRESENT", None, None, False)
        line_part = "NO_LINE"
    else:
        if before is None or after is None:
            return ExactThesisMatch(_MISSING, "EXACT_LINE_MISSING_OR_INVALID", None, None, False)
        if before != after:
            return ExactThesisMatch(_MISMATCH, "EXACT_LINE_MISMATCH", None, None, False)
        line_part = str(before)

    model_time = _aware(prediction.get("model_timestamp"))
    written_at = _aware(prediction.get("created_at"))
    kickoff = _aware(prediction.get("event_start_utc"))
    settled_kickoff = _aware(settled.get("event_start_utc"))
    if None in (model_time, written_at, kickoff, settled_kickoff):
        return ExactThesisMatch(_INVALID_TIME, "TIME_PROOF_MISSING_OR_NAIVE", None, None, False)
    if kickoff != settled_kickoff:
        return ExactThesisMatch(_MISMATCH, "EVENT_START_IDENTITY_MISMATCH", None, None, False)
    if model_time >= kickoff or written_at >= kickoff:
        return ExactThesisMatch(_INVALID_TIME, "PREDICTION_NOT_BEFORE_EVENT_START", None, None, False)

    key = tuple(values[field] for field in _FIELDS) + (line_part,)
    return ExactThesisMatch(_SUCCESS, None, prediction_id, key, True)


__all__ = ["CAN_EXECUTE", "ExactThesisMatch", "match_exact_pregame_thesis"]
