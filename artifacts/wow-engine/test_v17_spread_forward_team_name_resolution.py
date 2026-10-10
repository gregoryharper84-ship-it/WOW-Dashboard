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


def test_school_without_own_history_is_never_mapped_to_a_prefix_school():
    known = ("Washington", "Texas", "Miami", "Oklahoma", "Florida")
    # Each of these is a different school that merely lacks history here.
    assert _resolve_history_team("Washington State Cougars", known) == "Washington State Cougars"
    assert _resolve_history_team("Texas State Bobcats", known) == "Texas State Bobcats"
    assert _resolve_history_team("Texas Tech Red Raiders", known) == "Texas Tech Red Raiders"
    assert _resolve_history_team("Miami (OH) RedHawks", known) == "Miami (OH) RedHawks"
    assert _resolve_history_team("Florida Atlantic Owls", known) == "Florida Atlantic Owls"
    assert _resolve_history_team("Oklahoma State Cowboys", known) == "Oklahoma State Cowboys"
    # A genuine mascot still resolves.
    assert _resolve_history_team("Washington Huskies", known) == "Washington"


def test_prefix_school_history_is_not_used_for_absent_school():
    events = _events("Texas", "Oklahoma")
    target = {"event_id": "t", "event_start_time": "2026-10-10T17:00:00+00:00",
              "home_team": "Texas State Bobcats", "away_team": "Oklahoma Sooners", "season": 2026}
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        build_forward_matchup_features(events, target_event=target)
    assert exc.value.code == "SPREAD_FORWARD_HISTORY_INSUFFICIENT"


@pytest.mark.parametrize("incoming,forbidden", [
    ("Kansas St Wildcats", "Kansas"),
    ("Michigan St Spartans", "Michigan"),
    ("Oklahoma St Cowboys", "Oklahoma"),
    ("Washington St Cougars", "Washington"),
    ("Ohio St Buckeyes", "Ohio"),
    ("Miami OH RedHawks", "Miami"),
    ("Miami RedHawks", "Miami"),
    ("Kansas St", "Kansas"),
    ("Michigan St", "Michigan"),
])
def test_abbreviated_or_ambiguous_school_never_uses_wrong_history(incoming, forbidden):
    known = ("Kansas", "Kansas State", "Michigan", "Michigan State",
             "Oklahoma", "Oklahoma State", "Washington", "Washington State",
             "Ohio", "Ohio State", "Miami", "Miami (OH)")
    assert _resolve_history_team(incoming, known) != forbidden


def test_verified_short_school_mascots_resolve_without_prefix_guessing():
    known = ("UCLA", "Utah", "Iowa", "Iowa State", "Duke", "Ohio", "Ohio State")
    for incoming, expected in (
        ("UCLA Bruins", "UCLA"),
        ("Utah Utes", "Utah"),
        ("Iowa Hawkeyes", "Iowa"),
        ("Duke Blue Devils", "Duke"),
        ("Ohio Bobcats", "Ohio"),
    ):
        assert _resolve_history_team(incoming, known) == expected
    assert _resolve_history_team("Iowa State Cyclones", known) != "Iowa"


def test_unverified_team_name_does_not_borrow_prefix_history_end_to_end():
    events = _events("Kansas", "Oklahoma")
    target = {"event_id": "t", "event_start_time": "2026-10-10T17:00:00+00:00",
              "home_team": "Kansas St Wildcats", "away_team": "Oklahoma Sooners",
              "season": 2026}
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        build_forward_matchup_features(events, target_event=target)
    assert exc.value.code == "SPREAD_FORWARD_HISTORY_INSUFFICIENT"


def test_normalized_exact_name_collision_is_rejected():
    # Two distinct CFBD keys can normalize to the same value.
    known = ("Miami (OH)", "Miami OH")
    assert _resolve_history_team("miami-oh", known) == "miami-oh"


def test_ambiguous_verified_alias_does_not_select_arbitrary_history_key():
    # The verified alias must identify exactly one history key.
    known = ("Texas", "Téxas")
    assert _resolve_history_team("Texas Longhorns", known) == "Texas Longhorns"


@pytest.mark.parametrize("incoming,school", [
    ("Oregon Ducks", "Oregon"),
    ("Houston Cougars", "Houston"),
    ("Illinois Fighting Illini", "Illinois"),
])
def test_all_previously_unresolved_saturday_provider_school_names(incoming, school):
    known = ("Oregon", "Oregon State", "Houston", "Illinois", "Illinois State")
    assert _resolve_history_team(incoming, known) == school


@pytest.mark.parametrize("canon_home,canon_away,provider_home,provider_away", [
    ("Texas", "Oklahoma", "Texas Longhorns", "Oklahoma Sooners"),
    ("Oregon", "UCLA", "Oregon Ducks", "UCLA Bruins"),
    ("Kansas State", "Houston", "Kansas State Wildcats", "Houston Cougars"),
    ("Michigan State", "Illinois", "Michigan State Spartans", "Illinois Fighting Illini"),
])
def test_all_four_incident_spread_matchups_find_real_history(
    canon_home, canon_away, provider_home, provider_away
):
    events = _events(canon_home, canon_away)
    target = {
        "event_id": "strict-evidence-preview",
        "event_start_time": "2026-10-10T17:00:00+00:00",
        "home_team": provider_home,
        "away_team": provider_away,
        "season": 2026,
    }
    features, audit = build_forward_matchup_features(events, target_event=target)
    assert features and all(isinstance(v, float) for v in features.values())


@pytest.mark.parametrize("incoming", [
    "Oregon State Beavers", "Houston Christian Huskies",
    "Illinois State Redbirds", "Oregn Ducks",
])
def test_new_aliases_do_not_steal_similar_school_history(incoming):
    known = ("Oregon", "Houston", "Illinois")
    assert _resolve_history_team(incoming, known) == incoming


def test_new_alias_does_not_choose_ambiguous_canonical_history_key():
    assert _resolve_history_team("Oregon Ducks", ("Oregon", "Orégon")) == "Oregon Ducks"
