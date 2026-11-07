"""One NHL canonicalizer failure must retain its typed origin after discovery."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from v17.cross_sport_winner_discovery import (
    DiscoveredEvent,
    IDENTITY_UNRESOLVED,
    route_discovered_slate,
)


def _route(blocker, *, sport="NHL"):
    event = DiscoveredEvent(
        sport=sport,
        league=sport,
        sport_key="6",
        official_event_id=None,
        home_team="Winnipeg Jets",
        away_team="Vancouver Canucks",
        commence_time_utc="2026-11-06T01:00:00Z",
        event_status="PREGAME",
        source="ESPN_SCOREBOARD",
        raw={
            "canonical_identity_status": "ALIAS_ONLY_UNRESOLVED",
            "canonical_identity_blocker": blocker,
        },
    )
    inventory = SimpleNamespace(
        events=[event],
        requested_slate_date="2026-11-05",
        requested_timezone="America/Chicago",
    )
    def not_allowed(*_args, **_kwargs):
        raise AssertionError("identity failure must not reach model")
    return route_discovered_slate(
        inventory, resolve_model=not_allowed, score_row=not_allowed,
    )[0]


def test_nhl_resolver_failure_survives_identity_hold_without_promotion():
    row = _route("NHL_CANONICAL_EVENT_NOT_FOUND")
    assert row.bucket == IDENTITY_UNRESOLVED
    assert row.detail["reason"] == IDENTITY_UNRESOLVED
    assert row.detail["originating_identity_blocker"] == "NHL_CANONICAL_EVENT_NOT_FOUND"
    assert row.detail["model_invoked"] is False
    assert row.rank_eligible is False
    assert row.probability_publishable is False
    assert row.as_dict()["can_execute"] is False


@pytest.mark.parametrize("unsafe", [
    "", "NHL_OK\nSECRET", "nhl_canonical_event_not_found", "DROP TABLE",
    "NHL_BAD;" , 123, None, ["NHL_CANONICAL_EVENT_NOT_FOUND"],
    "NHL_" + "A" * 200,
])
def test_nhl_untrusted_failure_values_do_not_leak_to_row_detail(unsafe):
    row = _route(unsafe)
    assert row.bucket == IDENTITY_UNRESOLVED
    assert "originating_identity_blocker" not in row.detail
    assert row.rank_eligible is False


def test_same_spoofed_code_in_other_sport_is_never_exposed_as_nhl():
    row = _route("NHL_CANONICAL_EVENT_NOT_FOUND", sport="NCAAB")
    assert row.bucket == IDENTITY_UNRESOLVED
    assert "originating_identity_blocker" not in row.detail
