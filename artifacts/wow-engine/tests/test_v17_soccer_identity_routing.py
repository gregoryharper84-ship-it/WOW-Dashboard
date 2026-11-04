"""SOCCER identity reaches model routing with the three-way space intact.

Identity repair must move a SOCCER row out of the identity bucket and must not
do anything else: no winner is chosen, the draw stays a terminal outcome, and an
uncertified lane still answers MODEL_UNAVAILABLE.
"""
from __future__ import annotations

from datetime import datetime, timezone

from v17.cross_sport_winner_discovery import (
    IDENTITY_UNRESOLVED,
    MODEL_UNAVAILABLE,
    DiscoveredEvent,
    DiscoveryInventory,
    route_discovered_slate,
)
from v17.team_event_sport_parity import canonicalize_soccer_discovery_identity

EVENT_AT = "2026-11-07T12:30:00Z"
SLATE = "2026-11-07"
TZ = "America/New_York"


def soccer_alias_event(*, raw_extra=None, sport_key="soccer_epl"):
    raw = {"provider_event_id": "alias-epl-1"}
    raw.update(raw_extra or {})
    return DiscoveredEvent(
        sport="SOCCER",
        league="SOCCER",
        sport_key=sport_key,
        official_event_id=None,
        home_team="Leeds United",
        away_team="Tottenham Hotspur",
        commence_time_utc=EVENT_AT,
        event_status="PREGAME",
        source="DISCOVERY_FEED",
        provider="THE_ODDS_API",
        raw=raw,
    )


def test_resolved_soccer_identity_preserves_three_way_and_the_alias(monkeypatch):
    import v17.soccer_event_identity as identity

    monkeypatch.setattr(
        identity,
        "resolve_soccer_current_event_identity",
        lambda **_kwargs: {
            "event_id": "SOCCER:401878758",
            "competition": "eng.1",
            "outcome_space": "HOME_DRAW_AWAY",
            "draw_possible": True,
            "settlement_contract": "REGULATION_TIME_THREE_WAY_UNLESS_MARKET_EXPLICIT",
            "identity_provider": "ESPN_SOCCER_SCOREBOARD",
            "identity_resolution": "ESPN_COMPETITION_SCOREBOARD_EXACT_PARTICIPANTS_DATE_MATCH",
            "prediction_authority": False,
            "can_execute": False,
        },
    )
    out = canonicalize_soccer_discovery_identity(soccer_alias_event())
    assert out.official_event_id == "SOCCER:401878758"
    assert out.raw["provider_event_id"] == "alias-epl-1"
    assert out.raw["canonical_identity_status"] == "CANONICAL_RESOLVED"
    assert out.raw["canonical_identity_competition"] == "eng.1"
    assert out.raw["canonical_identity_outcome_space"] == "HOME_DRAW_AWAY"
    assert out.raw["canonical_identity_draw_possible"] is True
    assert out.raw["canonical_identity_market_features_used"] is False


def test_unattested_competition_stays_alias_only_without_a_source_call(monkeypatch):
    """An unattested league key must never reach the scoreboard.

    The resolver owns competition validation, so the assertion is that no HTTP
    request happens — not that the resolver is skipped.
    """
    import v17.soccer_event_identity as identity

    def explode(*_args, **_kwargs):
        raise AssertionError("scoreboard must not be queried for an unattested competition")

    monkeypatch.setattr(identity, "_fetch_scoreboard", explode)
    event = soccer_alias_event(sport_key="soccer_uefa_champs_league")
    out = canonicalize_soccer_discovery_identity(event)
    assert out.official_event_id is None
    assert out.raw["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
    assert out.raw["canonical_identity_blocker"] == "SOCCER_COMPETITION_UNSUPPORTED"


def test_identity_failure_keeps_the_row_held(monkeypatch):
    import v17.soccer_event_identity as identity

    class IdentityFailure(RuntimeError):
        code = "SOCCER_CANONICAL_EVENT_NOT_FOUND"

    def fail(**_kwargs):
        raise IdentityFailure("no fixture")

    monkeypatch.setattr(identity, "resolve_soccer_current_event_identity", fail)
    out = canonicalize_soccer_discovery_identity(soccer_alias_event())
    assert out.official_event_id is None
    assert out.raw["canonical_identity_blocker"] == "SOCCER_CANONICAL_EVENT_NOT_FOUND"


def test_other_sports_pass_through_untouched():
    event = DiscoveredEvent(
        sport="NCAAB",
        league="NCAAB",
        sport_key="basketball_ncaab",
        official_event_id=None,
        home_team="Duke Blue Devils",
        away_team="Kansas Jayhawks",
        commence_time_utc=EVENT_AT,
        event_status="PREGAME",
        source="DISCOVERY_FEED",
        raw={"provider_event_id": "alias"},
    )
    assert canonicalize_soccer_discovery_identity(event) is event


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
        now=datetime(2026, 11, 7, 6, 0, tzinfo=timezone.utc),
    )[0]


def test_resolved_soccer_row_reaches_model_routing_not_the_identity_bucket():
    event = DiscoveredEvent(
        **{
            **soccer_alias_event().__dict__,
            "official_event_id": "SOCCER:401878758",
            "raw": {
                "provider_event_id": "alias-epl-1",
                "canonical_identity_outcome_space": "HOME_DRAW_AWAY",
            },
        }
    )
    row = _route(event)
    assert row.bucket == MODEL_UNAVAILABLE
    assert row.bucket != IDENTITY_UNRESOLVED
    assert row.identity["official_event_id"] == "SOCCER:401878758"


def test_unresolved_control_still_lands_in_the_identity_bucket():
    row = _route(soccer_alias_event())
    assert row.bucket == IDENTITY_UNRESOLVED


def test_soccer_identity_never_publishes_a_probability_or_a_draw_shortcut():
    event = DiscoveredEvent(
        **{
            **soccer_alias_event().__dict__,
            "official_event_id": "SOCCER:401878758",
            "raw": {
                "provider_event_id": "alias-epl-1",
                "canonical_identity_outcome_space": "HOME_DRAW_AWAY",
                "canonical_identity_draw_possible": True,
            },
        }
    )
    row = _route(event)
    payload = row.as_dict()
    assert row.probability_publishable is False
    assert row.rank_eligible is False
    assert payload["can_execute"] is False
    assert payload["market_probability_substitution_allowed"] is False
    assert payload["generic_reasoning_substitution_allowed"] is False
    assert "probability" not in payload
