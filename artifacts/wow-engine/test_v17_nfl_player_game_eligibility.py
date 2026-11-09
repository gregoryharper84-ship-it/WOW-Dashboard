"""P0 #2131 game-day availability safety policy; no fitted probability involved."""
from __future__ import annotations

import pytest

from nfl_player_game_eligibility import (
    PLAYER_UNAVAILABLE,
    PLAYER_AVAILABILITY_UNVERIFIED,
    player_game_availability_blocker,
)


@pytest.mark.parametrize(
    "indicator",
    ["OUT", "IR", "injured reserve", "reserve-injured", "INACTIVE",
     "GAME_INACTIVE", "suspended", "PUP", "NFI", "not with team"],
)
def test_explicit_negative_status_blocks_model(indicator: str) -> None:
    assert player_game_availability_blocker(
        {"game_availability_status": indicator}
    ) == PLAYER_UNAVAILABLE


@pytest.mark.parametrize(
    "indicator",
    [None, "", "ACTIVE", "AVAILABLE", "ACTIVE_CURRENT_ESPN_ROSTER",
     "DOUBTFUL", "QUESTIONABLE", "PROBABLE", "CLEARED"],
)
def test_roster_or_unverified_positive_never_grants_scoring(indicator: str | None) -> None:
    assert player_game_availability_blocker(
        {"game_availability_status": indicator, "status": "ACTIVE_CURRENT_ESPN_ROSTER"}
    ) == PLAYER_AVAILABILITY_UNVERIFIED


def test_any_negative_beats_conflicting_caller_active() -> None:
    role = {
        "game_availability_status": "ACTIVE",
        "injury_status": "injured reserve",
        "availability_status": "AVAILABLE",
        "status": "ACTIVE_CURRENT_ESPN_ROSTER",
    }
    assert player_game_availability_blocker(role) == PLAYER_UNAVAILABLE


@pytest.mark.parametrize("role", [None, {}, [], "ACTIVE", {"game_availability_status": {"status": "ACTIVE"}}])
def test_missing_or_malformed_inputs_are_unknown(role) -> None:
    assert player_game_availability_blocker(role) == PLAYER_AVAILABILITY_UNVERIFIED


def test_hurts_october_26_roster_only_receipt_blocks() -> None:
    # Real 2026-10-26 Daily role fields; copied as identity-only metadata.
    role = {
        "status": "ACTIVE_CURRENT_ESPN_ROSTER",
        "player": "Jalen Hurts",
        "espn_athlete_id": "4040715",
        "event_id": "401873009",
        "verified_canonical_event_id": "2026_07_DAL_PHI",
        "identity_binding_status": "PASS",
    }
    assert player_game_availability_blocker(role) == PLAYER_AVAILABILITY_UNVERIFIED
