"""NI-001..NI-010: NCAAB canonical identity fails closed and never amplifies.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from v17 import ncaab_event_identity as identity

EVENT_AT = "2026-11-04T23:30:00Z"  # local evening game; UTC date is the same day


def scoreboard_event(event_id, *, date=EVENT_AT, home_id="150", away_id="2306"):
    return {
        "id": event_id,
        "date": date,
        "competitions": [
            {
                "competitors": [
                    {"id": home_id, "homeAway": "home"},
                    {"id": away_id, "homeAway": "away"},
                ]
            }
        ],
    }


def fetcher_for(rows_by_date):
    def fetch(date_key):
        return list(rows_by_date.get(date_key, []))

    return fetch


@pytest.fixture(autouse=True)
def _clear_cache():
    identity.reset_cache()
    yield
    identity.reset_cache()


def _resolve(**overrides):
    kwargs = {
        "event_start_time": EVENT_AT,
        "home_team_alias": "espn-150",
        "away_team_alias": "2306",
    }
    kwargs.update(overrides)
    return identity.resolve_ncaab_current_event_identity(**kwargs)


def test_ni_001_unique_participant_date_match_assigns_canonical_ncaab_id():
    out = _resolve(
        fetcher=fetcher_for({"20261104": [scoreboard_event("401800001")]})
    )
    assert out["event_id"] == "NCAAB:401800001"
    assert out["espn_event_id"] == "401800001"
    assert out["home_team_id"] == "espn-150"
    assert out["away_team_id"] == "espn-2306"
    assert out["identity_provider"] == "ESPN_NCAAB_SCOREBOARD"
    assert out["identity_resolution"] == "ESPN_SCOREBOARD_EXACT_PARTICIPANTS_DATE_MATCH"
    assert out["market_features_used"] is False
    assert out["prediction_authority"] is False
    assert out["can_execute"] is False


def test_ni_002_home_away_order_is_not_interchangeable():
    # Same two teams, reversed sides: this is a different event, not a match.
    with pytest.raises(identity.NCAABEventIdentityError) as exc:
        _resolve(
            fetcher=fetcher_for(
                {"20261104": [scoreboard_event("401800002", home_id="2306", away_id="150")]}
            )
        )
    assert exc.value.code == "NCAAB_CANONICAL_EVENT_NOT_FOUND"


def test_ni_003_unknown_participants_fail_closed():
    with pytest.raises(identity.NCAABEventIdentityError) as exc:
        _resolve(fetcher=fetcher_for({"20261104": []}))
    assert exc.value.code == "NCAAB_CANONICAL_EVENT_NOT_FOUND"


def test_ni_004_two_matching_events_are_ambiguous_not_picked_arbitrarily():
    rows = [scoreboard_event("401800003"), scoreboard_event("401800004")]
    with pytest.raises(identity.NCAABEventIdentityError) as exc:
        _resolve(fetcher=fetcher_for({"20261104": rows}))
    assert exc.value.code == "NCAAB_CANONICAL_EVENT_AMBIGUOUS"


@pytest.mark.parametrize(("home", "away"), [("", "2306"), ("150", ""), ("150", "150")])
def test_ni_005_invalid_provider_aliases_are_rejected(home, away):
    with pytest.raises(identity.NCAABEventIdentityError) as exc:
        _resolve(home_team_alias=home, away_team_alias=away, fetcher=fetcher_for({}))
    assert exc.value.code == "NCAAB_PROVIDER_TEAM_ALIAS_INVALID"


@pytest.mark.parametrize("value", ["", "not-a-time", "2026-11-04T23:30:00"])
def test_ni_006_unusable_start_time_is_rejected(value):
    with pytest.raises(identity.NCAABEventIdentityError) as exc:
        _resolve(event_start_time=value, fetcher=fetcher_for({}))
    assert exc.value.code == "NCAAB_EVENT_TIME_INVALID"


def test_ni_007_transport_failure_is_typed_not_swallowed(monkeypatch):
    class _Boom:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("connection reset")

    monkeypatch.setattr(identity, "requests", _Boom())
    with pytest.raises(identity.NCAABEventIdentityError) as exc:
        _resolve()
    assert exc.value.code == "NCAAB_OFFICIAL_SOURCE_TRANSPORT_FAILED"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (SimpleNamespace(status_code=429, json=lambda: {}), "NCAAB_OFFICIAL_SOURCE_RATE_LIMITED"),
        (SimpleNamespace(status_code=503, json=lambda: {}), "NCAAB_OFFICIAL_SOURCE_HTTP_503"),
        (SimpleNamespace(status_code=200, json=lambda: {"events": {}}), "NCAAB_OFFICIAL_SOURCE_SCHEMA_DRIFT"),
    ],
)
def test_ni_008_provider_failures_keep_their_own_code(monkeypatch, response, expected):
    monkeypatch.setattr(
        identity, "requests", SimpleNamespace(get=lambda *_a, **_k: response)
    )
    with pytest.raises(identity.NCAABEventIdentityError) as exc:
        _resolve()
    assert exc.value.code == expected


def test_ni_009_one_source_outage_is_not_repeated_per_row(monkeypatch):
    """26 NCAAB rows must not become 78 scoreboard calls (3 dates each)."""
    calls = {"n": 0}

    def boom(_date_key):
        calls["n"] += 1
        raise identity.NCAABEventIdentityError("NCAAB_OFFICIAL_SOURCE_TRANSPORT_FAILED")

    monkeypatch.setattr(identity, "_fetch_scoreboard", boom)
    for row in range(26):
        with pytest.raises(identity.NCAABEventIdentityError):
            _resolve(home_team_alias=f"espn-{1000 + row}")

    # The first typed failure is cached, so the following 25 rows short-circuit
    # instead of re-requesting the source.
    assert calls["n"] <= len(identity.date_keys_for(identity._aware(EVENT_AT)))
    assert calls["n"] == 1


def test_ni_010_successful_reads_are_cached_and_reset_clears_them(monkeypatch):
    calls = {"n": 0}

    def ok(date_key):
        calls["n"] += 1
        return [scoreboard_event("401800005")] if date_key == "20261104" else []

    monkeypatch.setattr(identity, "_fetch_scoreboard", ok)
    assert _resolve()["event_id"] == "NCAAB:401800005"
    assert _resolve()["event_id"] == "NCAAB:401800005"
    assert calls["n"] == 3

    identity.reset_cache()
    _resolve()
    assert calls["n"] == 6
