"""Market-optional qualification contract for V17 Free-Core Independence.

The fitted sporting probability and the market/value decision are separate
surfaces. User-supplied or provider-supplied lines may identify a market for
comparison, but they never become sporting probability inputs or authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

CAN_EXECUTE = False
MODEL_PROBABILITY_AVAILABLE = "MODEL_PROBABILITY_AVAILABLE"
MODEL_PROBABILITY_UNAVAILABLE = "MODEL_PROBABILITY_UNAVAILABLE"
MARKET_VALUE_INPUT_AVAILABLE = "MARKET_VALUE_INPUT_AVAILABLE"
MARKET_VALUE_INPUT_UNAVAILABLE = "MARKET_VALUE_INPUT_UNAVAILABLE"

VALID_MARKET_FAMILIES = frozenset({"MONEYLINE", "SPREAD", "PROP"})


class MarketInputError(ValueError):
    pass


def _number(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise MarketInputError(f"{field}_INVALID")
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketInputError(f"{field}_INVALID") from exc
    if out != out or out in {float("inf"), float("-inf")}:
        raise MarketInputError(f"{field}_INVALID")
    return out


@dataclass(frozen=True)
class MarketInput:
    market_family: str
    selection: str
    line: float | None
    american_odds: float | None
    stat_type: str | None
    direction: str | None
    captured_at: str
    source: str = "USER_SUPPLIED"
    market_identity_authority: bool = True
    sporting_probability_authority: bool = False
    can_execute: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "market_family": self.market_family,
            "selection": self.selection,
            "line": self.line,
            "american_odds": self.american_odds,
            "stat_type": self.stat_type,
            "direction": self.direction,
            "captured_at": self.captured_at,
            "source": self.source,
            "market_identity_authority": self.market_identity_authority,
            "sporting_probability_authority": False,
            "can_execute": False,
        }


def normalize_user_market_input(payload: Mapping[str, Any]) -> MarketInput:
    family = str(payload.get("market_family") or "").strip().upper()
    if family not in VALID_MARKET_FAMILIES:
        raise MarketInputError("MARKET_FAMILY_UNSUPPORTED")
    selection = str(payload.get("selection") or "").strip()
    if not selection:
        raise MarketInputError("MARKET_SELECTION_REQUIRED")

    line: float | None = None
    stat_type: str | None = None
    direction: str | None = None
    if family in {"SPREAD", "PROP"}:
        line = _number(payload.get("line"), field="MARKET_LINE")
    if family == "PROP":
        stat_type = str(payload.get("stat_type") or "").strip()
        direction = str(payload.get("direction") or "").strip().upper()
        if not stat_type:
            raise MarketInputError("PROP_STAT_TYPE_REQUIRED")
        if direction not in {"MORE", "LESS", "OVER", "UNDER"}:
            raise MarketInputError("PROP_DIRECTION_INVALID")

    price = payload.get("american_odds")
    american_odds = None if price in {None, ""} else _number(price, field="AMERICAN_ODDS")
    captured_at = str(payload.get("captured_at") or "").strip()
    if not captured_at:
        captured_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    return MarketInput(
        market_family=family,
        selection=selection,
        line=line,
        american_odds=american_odds,
        stat_type=stat_type,
        direction=direction,
        captured_at=captured_at,
    )


def qualification_receipt(
    model_package: Mapping[str, Any] | None,
    market_input: MarketInput | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    model = dict(model_package or {})
    probability = model.get("calibrated_probability")
    probability_available = (
        isinstance(probability, (int, float))
        and not isinstance(probability, bool)
        and 0.0 <= float(probability) <= 1.0
    )
    has_market = market_input is not None
    market = (
        market_input.as_dict()
        if isinstance(market_input, MarketInput)
        else dict(market_input or {})
    )
    return {
        "probability_qualification": (
            MODEL_PROBABILITY_AVAILABLE
            if probability_available
            else MODEL_PROBABILITY_UNAVAILABLE
        ),
        "market_value_qualification": (
            MARKET_VALUE_INPUT_AVAILABLE
            if has_market
            else MARKET_VALUE_INPUT_UNAVAILABLE
        ),
        "calibrated_probability": float(probability) if probability_available else None,
        "market_input": market if has_market else None,
        "probability_can_exist_without_market": True,
        "market_probability_used_as_model": False,
        "generic_reasoning_probability_substitution_allowed": False,
        "market_value_decision_requires_market_input": True,
        "can_execute": False,
    }


__all__ = [
    "CAN_EXECUTE",
    "MARKET_VALUE_INPUT_AVAILABLE",
    "MARKET_VALUE_INPUT_UNAVAILABLE",
    "MODEL_PROBABILITY_AVAILABLE",
    "MODEL_PROBABILITY_UNAVAILABLE",
    "MarketInput",
    "MarketInputError",
    "normalize_user_market_input",
    "qualification_receipt",
]
