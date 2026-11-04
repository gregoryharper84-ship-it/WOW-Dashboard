"""NCAAB identity handoff reaches model routing without unlocking a model.

The 2026-11-04 board contained NCAAB rows that never left the identity bucket
and rows that were correctly held for certification. These two facts must stay
separate: identity repair moves a row to the model question, and the model
question still answers MODEL_UNAVAILABLE until NCAAB is certified.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from v17.cross_sport_winner_discovery import (
    IDENTITY_UNRESOLVED,
    MODEL_UNAVAILABLE,
    DiscoveredEvent,
    DiscoveryInventory,
    route_discovered_slate,
)
from v17.team_event_sport_parity import canonicalize_ncaab_discovery_identity

EVENT_AT = "2026-11-04T23:30:00Z"
SLATE = "2026-11-04"
TZ = "America/New_York"


def ncaab_alias_event(*, raw_extra=None):
    raw = {
        "provider_event_id": "401999999",
        "_wow_secondary_event_id": "401999999",
        "_wow_secondary_home_team_id": "150",
        "_wow_secondary_away_team_id": "2306",
        "prediction_authority": False,
    }
    raw.update(raw_extra or {})
    return DiscoveredEvent(
        sport="NCAAB",
        league="NCAAB",
        sport_key="basketball_ncaab",
        official_event_id=None,
        home_team="Duke Blue Devils",
        away_team="Kansas Jayhawks",
        commence_time_utc=EVENT_AT,
        event_status="PREGAME",
        source="DISCOVERY_FEED",
        provider="ESPN_SCOREBOARD",
        raw=raw,
    )


def test_resolved_identity_is_recorded_and_the_alias_is_preserved(monkeypatch):
    import v17.ncaab_event_identity as identity

    monkeypatch.setattr(
        identity,
        "resolve_ncaab_current_event_identity",
        lambda **_kwargs: {
            "event_id": "NCAAB:401950001",
            "identity_provider": "ESPN_NCAAB_SCOREBOARD",
            "identity_resolution": "ESPN_SCOREBOARD_EXACT_PARTICIPANTS_DATE_MATCH",
            "prediction_authority": False,
            "can_execute": False,
        },
    )
    out = canonicalize_ncaab_discovery_identity(ncaab_alias_event())
    assert out.official_event_id == "NCAAB:401950001"
    assert out.raw["provider_event_id"] == "401999999"
    assert out.raw["canonical_identity_status"] == "CANONICAL_RESOLVED"
    assert out.raw["canonical_identity_source"] == "ESPN_NCAAB_SCOREBOARD"
    assert out.raw["canonical_identity_market_features_used"] is False


def test_unproven_identity_keeps_the_row_alias_only(monkeypatch):
    import v17.ncaab_event_identity as identity

    class IdentityFailure(RuntimeError):
        code = "NCAAB_CANONICAL_EVENT_NOT_FOUND"

    def fail(**_kwargs):
        raise IdentityFailure("no exact scoreboard event")

    monkeypatch.setattr(identity, "resolve_ncaab_current_event_identity", fail)
    out = canonicalize_ncaab_discovery_identity(ncaab_alias_event())
    assert out.official_event_id is None
    assert out.raw["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
    assert out.raw["canonical_identity_blocker"] == "NCAAB_CANONICAL_EVENT_NOT_FOUND"


def test_missing_secondary_team_alias_never_queries_the_source(monkeypatch):
    import v17.ncaab_event_identity as identity

    def explode(**_kwargs):
        raise AssertionError("resolver must not be consulted without both aliases")

    monkeypatch.setattr(identity, "resolve_ncaab_current_event_identity", explode)
    event = ncaab_alias_event(raw_extra={"_wow_secondary_away_team_id": ""})
    out = canonicalize_ncaab_discovery_identity(event)
    assert out.official_event_id is None
    assert out.raw["canonical_identity_blocker"] == "NCAAB_PROVIDER_TEAM_ALIAS_MISSING"


def test_other_sports_pass_through_untouched():
    event = DiscoveredEvent(
        sport="NHL",
        league="NHL",
        sport_key="icehockey_nhl",
        official_event_id=None,
        home_team="Boston Bruins",
        away_team="New York Rangers",
        commence_time_utc=EVENT_AT,
        event_status="PREGAME",
        source="DISCOVERY_FEED",
        raw={"provider_event_id": "alias"},
    )
    assert canonicalize_ncaab_discovery_identity(event) is event


def _route(event):
    inventory = DiscoveryInventory(
        requested_slate_date=SLATE,
        requested_timezone=TZ,
        events=[event],
    )
    return route_discovered_slate(
        inventory,
        resolve_model=lambda _event: None,
        score_row=lambda _event, _model: {},
        now=datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc),
    )[0]


def test_identity_repair_moves_the_row_out_of_the_identity_bucket():
    """The exact board symptom: a NCAAB row stuck as EVENT_IDENTITY_UNRESOLVED."""
    resolved = canonicalize_ncaab_discovery_identity(
        ncaab_alias_event(raw_extra={"official_event_id": None})
    )
    resolved = DiscoveredEvent(
        **{
            **resolved.__dict__,
            "official_event_id": "NCAAB:401950001",
        }
    )
    row = _route(resolved)
    assert row.bucket == MODEL_UNAVAILABLE
    assert row.bucket != IDENTITY_UNRESOLVED
    assert row.identity["official_event_id"] == "NCAAB:401950001"


def test_unresolved_control_still_lands_in_the_identity_bucket():
    row = _route(ncaab_alias_event())
    assert row.bucket == IDENTITY_UNRESOLVED


def test_identity_alone_never_publishes_a_probability():
    resolved = DiscoveredEvent(
        **{**ncaab_alias_event().__dict__, "official_event_id": "NCAAB:401950001"}
    )
    row = _route(resolved)
    assert row.model_status == MODEL_UNAVAILABLE
    assert row.probability_publishable is False
    assert row.rank_eligible is False
    assert row.detail["model_invoked"] is False
    assert row.as_dict()["can_execute"] is False
