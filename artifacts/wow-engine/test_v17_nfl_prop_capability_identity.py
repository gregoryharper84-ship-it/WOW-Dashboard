from prop_fitted_provider import capability_key, canonical_prop_period


def test_exact_nfl_player_prop_capability_identity_is_five_fields():
    key = capability_key(
        sport="nfl",
        league="NFL",
        market_family="player_prop",
        stat_type="passing_yards",
        period="full_game",
    )
    assert (
        key.sport,
        key.league,
        key.market_family,
        key.stat_type,
        key.period,
    ) == ("NFL", "NFL", "PLAYER_PROP", "PASSING_YARDS", "FULL_GAME")


def test_first_inning_period_normalization_preserves_live_1st_inning_spelling():
    assert canonical_prop_period("PITCH_COUNT_1IP") == "FIRST_INNING"
    assert canonical_prop_period("FIRST_INNING_PITCHES_THROWN") == "FIRST_INNING"
    assert canonical_prop_period("1ST_INNING_PITCHES_THROWN") == "FIRST_INNING"
    assert canonical_prop_period("PITCHER_STRIKEOUTS") == "FULL_GAME"
