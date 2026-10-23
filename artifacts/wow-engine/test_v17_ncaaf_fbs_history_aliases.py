"""Regression tests for #1636 / #1651 (Oct 14 NCAAF SPREAD_FORWARD_HISTORY_INSUFFICIENT).

The fix is identity resolution only; these tests also pin that the feature
builder's chronology and five-game floor are unchanged.
"""
import pytest

from v17.ncaaf_fbs_school_aliases import (
    FBS_DISPLAY_ALIASES,
    FBS_SCHOOLS,
    FBSAliasTableError,
    _norm,
    merge_verified_aliases,
)
from v17.spread_forward_shadow import (
    SpreadChallengerUnavailable,
    _resolve_history_team,
    build_forward_matchup_features,
)

TARGET_START = "2026-10-14T23:30:00+00:00"


def _history(team, n=6, *, day0=5, month=9, home=True):
    rows = []
    for i in range(n):
        h, a = (team, f"Opp-{team}-{i}") if home else (f"Foe-{team}-{i}", team)
        rows.append({
            "event_id": f"{team}-{month}-{i}",
            "event_start_time": f"2026-{month:02d}-{day0 + i:02d}T18:00:00+00:00",
            "season": 2026, "home_team": h, "away_team": a,
            "home_score": 27, "away_score": 20,
        })
    return rows


def _target(home, away):
    return {"event_id": "oct14", "event_start_time": TARGET_START,
            "home_team": home, "away_team": away, "season": 2026}


# --- exact October 14 incident matchups -----------------------------------

@pytest.mark.parametrize("cfbd_home,cfbd_away,provider_home,provider_away", [
    ("Missouri State", "Kennesaw State", "Missouri State Bears", "Kennesaw State Owls"),
    ("Sam Houston", "Western Kentucky", "Sam Houston Bearkats", "Western Kentucky Hilltoppers"),
])
def test_oct14_incident_matchups_recover_prior_history(cfbd_home, cfbd_away, provider_home, provider_away):
    events = _history(cfbd_home) + _history(cfbd_away, home=False)
    features, audit = build_forward_matchup_features(events, target_event=_target(provider_home, provider_away))
    assert audit["home_prior_events"] == 6 and audit["away_prior_events"] == 6
    assert features and all(isinstance(v, float) for v in features.values())
    assert audit["market_features_used"] is False
    assert audit["can_execute"] is False


# --- renamed schools --------------------------------------------------------

@pytest.mark.parametrize("history_key", ["Sam Houston", "Sam Houston State"])
def test_renamed_school_resolves_to_whichever_single_spelling_has_history(history_key):
    known = (history_key, "Houston", "Western Kentucky")
    assert _resolve_history_team("Sam Houston Bearkats", known) == history_key
    assert _resolve_history_team("Sam Houston State Bearkats", known) == history_key


def test_split_renamed_history_fails_closed():
    known = ("Sam Houston", "Sam Houston State", "Houston")
    assert _resolve_history_team("Sam Houston Bearkats", known) == "Sam Houston Bearkats"


@pytest.mark.parametrize("incoming,known_key", [
    ("App State Mountaineers", "Appalachian State"),
    ("App State", "Appalachian State"),
    ("UConn Huskies", "Connecticut"),
    ("UMass Minutemen", "Massachusetts"),
    ("Southern Miss Golden Eagles", "Southern Mississippi"),
    ("FIU Panthers", "Florida International"),
    ("UL Monroe Warhawks", "UL Monroe"),
    ("Louisiana Ragin' Cajuns", "Louisiana"),
    ("Hawai'i Rainbow Warriors", "Hawai'i"),
    ("San José State Spartans", "San José State"),
    ("Ole Miss Rebels", "Ole Miss"),
    ("Miami (OH) RedHawks", "Miami (OH)"),
])
def test_provider_school_spellings_resolve_to_cfbd_key(incoming, known_key):
    known = (known_key, "Louisiana Tech", "Miami", "Houston", "Washington")
    assert _resolve_history_team(incoming, known) == known_key


# --- negative paths: no prefix borrowing, no abbreviation guessing ---------

@pytest.mark.parametrize("incoming,forbidden", [
    ("Western Kentucky Hilltoppers", "Kentucky"),
    ("Kennesaw State Owls", "Kennesaw"),
    ("Missouri State Bears", "Missouri"),
    ("Sam Houston Bearkats", "Houston"),
    ("Middle Tennessee Blue Raiders", "Tennessee"),
    ("Louisiana Tech Bulldogs", "Louisiana"),
    ("Houston Christian Huskies", "Houston"),
    ("Missouri St Bears", "Missouri"),
    ("W Kentucky", "Kentucky"),
    ("Miami RedHawks", "Miami"),
])
def test_school_absent_from_history_never_borrows_a_prefix_school(incoming, forbidden):
    known = ("Kentucky", "Kennesaw", "Missouri", "Houston", "Tennessee", "Louisiana", "Miami")
    assert _resolve_history_team(incoming, known) != forbidden


@pytest.mark.parametrize("incoming", ["Missouri St Bears", "WKU Hilltoppers", "Kennesaw St", "Sam Houston St"])
def test_unlisted_abbreviations_stay_unresolved_and_fail_closed(incoming):
    known = ("Missouri State", "Western Kentucky", "Kennesaw State", "Sam Houston")
    assert _resolve_history_team(incoming, known) == incoming


def test_unresolved_team_fails_closed_end_to_end_with_typed_code():
    events = _history("Missouri State") + _history("Kennesaw State", home=False)
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        build_forward_matchup_features(events, target_event=_target("Missouri St Bears", "Kennesaw State Owls"))
    assert exc.value.code == "SPREAD_FORWARD_HISTORY_INSUFFICIENT"


# --- chronology and five-game floor are unchanged ---------------------------

def test_future_games_are_never_used_after_alias_resolution():
    # 4 prior games + 6 games after kickoff: the after-kickoff games must not count.
    events = (
        _history("Missouri State", n=4)
        + _history("Missouri State", n=6, month=11)
        + _history("Kennesaw State", home=False)
    )
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        build_forward_matchup_features(events, target_event=_target("Missouri State Bears", "Kennesaw State Owls"))
    assert exc.value.code == "SPREAD_FORWARD_HISTORY_INSUFFICIENT"
    assert "home=4" in str(exc.value)


def test_five_game_minimum_is_not_bypassed():
    events = _history("Sam Houston", n=4) + _history("Western Kentucky", home=False)
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        build_forward_matchup_features(events, target_event=_target("Sam Houston Bearkats", "Western Kentucky Hilltoppers"))
    assert exc.value.code == "SPREAD_FORWARD_HISTORY_INSUFFICIENT"
    events = _history("Sam Houston", n=5) + _history("Western Kentucky", home=False)
    _, audit = build_forward_matchup_features(events, target_event=_target("Sam Houston Bearkats", "Western Kentucky Hilltoppers"))
    assert audit["home_prior_events"] == 5


def test_alias_and_canonical_for_same_school_is_identity_invalid():
    events = _history("Kennesaw State") + _history("Missouri State", home=False)
    with pytest.raises(SpreadChallengerUnavailable) as exc:
        build_forward_matchup_features(events, target_event=_target("Kennesaw State Owls", "Kennesaw State"))
    assert exc.value.code == "SPREAD_FORWARD_EVENT_IDENTITY_INVALID"


# --- table integrity ---------------------------------------------------------

def test_table_has_no_duplicate_school_or_alias_collisions():
    schools = [_norm(names[0]) for names, _ in FBS_SCHOOLS]
    assert len(schools) == len(set(schools))
    for alias, school in FBS_DISPLAY_ALIASES.items():
        owners = [names[0] for names, mascot in FBS_SCHOOLS
                  if alias in {_norm(f"{n} {mascot}") for n in names} | {_norm(n) for n in names[1:]}]
        assert owners == [school], alias


def test_every_fbs_school_has_its_display_alias():
    for names, mascot in FBS_SCHOOLS:
        assert FBS_DISPLAY_ALIASES[_norm(f"{names[0]} {mascot}")] == names[0]


def test_conflicting_merge_raises_instead_of_borrowing_history():
    with pytest.raises(FBSAliasTableError):
        merge_verified_aliases({"kentuckywildcats": "Kentucky"}, {"kentuckywildcats": "Kansas State"})
    assert merge_verified_aliases({"utahutes": "utah"}, {"utahutes": "Utah"}) == {"utahutes": "utah"}
