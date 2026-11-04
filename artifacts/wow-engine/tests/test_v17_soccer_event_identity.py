"""SI-001..SI-013: SOCCER identity is competition-scoped and truly three-way.
"""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
import re

import pytest

from v17 import soccer_event_identity as identity
from v17.team_event_governance_profiles import TEAM_EVENT_GOVERNANCE_PROFILES

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


def test_soccer_failure_codes_are_registered():
    registry = (Path(__file__).resolve().parents[1] / "docs/failure_codes.md").read_text()
    source = Path(identity.__file__).read_text()
    fixed_codes = set(re.findall(r'"(SOCCER_[A-Z_]+)"', source)) - {identity.SOURCE_PROVIDER}
    assert fixed_codes
    assert not {code for code in fixed_codes if f"`{code}`" not in registry}
    assert "`SOCCER_OFFICIAL_SOURCE_HTTP_{status}`" in registry

