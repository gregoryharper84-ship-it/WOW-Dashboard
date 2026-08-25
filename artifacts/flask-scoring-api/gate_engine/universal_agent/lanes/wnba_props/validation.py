"""
gate_engine/universal_agent/lanes/wnba_props/validation.py
WOW-PATCH-2026-08-11-UNIVERSAL-AGENT-CORE-V1-B4

WNBA/NBA Props adapter input validator.

Required identity checks (AdapterInputError if any fail):
  sport       — "WNBA" or "NBA" (case-insensitive)
  market /    — must NOT contain winner/moneyline keyword (props lane blocks h2h)
  prop_type
  event_id    — non-empty string; used as canonical_event_id

Evidence/enrichment fields (role_status, hit_probability, etc.) are NOT
validated here — they degrade gracefully via UNKNOWN/MISSING sentinels in
field_map.py.

can_execute = False
"""
from __future__ import annotations

from typing import Any

can_execute    = False
EXECUTION_RULE = "DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS"
ADAPTER_MODULE  = "wnba_props_adapter"
ADAPTER_VERSION = "v1.0"

_ALLOWED_SPORTS: frozenset[str] = frozenset({"WNBA", "NBA"})
_WINNER_KEYWORDS: tuple[str, ...] = ("winner", "moneyline", "ml", "game winner")


class AdapterInputError(ValueError):
    """Required identity field absent or sport/market mismatch."""
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code    = code
        self.message = message

    def __repr__(self) -> str:
        return f"AdapterInputError(code={self.code!r}, message={self.message!r})"


def validate_wnba_props_row(row: Any) -> None:
    """
    Validate that `row` is a WNBA/NBA props candidate.

    Checks (in order):
      1. row is a dict.
      2. sport in {"WNBA", "NBA"} (case-insensitive).
      3. market/prop_type does NOT contain a winner/moneyline keyword.
      4. event_id is present and non-empty.

    Raises AdapterInputError on first failure.
    Does NOT validate evidence fields (role_status, hit_probability, etc.).
    """
    if not isinstance(row, dict):
        raise AdapterInputError(
            "ADAPTER_INPUT_NOT_DICT",
            f"WNBA props adapter requires a dict row, got {type(row).__name__}",
        )

    sport = (row.get("sport") or "").strip().upper()
    if sport not in _ALLOWED_SPORTS:
        raise AdapterInputError(
            "ADAPTER_SPORT_MISMATCH",
            f"WNBA props adapter requires sport in {sorted(_ALLOWED_SPORTS)}, got {sport!r}",
        )

    market = (row.get("market") or row.get("prop_type") or "").strip().lower()
    if any(kw in market for kw in _WINNER_KEYWORDS):
        raise AdapterInputError(
            "ADAPTER_MARKET_MISMATCH",
            f"WNBA props adapter rejects winner/moneyline markets; got {market!r}. "
            "Use the MLB Moneyline lane for h2h markets.",
        )

    event_id = row.get("event_id")
    if not event_id or not str(event_id).strip():
        raise AdapterInputError(
            "ADAPTER_MISSING_EVENT_ID",
            "WNBA props adapter requires a non-empty event_id "
            "(used as canonical_event_id).",
        )
