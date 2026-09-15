from __future__ import annotations

import pytest

from v17.team_event_specialist_ownership import (
    FIRST_SIX_TEAM_EVENT_OWNERS,
    owner_for,
    validate_unique_ownership,
)


EXPECTED = {
    "NCAAF": "wow.ncaaf-game-win-probability-expert",
    "NBA": "wow.nba-game-win-probability-expert",
    "WNBA": "wow.wnba-game-win-probability-expert",
    "NCAAB": "wow.ncaab-game-win-probability-expert",
    "SOCCER": "wow.soccer-match-win-probability-expert",
    "TENNIS": "wow.tennis-match-win-probability-expert",
}


def test_first_six_have_exact_unique_owners():
    validate_unique_ownership()
    assert set(FIRST_SIX_TEAM_EVENT_OWNERS) == set(EXPECTED)
    for sport, specialist in EXPECTED.items():
        owner = owner_for(sport)
        assert owner.controlling_specialist == specialist
        assert owner.market_family == "OUTRIGHT_WINNER"
        assert owner.probability_publishable is False
        assert owner.can_execute is False


def test_ownership_does_not_create_unknown_capability():
    with pytest.raises(ValueError):
        owner_for("NHL")
