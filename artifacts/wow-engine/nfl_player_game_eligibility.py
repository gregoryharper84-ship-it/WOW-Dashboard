"""Fail-closed NFL player game-eligibility preflight (P0 #2131).

This is deliberately *not* an availability provider: roster membership,
historical stats and arbitrary caller evidence cannot certify a player's
participation in a particular game. Until an independently reviewed,
event-specific source is integrated at the trusted server boundary, every
NFL prop is blocked before invoking a fitted specialist.

Negative statuses may conservatively invalidate a candidate. Ambiguous injury
designations (including DOUBTFUL) remain unverified rather than being asserted
as officially inactive. No positive caller-supplied string grants scoring.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

PLAYER_UNAVAILABLE = "PLAYER_UNAVAILABLE"
PLAYER_AVAILABILITY_UNVERIFIED = "PLAYER_AVAILABILITY_UNVERIFIED"

_EXPLICIT_UNAVAILABLE = frozenset({
    "OUT", "IR", "INJURED_RESERVE", "RESERVE_INJURED",
    "INACTIVE", "GAME_INACTIVE", "SUSPENDED", "RESERVE_SUSPENDED",
    "PUP", "RESERVE_PUP", "NFI", "RESERVE_NFI", "NOT_WITH_TEAM",
})

_STATUS_FIELDS = (
    "game_availability_status", "availability_status", "injury_status",
    "game_status", "designation",
)


def player_game_availability_blocker(role_status: Any) -> str:
    """Return a *blocking* type for NFL props; never authorize scoring.

    Consume every supplied negative indicator (rather than trusting an ACTIVE
    field to override IR). The source-of-truth positive check belongs to a
    future trusted, event-specific provider. A direct Action caller must not
    self-attest or override it through an evidence JSON field.
    """
    role: Mapping[str, Any] = role_status if isinstance(role_status, Mapping) else {}
    for key in _STATUS_FIELDS:
        value = role.get(key)
        if not isinstance(value, str):
            continue
        status = "_".join(value.strip().upper().replace("-", " ").split())
        if status in _EXPLICIT_UNAVAILABLE:
            return PLAYER_UNAVAILABLE
    return PLAYER_AVAILABILITY_UNVERIFIED


__all__ = [
    "PLAYER_UNAVAILABLE",
    "PLAYER_AVAILABILITY_UNVERIFIED",
    "player_game_availability_blocker",
]
