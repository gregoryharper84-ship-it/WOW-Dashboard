"""
board_intake.py
Normalize raw PrizePicks board rows into a canonical gate_engine row dict.
Every row that enters here appears in every downstream output.
Missing required fields are flagged — never fake-filled.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from .labels import DataStatus


REQUIRED_FIELDS = ["player", "sport", "prop_type", "line", "direction"]
OPTIONAL_FIELDS = [
    "league", "game", "team", "opponent",
    "board_source", "start_time", "slate_date",
    "market_line", "consensus_line",
]

VALID_DIRECTIONS = {"MORE", "LESS", "OVER", "UNDER"}
VALID_SPORTS = {
    "NBA", "NFL", "MLB", "NHL", "WNBA",
    "SOCCER", "TENNIS", "MMA", "PGA",
}


def normalize_row(raw: dict[str, Any], row_index: int = 0) -> dict[str, Any]:
    """
    Normalize one raw board row.
    Returns a canonical dict with:
      - row_id
      - intake_ts
      - data_status  (RETRIEVED | INPUT_FAILURE)
      - intake_errors  list[str]
      - all REQUIRED_FIELDS coerced to clean types
      - all OPTIONAL_FIELDS present (None if absent)
    """
    row_id = raw.get("row_id") or f"row_{row_index}_{uuid.uuid4().hex[:6]}"
    errors: list[str] = []

    player = _clean_str(raw.get("player"))
    sport  = _clean_str(raw.get("sport"), upper=True)
    league = _clean_str(raw.get("league"), upper=True)
    game   = _clean_str(raw.get("game"))
    team   = _clean_str(raw.get("team"), upper=True)
    opponent = _clean_str(raw.get("opponent"), upper=True)

    prop_type = _clean_str(raw.get("prop_type"))
    direction = _clean_str(raw.get("direction"), upper=True)
    board_source = _clean_str(raw.get("board_source")) or "UNKNOWN"
    start_time = _clean_str(raw.get("start_time"))
    slate_date = _clean_str(raw.get("slate_date"))
    market_line = _parse_float(raw.get("market_line"))
    consensus_line = _parse_float(raw.get("consensus_line"))

    line = _parse_float(raw.get("line"))

    if not player:
        errors.append("MISSING:player")
    if not sport:
        errors.append("MISSING:sport")
    elif sport not in VALID_SPORTS:
        errors.append(f"UNKNOWN_SPORT:{sport}")
    if not prop_type:
        errors.append("MISSING:prop_type")
    if line is None:
        errors.append("MISSING:line")
    if not direction:
        errors.append("MISSING:direction")
    elif direction not in VALID_DIRECTIONS:
        errors.append(f"INVALID_DIRECTION:{direction}")

    data_status = DataStatus.INPUT_FAILURE if errors else DataStatus.RETRIEVED

    return {
        "row_id":         row_id,
        "row_index":      row_index,
        "intake_ts":      datetime.now(timezone.utc).isoformat(),
        "data_status":    data_status.value,
        "intake_errors":  errors,
        "player":         player,
        "sport":          sport,
        "league":         league,
        "game":           game,
        "team":           team,
        "opponent":       opponent,
        "prop_type":      prop_type,
        "line":           line,
        "direction":      direction,
        "board_source":   board_source,
        "start_time":     start_time,
        "slate_date":     slate_date,
        "market_line":    market_line,
        "consensus_line": consensus_line,
        "gates":          {},
        "blockers":       [],
        "terminal_label": None,
    }


def normalize_board(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize an entire board. Guarantees every input row is present in output."""
    return [normalize_row(r, i) for i, r in enumerate(rows)]


def _clean_str(val: Any, upper: bool = False) -> str | None:
    if val is None:
        return None
    s = re.sub(r"\s+", " ", str(val).strip())
    return s.upper() if upper else s or None


def _parse_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
