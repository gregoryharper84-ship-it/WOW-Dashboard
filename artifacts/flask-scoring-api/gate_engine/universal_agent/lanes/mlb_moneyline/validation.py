"""
gate_engine/universal_agent/lanes/mlb_moneyline/validation.py
WOW-PATCH-2026-08-10-UNIVERSAL-AGENT-CORE-V1-B3A

MLB Moneyline adapter input validator.

Validates the raw row dict from the WOW/LLP pipeline before any field
mapping occurs. Fail-closed: raises AdapterInputError on any required
identity field missing. Never fabricates values.

Required fields (AdapterInputError if absent or wrong):
  sport       must be "MLB" (case-insensitive)
  market/     must contain a winner/moneyline keyword
    prop_type
  event_id    must be a non-empty string — used as canonical_event_id

All evidence fields (starter_status, odds, model_probability, etc.) are
NOT validated here — they are handled with explicit UNKNOWN/MISSING
degradation in field_map.py.

can_execute = False
"""
from __future__ import annotations

from typing import Any

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
ADAPTER_MODULE = "mlb_moneyline_adapter"
ADAPTER_VERSION = "v1.0"

# Must match keywords in llp_mlb_winner_preflight._is_mlb_winner_row
_WINNER_KEYWORDS: tuple[str, ...] = ("winner", "moneyline", "ml", "game winner")


class AdapterInputError(ValueError):
    """
    Raised when required identity fields are absent, or sport/market mismatch.

    Attributes
    ----------
    code
        Short machine-readable error code (e.g. "ADAPTER_SPORT_MISMATCH").
    message
        Human-readable explanation.
    """
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code    = code
        self.message = message

    def __repr__(self) -> str:
        return f"AdapterInputError(code={self.code!r}, message={self.message!r})"


def validate_mlb_moneyline_row(row: Any) -> None:
    """
    Validate that `row` is an MLB moneyline candidate suitable for adapter mapping.

    Checks (in order):
      1. row is a dict.
      2. row["sport"] is "MLB" (case-insensitive strip).
      3. row["market"] or row["prop_type"] contains a winner/moneyline keyword.
      4. row["event_id"] is present and non-empty.

    Raises AdapterInputError on the first failing check.
    Does NOT validate evidence fields (starter_status, odds, etc.).
    """
    if not isinstance(row, dict):
        raise AdapterInputError(
            "ADAPTER_INPUT_NOT_DICT",
            f"MLB moneyline adapter requires a dict row, got {type(row).__name__}",
        )

    sport = (row.get("sport") or "").strip().upper()
    if sport != "MLB":
        raise AdapterInputError(
            "ADAPTER_SPORT_MISMATCH",
            f"MLB moneyline adapter requires sport='MLB', got {sport!r}",
        )

    market = (row.get("market") or row.get("prop_type") or "").strip().lower()
    if not any(kw in market for kw in _WINNER_KEYWORDS):
        raise AdapterInputError(
            "ADAPTER_MARKET_MISMATCH",
            f"MLB moneyline adapter requires a winner/moneyline market type, "
            f"got {market!r}",
        )

    event_id = row.get("event_id")
    if not event_id or not str(event_id).strip():
        raise AdapterInputError(
            "ADAPTER_MISSING_EVENT_ID",
            "MLB moneyline adapter requires a non-empty event_id "
            "(used as canonical_event_id)",
        )
