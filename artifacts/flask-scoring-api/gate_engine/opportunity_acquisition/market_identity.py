"""
market_identity.py — Exact market identity canonicalization and comparison.

MarketIdentity records the 10 fields that uniquely identify a PrizePicks board
line.  compare_identity() returns EXACT / ADJACENT / PROXY / INCOMPATIBLE.

  EXACT:        all 10 fields match
  ADJACENT:     all fields match except exact_line, which differs by ≤0.5
  PROXY:        stat_family matches but other fields differ
  INCOMPATIBLE: stat_family mismatch or both are None
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from gate_engine.component_composite import STAT_FAMILY_ALIASES


# ---------------------------------------------------------------------------
# Identity match enum
# ---------------------------------------------------------------------------

class IdentityMatch(str, Enum):
    EXACT         = "EXACT"
    ADJACENT      = "ADJACENT"
    PROXY         = "PROXY"
    INCOMPATIBLE  = "INCOMPATIBLE"


# ---------------------------------------------------------------------------
# MarketIdentity dataclass
# ---------------------------------------------------------------------------

@dataclass
class MarketIdentity:
    """
    All 10 fields required to uniquely identify a market for WOW line comparison.
    None in any required field means the value was not obtainable.
    """
    platform:           str | None = None   # "prizepicks" / "draftkings" / ...
    participant_id:     str | None = None   # canonical player name (lowercased)
    event_id:           str | None = None   # game identifier
    event_date:         str | None = None   # YYYY-MM-DD
    period:             str | None = None   # "full_game" / "first_half" / "first_quarter"
    stat_family:        str | None = None   # canonical alias from STAT_FAMILY_ALIASES
    exact_line:         float | None = None # e.g. 24.5
    side:               str | None = None   # "more" / "less"
    settlement_basis:   str | None = None   # "official_box_score" / ...
    overtime_treatment: str | None = None   # "included" / "excluded"
    push_void_behavior: str | None = None   # "push" / "void" / "action"

    can_execute: bool = False   # unconditional

    def to_dict(self) -> dict[str, Any]:
        return {
            "can_execute":         False,
            "platform":            self.platform,
            "participant_id":      self.participant_id,
            "event_id":            self.event_id,
            "event_date":          self.event_date,
            "period":              self.period,
            "stat_family":         self.stat_family,
            "exact_line":          self.exact_line,
            "side":                self.side,
            "settlement_basis":    self.settlement_basis,
            "overtime_treatment":  self.overtime_treatment,
            "push_void_behavior":  self.push_void_behavior,
        }


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------

def canonicalize(raw_market: dict[str, Any]) -> MarketIdentity:
    """
    Normalize a raw market dict into a MarketIdentity.

    Handles common field-name variations from different sportsbooks / vendors.
    """
    def _str(v: Any) -> str | None:
        if v is None:
            return None
        s = str(v).strip().lower()
        return s if s else None

    def _float(v: Any) -> float | None:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # Stat family normalization
    raw_stat = (
        raw_market.get("stat_family")
        or raw_market.get("prop_type")
        or raw_market.get("stat")
        or raw_market.get("market_type")
        or ""
    )
    stat_family = STAT_FAMILY_ALIASES.get(raw_stat.lower().strip()) if raw_stat else None

    # Platform normalization
    raw_platform = _str(raw_market.get("platform") or raw_market.get("book") or raw_market.get("sportsbook"))
    _PLATFORM_MAP = {
        "pp": "prizepicks", "prize_picks": "prizepicks",
        "dk": "draftkings", "fd": "fanduel",
        "espn": "espn_bet", "mgm": "betmgm",
    }
    platform = _PLATFORM_MAP.get(raw_platform or "", raw_platform)

    # Period normalization
    raw_period = _str(
        raw_market.get("period") or raw_market.get("game_period") or raw_market.get("time_period")
    )
    _PERIOD_MAP = {
        "full": "full_game", "game": "full_game", "fullgame": "full_game",
        "1h": "first_half", "first_half": "first_half",
        "1q": "first_quarter", "q1": "first_quarter",
    }
    period = _PERIOD_MAP.get(raw_period or "", raw_period or "full_game")

    # Side normalization
    raw_side = _str(raw_market.get("side") or raw_market.get("direction") or raw_market.get("pick"))
    _SIDE_MAP = {
        "over": "more", "o": "more", "higher": "more", "above": "more",
        "under": "less", "u": "less", "lower": "less", "below": "less",
    }
    side = _SIDE_MAP.get(raw_side or "", raw_side)

    return MarketIdentity(
        platform=platform,
        participant_id=_str(
            raw_market.get("participant_id")
            or raw_market.get("player")
            or raw_market.get("player_name")
        ),
        event_id=_str(
            raw_market.get("event_id")
            or raw_market.get("game_id")
            or raw_market.get("event")
        ),
        event_date=_str(
            raw_market.get("event_date")
            or raw_market.get("game_date")
            or raw_market.get("date")
        ),
        period=period,
        stat_family=stat_family,
        exact_line=_float(
            raw_market.get("exact_line")
            or raw_market.get("line")
            or raw_market.get("threshold")
        ),
        side=side,
        settlement_basis=_str(
            raw_market.get("settlement_basis") or raw_market.get("settled_by")
        ) or "official_box_score",
        overtime_treatment=_str(
            raw_market.get("overtime_treatment") or raw_market.get("ot")
        ) or "included",
        push_void_behavior=_str(
            raw_market.get("push_void_behavior")
            or raw_market.get("push_behavior")
            or raw_market.get("push")
        ) or "push",
    )


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

_ADJACENT_LINE_TOLERANCE = 0.5  # lines may differ by at most 0.5 for ADJACENT

@dataclass
class IdentityMatchResult:
    match:       IdentityMatch
    explanation: str
    can_execute: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "can_execute": False,
            "match":       self.match.value,
            "explanation": self.explanation,
        }


def compare_identity(
    board_identity: MarketIdentity,
    sportsbook_identity: MarketIdentity,
) -> IdentityMatchResult:
    """
    Compare a PrizePicks board identity to a sportsbook/evidence identity.

    Returns IdentityMatchResult with EXACT / ADJACENT / PROXY / INCOMPATIBLE.
    """
    # Stat family must match; if either is None → INCOMPATIBLE
    if board_identity.stat_family is None or sportsbook_identity.stat_family is None:
        return IdentityMatchResult(
            IdentityMatch.INCOMPATIBLE,
            "INCOMPATIBLE: stat_family is None in one or both identities",
        )
    if board_identity.stat_family != sportsbook_identity.stat_family:
        return IdentityMatchResult(
            IdentityMatch.INCOMPATIBLE,
            f"INCOMPATIBLE: stat_family mismatch "
            f"({board_identity.stat_family} vs {sportsbook_identity.stat_family})",
        )

    # -----------------------------------------------------------------------
    # Fail-closed: required fields must be present in BOTH identities for an
    # EXACT or ADJACENT result.  A missing participant, date, side, or line
    # means we cannot confirm the identities refer to the same market —
    # return PROXY rather than silently confirming an unverifiable match.
    # -----------------------------------------------------------------------
    _REQUIRED_FOR_EXACT: list[str] = ["participant_id", "event_date", "side", "exact_line"]
    for _rf in _REQUIRED_FOR_EXACT:
        _v_board = getattr(board_identity, _rf)
        _v_sb    = getattr(sportsbook_identity, _rf)
        if _v_board is None or _v_sb is None:
            return IdentityMatchResult(
                IdentityMatch.PROXY,
                f"PROXY: required field '{_rf}' is None in one or both identities; "
                "cannot confirm EXACT/ADJACENT match (fail-closed on missing data)",
            )

    # -----------------------------------------------------------------------
    # Non-line structural fields — platform excluded (board vs sportsbook
    # always differ on platform; structural fields drive the classification).
    # Non-required fields that are None in either identity are skipped.
    # -----------------------------------------------------------------------
    _NON_LINE_FIELDS = [
        "participant_id", "event_id", "event_date",
        "period", "side", "settlement_basis", "overtime_treatment", "push_void_behavior",
    ]
    mismatches: list[str] = []
    for fname in _NON_LINE_FIELDS:
        v_board = getattr(board_identity, fname)
        v_sb    = getattr(sportsbook_identity, fname)
        if v_board is None or v_sb is None:
            continue   # non-required fields: absent ≠ mismatch
        if str(v_board).lower() != str(v_sb).lower():
            mismatches.append(f"{fname}: {v_board!r} vs {v_sb!r}")

    # exact_line guaranteed non-None for both (required field check above)
    line_diff: float = abs(board_identity.exact_line - sportsbook_identity.exact_line)  # type: ignore[operator]

    if not mismatches and line_diff == 0.0:
        return IdentityMatchResult(
            IdentityMatch.EXACT,
            "EXACT: all identity fields match",
        )

    if not mismatches and line_diff <= _ADJACENT_LINE_TOLERANCE:
        return IdentityMatchResult(
            IdentityMatch.ADJACENT,
            f"ADJACENT: all fields match; line differs by {line_diff:.2f} (≤{_ADJACENT_LINE_TOLERANCE})",
        )

    if not mismatches and line_diff > _ADJACENT_LINE_TOLERANCE:
        return IdentityMatchResult(
            IdentityMatch.PROXY,
            f"PROXY: stat_family matches; line differs by {line_diff:.2f} (>{_ADJACENT_LINE_TOLERANCE})",
        )

    # Non-line field mismatches → PROXY (same stat family, different event/period/etc.)
    return IdentityMatchResult(
        IdentityMatch.PROXY,
        "PROXY: stat_family matches but other fields differ: " + "; ".join(mismatches[:3]),
    )
