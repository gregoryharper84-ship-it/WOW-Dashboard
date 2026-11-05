"""SI-001..SI-013: SOCCER identity is competition-scoped and truly three-way.
"""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
import re

import pytest

from v17 import soccer_event_identity as identity
from v17.cross_sport_winner_discovery import DiscoveredEvent
from v17.team_event_governance_profiles import TEAM_EVENT_GOVERNANCE_PROFILES
from v17.team_event_sport_parity import canonicalize_soccer_discovery_identity

EVENT_AT = "2026-11-07T12:30:00Z"


def scoreboard_event(
    event_id="401878758",
    *,
    date=EVENT_AT,
    home=("357", "Leeds United", "Leeds"),
    away=("367", "Tottenham Hotspur", "Spurs"),
):
    def team(values):
        return {"id": values[0], "displayName": values[1], "shortDisplayName": values[2]}

    return {
        "id": event_id,
        "date": date,
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "team": team(home)},
                    {"homeAway": "away", "team": team(away)},
                ]
            }
        ],
    }


def fetcher_for(rows_by_slug):
    def fetch(competition, date_key):
        return list(rows_by_slug.get((competition, date_key), []))

    return fetch


@pytest.fixture(autouse=True)
def _clear_cache():
    identity.reset_cache()
    yield
    identity.reset_cache()


def _resolve(**overrides):
    kwargs = {
        "competition": "soccer_epl",
        "event_start_time": EVENT_AT,
        "home_team": "Leeds United",
        "away_team": "Tottenham Hotspur",
    }
    kwargs.update(overrides)
    return identity.resolve_soccer_current_event_identity(**kwargs)


def test_si_001_unique_competition_scoped_match_assigns_canonical_id():
    out = _resolve(
        fetcher=fetcher_for({("eng.1", "20261107"): [scoreboard_event()]})
    )
    assert out["event_id"] == "SOCCER:401878758"
    assert out["competition"] == "eng.1"
    assert out["home_team_id"] == "espn-357"
    assert out["away_team_id"] == "espn-367"
    assert out["identity_provider"] == "ESPN_SOCCER_SCOREBOARD"
    assert out["market_features_used"] is False
    assert out["prediction_authority"] is False
    assert out["can_execute"] is False


def test_si_002_outcome_space_is_three_way_and_matches_the_governance_profile():
    out = _resolve(
        fetcher=fetcher_for({("eng.1", "20261107"): [scoreboard_event()]})
    )
    profile = TEAM_EVENT_GOVERNANCE_PROFILES["SOCCER"]
    assert profile.outcome_space == "HOME_DRAW_AWAY"
    assert out["outcome_space"] == identity.OUTCOME_SPACE == "HOME_DRAW_AWAY"
    assert out["draw_possible"] is identity.DRAW_POSSIBLE is True
    assert out["settlement_contract"] == profile.settlement_contract
    assert "THREE_WAY" in profile.settlement_contract


def test_si_003_identity_never_reports_a_winner_or_collapses_the_draw():
    out = _resolve(
        fetcher=fetcher_for({("eng.1", "20261107"): [scoreboard_event()]})
    )
    assert out["winner_determined_by_resolver"] is False
    assert "winner" not in out
    assert "home_win" not in out
    assert "probabilities" not in out


def test_si_004_identity_does_not_depend_on_the_score():
    level = scoreboard_event()
    for competitor in level["competitions"][0]["competitors"]:
        competitor["score"] = "1"
    out = _resolve(fetcher=fetcher_for({("eng.1", "20261107"): [level]}))
    assert out["event_id"] == "SOCCER:401878758"
    assert out["draw_possible"] is True


def test_si_005_accent_and_diacritic_forms_resolve_as_identity_only():
    row = scoreboard_event(
        home=("172", "AJ Auxerre", "Auxerre"),
        away=("6851", "Paris FC", "Paris FC"),
    )
    row["competitions"][0]["competitors"][0]["team"]["displayName"] = "CF Montréal"
    row["competitions"][0]["competitors"][1]["team"]["displayName"] = "Vancouver Whitecaps"
    out = _resolve(
        competition="soccer_usa_mls",
        home_team="CF Montreal",
        away_team="Vancouver Whitecaps",
        fetcher=fetcher_for({("usa.1", "20261107"): [row]}),
    )
    assert out["event_id"] == "SOCCER:401878758"
    assert out["competition"] == "usa.1"


def test_si_006_home_and_away_are_not_interchangeable():
    with pytest.raises(identity.SoccerEventIdentityError) as exc:
        _resolve(
            fetcher=fetcher_for(
                {
                    ("eng.1", "20261107"): [
                        scoreboard_event(home=("367", "Tottenham Hotspur", "Spurs"),
                                         away=("357", "Leeds United", "Leeds"))
                    ]
                }
            )
        )
    assert exc.value.code == "SOCCER_CANONICAL_EVENT_NOT_FOUND"


def test_si_007_a_shared_city_or_club_fragment_is_not_enough():
    # "Manchester" alone spans United and City; the source's display name is
    # "Manchester United", so a fragment must not inherit that identity.
    with pytest.raises(identity.SoccerEventIdentityError) as exc:
        _resolve(
            home_team="Manchester",
            fetcher=fetcher_for(
                {
                    ("eng.1", "20261107"): [
                        scoreboard_event(home=("360", "Manchester United", "Man Utd"))
                    ]
                }
            ),
        )
    assert exc.value.code == "SOCCER_CANONICAL_EVENT_NOT_FOUND"


def test_si_008_two_matching_fixtures_are_ambiguous():
    rows = [scoreboard_event("401800001"), scoreboard_event("401800002")]
    with pytest.raises(identity.SoccerEventIdentityError) as exc:
        _resolve(fetcher=fetcher_for({("eng.1", "20261107"): rows}))
    assert exc.value.code == "SOCCER_CANONICAL_EVENT_AMBIGUOUS"


def test_si_009_repeated_source_row_is_one_event_not_an_ambiguity():
    row = scoreboard_event()
    rows = [row, dict(row)]
    out = _resolve(fetcher=fetcher_for({("eng.1", "20261107"): rows}))
    assert out["event_id"] == "SOCCER:401878758"


@pytest.mark.parametrize("competition", ["soccer_uefa_champs_league", "", "nonsense.9"])
def test_si_010_unattested_competition_fails_closed_without_a_source_call(competition):
    def explode(_competition, _date_key):
        raise AssertionError("source must not be queried for an unattested competition")

    with pytest.raises(identity.SoccerEventIdentityError) as exc:
        _resolve(competition=competition, fetcher=explode)
    assert exc.value.code == "SOCCER_COMPETITION_UNSUPPORTED"


@pytest.mark.parametrize(("home", "away"), [("", "Tottenham"), ("Leeds", ""), ("Leeds", "Leeds")])
def test_si_011_invalid_team_aliases_are_rejected(home, away):
    with pytest.raises(identity.SoccerEventIdentityError) as exc:
        _resolve(home_team=home, away_team=away, fetcher=fetcher_for({}))
    assert exc.value.code == "SOCCER_PROVIDER_TEAM_ALIAS_INVALID"


@pytest.mark.parametrize("value", ["", "not-a-time", "2026-11-07T12:30:00"])
def test_si_012_unusable_start_time_is_rejected(value):
    with pytest.raises(identity.SoccerEventIdentityError) as exc:
        _resolve(event_start_time=value, fetcher=fetcher_for({}))
    assert exc.value.code == "SOCCER_EVENT_TIME_INVALID"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (SimpleNamespace(status_code=429, json=lambda: {}), "SOCCER_OFFICIAL_SOURCE_RATE_LIMITED"),
        (SimpleNamespace(status_code=502, json=lambda: {}), "SOCCER_OFFICIAL_SOURCE_HTTP_502"),
        (SimpleNamespace(status_code=200, json=lambda: {"events": {}}), "SOCCER_OFFICIAL_SOURCE_SCHEMA_DRIFT"),
    ],
)
def test_si_013_provider_failures_keep_their_own_code(monkeypatch, response, expected):
    monkeypatch.setattr(identity, "requests", SimpleNamespace(get=lambda *_a, **_k: response))
    with pytest.raises(identity.SoccerEventIdentityError) as exc:
        _resolve()
    assert exc.value.code == expected


def test_si_014_one_source_outage_is_not_repeated_per_row(monkeypatch):
    calls = {"n": 0}

    def boom(_competition, _date_key):
        calls["n"] += 1
        raise identity.SoccerEventIdentityError("SOCCER_OFFICIAL_SOURCE_TRANSPORT_FAILED")

    monkeypatch.setattr(identity, "_fetch_scoreboard", boom)
    for index in range(26):
        with pytest.raises(identity.SoccerEventIdentityError):
            _resolve(home_team=f"Club {index}")

    assert calls["n"] == 1


def test_si_015_success_is_cached_and_reset_clears_it(monkeypatch):
    calls = {"n": 0}

    def ok(competition, date_key):
        calls["n"] += 1
        return [scoreboard_event()] if date_key == "20261107" else []

    monkeypatch.setattr(identity, "_fetch_scoreboard", ok)
    assert _resolve()["event_id"] == "SOCCER:401878758"
    assert _resolve()["event_id"] == "SOCCER:401878758"
    assert calls["n"] == 3

    identity.reset_cache()
    _resolve()
    assert calls["n"] == 6


def test_si_016_repository_competition_keys_are_all_verified_slugs():
    for token, slug in identity.COMPETITION_SLUGS.items():
        assert identity.normalize_competition(token) == slug
        assert "." in slug
    # The five soccer keys this repository actually emits are mapped.
    for token in (
        "soccer_epl",
        "soccer_usa_mls",
        "soccer_spain_la_liga",
        "soccer_germany_bundesliga",
        "soccer_italy_serie_a",
    ):
        assert identity.normalize_competition(token)


@pytest.mark.parametrize("date", [None, "not-a-time", "2026-11-07T12:30:00"])
@pytest.mark.parametrize("valid_match", [False, True])
def test_malformed_matching_fixture_cannot_prove_absence_or_uniqueness(date, valid_match):
    rows = [scoreboard_event("401800001", date=date)]
    if valid_match:
        rows.append(scoreboard_event("401800002"))
    with pytest.raises(identity.SoccerEventIdentityError) as exc:
        _resolve(fetcher=fetcher_for({("eng.1", "20261107"): rows}))
    assert exc.value.code == "SOCCER_OFFICIAL_SOURCE_SCHEMA_DRIFT"


@pytest.mark.parametrize("malformed", [None, "bad-row", []])
@pytest.mark.parametrize("malformed_first", [False, True])
def test_mixed_scoreboard_keeps_valid_fixture_unresolved(monkeypatch, malformed, malformed_first):
    events = [malformed, scoreboard_event()] if malformed_first else [scoreboard_event(), malformed]
    monkeypatch.setattr(
        identity.requests, "get",
        lambda *_a, **_k: SimpleNamespace(status_code=200, json=lambda: {"events": events}),
    )
    event = DiscoveredEvent(
        sport="SOCCER", league="SOCCER", sport_key="soccer_epl",
        official_event_id=None, home_team="Leeds United", away_team="Tottenham Hotspur",
        commence_time_utc=EVENT_AT, event_status="PREGAME", source="DISCOVERY_FEED",
        raw={"provider_event_id": "alias-epl-1"},
    )
    held = canonicalize_soccer_discovery_identity(event)
    assert held.official_event_id is None
    assert held.raw["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
    assert held.raw["canonical_identity_blocker"] == "SOCCER_OFFICIAL_SOURCE_SCHEMA_DRIFT"


def test_wrong_day_fixture_is_not_accepted_for_requested_day():
    # The ±1-day fetch surfaces tomorrow's fixture. Participants match, but the
    # kickoff does not; identity must stay unresolved rather than borrow a
    # different day's event.
    tomorrow = scoreboard_event(date="2026-11-08T12:30:00Z")
    with pytest.raises(identity.SoccerEventIdentityError) as exc:
        _resolve(fetcher=fetcher_for({("eng.1", "20261108"): [tomorrow]}))
    assert exc.value.code == "SOCCER_CANONICAL_EVENT_NOT_FOUND"


def test_two_close_fixtures_inside_drift_budget_are_ambiguous():
    rows = [
        scoreboard_event("401800010", date="2026-11-07T12:30:00Z"),
        scoreboard_event("401800011", date="2026-11-07T13:30:00Z"),
    ]
    with pytest.raises(identity.SoccerEventIdentityError) as exc:
        _resolve(fetcher=fetcher_for({("eng.1", "20261107"): rows}))
    assert exc.value.code == "SOCCER_CANONICAL_EVENT_AMBIGUOUS"


def test_fixture_outside_drift_budget_is_excluded_not_ambiguous():
    rows = [
        scoreboard_event("401800012", date="2026-11-07T12:30:00Z"),
        scoreboard_event("401800013", date="2026-11-07T16:00:00Z"),
    ]
    out = _resolve(fetcher=fetcher_for({("eng.1", "20261107"): rows}))
    assert out["event_id"] == "SOCCER:401800012"
    assert out["kickoff_drift_minutes"] == 0.0


def test_midnight_crossover_fixture_is_accepted_within_tolerance():
    out = _resolve(
        event_start_time="2026-11-08T00:30:00Z",
        fetcher=fetcher_for(
            {("eng.1", "20261107"): [scoreboard_event(date="2026-11-07T23:45:00Z")]}
        ),
    )
    assert out["event_id"] == "SOCCER:401878758"
    assert out["event_start_time"] == "2026-11-07T23:45:00+00:00"
    assert out["kickoff_drift_minutes"] == 45.0
    assert out["can_execute"] is False


@pytest.mark.parametrize("invalid_id", [None, "", "not-an-id", "401878758-abc", "9" * 21, "٤٠١٨٧٨٧٥٨"])
@pytest.mark.parametrize("with_valid_match", [False, True])
@pytest.mark.parametrize("bad_first", [False, True])
def test_matching_malformed_soccer_id_blocks_uniqueness_instead_of_disappearing(
    invalid_id, with_valid_match, bad_first
):
    # Even if a good second fixture is present, a matching malformed ESPN ID
    # makes unique event identity unprovable. Do not publish SOCCER:<bad-id>.
    malformed = scoreboard_event(invalid_id)
    valid = scoreboard_event("401800099")
    rows = ([malformed, valid] if bad_first else [valid, malformed]) if with_valid_match else [malformed]
    with pytest.raises(identity.SoccerEventIdentityError) as exc:
        _resolve(fetcher=fetcher_for({("eng.1", "20261107"): rows}))
    assert exc.value.code == "SOCCER_OFFICIAL_SOURCE_SCHEMA_DRIFT"
    assert exc.value.detail == "matching event id invalid"


def test_nonmatching_malformed_soccer_id_does_not_pollute_valid_identity():
    # A bad ID attached to different participants is not the requested
    # fixture and cannot erase the single fully verified matching event.
    unrelated = scoreboard_event(
        None, home=("999", "Unrelated Club", "Other"),
    )
    valid = scoreboard_event("401800098")
    out = _resolve(fetcher=fetcher_for({("eng.1", "20261107"): [unrelated, valid]}))
    assert out["event_id"] == "SOCCER:401800098"
    assert out["can_execute"] is False


def test_outside_kickoff_window_bad_id_is_not_a_matching_candidate():
    tomorrow = scoreboard_event(None, date="2026-11-08T12:30:00Z")
    valid = scoreboard_event("401800097")
    out = _resolve(fetcher=fetcher_for({("eng.1", "20261107"): [tomorrow, valid]}))
    assert out["event_id"] == "SOCCER:401800097"
    assert out["can_execute"] is False


def test_conflicting_same_id_kickoff_never_proves_canonical_identity():
    rows = [
        scoreboard_event("401800014", date="2026-11-07T12:30:00Z"),
        scoreboard_event("401800014", date="2026-11-07T12:31:00Z"),
    ]
    with pytest.raises(identity.SoccerEventIdentityError) as exc:
        _resolve(fetcher=fetcher_for({("eng.1", "20261107"): rows}))
    assert exc.value.code == "SOCCER_OFFICIAL_SOURCE_SCHEMA_DRIFT"


def test_duplicate_same_id_with_equivalent_timezone_spelling_is_allowed():
    rows = [
        scoreboard_event("401800014", date="2026-11-07T12:30:00Z"),
        scoreboard_event("401800014", date="2026-11-07T07:30:00-05:00"),
    ]
    out = _resolve(fetcher=fetcher_for({("eng.1", "20261107"): rows}))
    assert out["event_id"] == "SOCCER:401800014"
    assert out["can_execute"] is False


def test_conflicting_same_id_team_identity_never_borrows_first_fixture():
    rows = [
        scoreboard_event("401800016"),
        scoreboard_event("401800016", home=("999", "Another Club", "Other")),
    ]
    with pytest.raises(identity.SoccerEventIdentityError) as exc:
        _resolve(fetcher=fetcher_for({("eng.1", "20261107"): rows}))
    assert exc.value.code == "SOCCER_OFFICIAL_SOURCE_SCHEMA_DRIFT"


def test_duplicate_home_competitor_role_is_schema_drift_not_last_write_wins():
    malformed = scoreboard_event("401800017")
    # Preserve exactly two competitors: three would fail the length guard
    # before exercising the duplicate-home-role validation.
    malformed["competitions"][0]["competitors"][1]["homeAway"] = "home"
    with pytest.raises(identity.SoccerEventIdentityError) as exc:
        _resolve(fetcher=fetcher_for({("eng.1", "20261107"): [malformed]}))
    assert exc.value.code == "SOCCER_OFFICIAL_SOURCE_SCHEMA_DRIFT"
    # Generic schema drift can come from earlier guards; assert the precise one.
    assert exc.value.detail == "duplicate home/away role"


def test_missing_or_equal_official_team_ids_fail_closed():
    for home_id, away_id in (("", "367"), ("357", ""), ("357", "357")):
        malformed = scoreboard_event(
            home=(home_id, "Leeds United", "Leeds"),
            away=(away_id, "Tottenham Hotspur", "Spurs"),
        )
        with pytest.raises(identity.SoccerEventIdentityError) as exc:
            _resolve(fetcher=fetcher_for({("eng.1", "20261107"): [malformed]}))
        assert exc.value.code == "SOCCER_OFFICIAL_SOURCE_SCHEMA_DRIFT"


def test_partial_provider_record_cannot_establish_identity():
    partial = scoreboard_event("401800015")
    partial["competitions"][0]["competitors"] = partial["competitions"][0]["competitors"][:1]
    with pytest.raises(identity.SoccerEventIdentityError) as exc:
        _resolve(fetcher=fetcher_for({("eng.1", "20261107"): [partial]}))
    assert exc.value.code == "SOCCER_CANONICAL_EVENT_NOT_FOUND"


def test_soccer_failure_codes_are_registered():
    registry = (Path(__file__).resolve().parents[1] / "docs/failure_codes.md").read_text()
    source = Path(identity.__file__).read_text()
    fixed_codes = set(re.findall(r'"(SOCCER_[A-Z_]+)"', source)) - {identity.SOURCE_PROVIDER}
    assert fixed_codes
    assert not {code for code in fixed_codes if f"`{code}`" not in registry}
    assert "`SOCCER_OFFICIAL_SOURCE_HTTP_{status}`" in registry

