import pytest

from v17.spread_forward_shadow import (
    SpreadChallengerUnavailable,
    _resolve_history_team,
    build_forward_matchup_features,
)

TEAMS = ("Texas", "Texas Tech", "Texas A&M", "Oklahoma", "Oklahoma State", "Miami", "Miami (OH)", "Ohio")


def test_exact_name_always_wins():
    assert _resolve_history_team("Texas", TEAMS) == "Texas"
    assert _resolve_history_team("Texas Tech", TEAMS) == "Texas Tech"


def test_mascot_suffix_resolves_to_longest_school_not_shorter_prefix():
    assert _resolve_history_team("Texas Longhorns", TEAMS) == "Texas"
    assert _resolve_history_team("Texas Tech Red Raiders", TEAMS) == "Texas Tech"
    assert _resolve_history_team("Oklahoma Sooners", TEAMS) == "Oklahoma"
    assert _resolve_history_team("Oklahoma State Cowboys", TEAMS) == "Oklahoma State"
    assert _resolve_history_team("Miami (OH) RedHawks", TEAMS) == "Miami (OH)"
    assert _resolve_history_team("Miami Hurricanes", TEAMS) == "Miami"


def test_unknown_or_short_names_are_never_guessed():
    assert _resolve_history_team("Utah Utes", TEAMS) == "Utah Utes"
    assert _resolve_history_team("Ohi", TEAMS) == "Ohi"
    # "Ohio" (4 chars) is below the 5-character floor, so "Ohio State Buckeyes" is not Ohio
    assert _resolve_history_team("Ohio State Buckeyes", TEAMS) == "Ohio State Buckeyes"


def _events(home, away, n=6):
    rows = []
    for i in range(n):
        rows.append({
            "event_id": f"a{i}", "event_start_time": f"2026-09-0{i + 1}T18:00:00+00:00",
            "season": 2026, "home_team": home, "away_team": f"Opp{i}",
            "home_score": 30, "away_score": 20,
        })
        rows.append({
            "event_id": f"b{i}", "event_start_time": f"2026-09-0{i + 1}T20:00:00+00:00",
            "season": 2026, "home_team": f"Foe{i}", "away_team": away,
            "home_score": 17, "away_score": 24,
        })
    return rows


def test_mascot_named_request_now_finds_history_instead_of_zero():
    events = _events("Texas", "Oklahoma")
    target = {"event_id": "t", "event_start_time": "2026-10-10T17:00:00+00:00",
              "home_team": "Oklahoma Sooners", "away_team": "Texas Longhorns", "season": 2026}
    features, audit = build_forward_matchup_features(events, target_event=target)
    assert features and all(isinstance(v, float) for v in features.values())


def test_truly_unknown_team_still_fails_closed_with_typed_code():
    events = _events("Texas", "Oklahoma")
    target = {"event_id": "t", "event_start_time": "2026-10-10T17:00:00+00:00",
              "home_team": "Nowhere State Wolves", "away_team": "Texas Longhorns", "season": 2026}
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        build_forward_matchup_features(events, target_event=target)
    assert exc.value.code == "SPREAD_FORWARD_HISTORY_INSUFFICIENT"


def test_both_names_resolving_to_same_school_is_rejected():
    events = _events("Texas", "Oklahoma")
    target = {"event_id": "t", "event_start_time": "2026-10-10T17:00:00+00:00",
              "home_team": "Texas Longhorns", "away_team": "Texas", "season": 2026}
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        build_forward_matchup_features(events, target_event=target)
    assert exc.value.code == "SPREAD_FORWARD_EVENT_IDENTITY_INVALID"
