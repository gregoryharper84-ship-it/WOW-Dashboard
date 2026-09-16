from __future__ import annotations

import pytest

from v17.team_event_specialist_ownership import (
    BASELINE_TEAM_EVENT_OWNERS,
    FIRST_SIX_TEAM_EVENT_OWNERS,
    REMAINING_FOUR_TEAM_EVENT_OWNERS,
    owner_for,
    validate_unique_ownership,
)


FIRST_SIX_EXPECTED = {
    "NCAAF": "wow.ncaaf-game-win-probability-expert",
    "NBA": "wow.nba-game-win-probability-expert",
    "WNBA": "wow.wnba-game-win-probability-expert",
    "NCAAB": "wow.ncaab-game-win-probability-expert",
    "SOCCER": "wow.soccer-match-win-probability-expert",
    "TENNIS": "wow.tennis-match-win-probability-expert",
}

REMAINING_FOUR_EXPECTED = {
    "NHL": "wow.nhl-game-win-probability-expert",
    "GOLF": "wow.golf-event-win-probability-expert",
    "MMA": "wow.mma-fight-win-probability-expert",
    "BOXING": "wow.boxing-fight-win-probability-expert",
}

BASELINE_EXPECTED = {
    "MLB": "wow.mlb-game-win-probability-expert",
    "NFL": "wow.nfl-game-win-probability-expert",
    **FIRST_SIX_EXPECTED,
    **REMAINING_FOUR_EXPECTED,
}


def _assert_owner(sport: str, specialist: str) -> None:
    owner = owner_for(sport)
    assert owner.controlling_specialist == specialist
    assert owner.market_family == "OUTRIGHT_WINNER"
    assert owner.probability_publishable is False
    assert owner.can_execute is False


def test_first_six_have_exact_unique_owners():
    validate_unique_ownership()
    assert set(FIRST_SIX_TEAM_EVENT_OWNERS) == set(FIRST_SIX_EXPECTED)
    for sport, specialist in FIRST_SIX_EXPECTED.items():
        _assert_owner(sport, specialist)


def test_remaining_four_have_explicit_nonpublishing_owners():
    assert set(REMAINING_FOUR_TEAM_EVENT_OWNERS) == set(REMAINING_FOUR_EXPECTED)
    for sport, specialist in REMAINING_FOUR_EXPECTED.items():
        _assert_owner(sport, specialist)


def test_baseline_inventory_has_twelve_unique_owned_sports():
    validate_unique_ownership()
    assert BASELINE_TEAM_EVENT_OWNERS.keys() == BASELINE_EXPECTED.keys()
    assert len(BASELINE_TEAM_EVENT_OWNERS) == 12
    for sport, specialist in BASELINE_EXPECTED.items():
        _assert_owner(sport, specialist)


def test_ownership_does_not_create_unknown_capability():
    with pytest.raises(ValueError):
        owner_for("CRICKET")
