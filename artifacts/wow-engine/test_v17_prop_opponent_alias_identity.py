from __future__ import annotations

from types import SimpleNamespace

import pytest

import prop_auto_hydration_router as router
from prop_auto_hydration import PropAutoHydrationError


START = "2026-09-23T23:05:00+00:00"


def test_mlb_full_team_name_matches_official_abbreviation() -> None:
    router._validate_requested_opponent(
        sport="MLB",
        requested_opponent="Baltimore Orioles",
        role_status={"opponent": "BAL"},
    )
    router._validate_requested_opponent(
        sport="MLB",
        requested_opponent="Toronto Blue Jays",
        role_status={"opponent": "TOR"},
    )


def test_mlb_wrong_opponent_still_fails_closed() -> None:
    with pytest.raises(PropAutoHydrationError) as exc_info:
        router._validate_requested_opponent(
            sport="MLB",
            requested_opponent="Tampa Bay Rays",
            role_status={"opponent": "BAL"},
        )

    assert exc_info.value.code == "PROP_EVENT_IDENTITY_CONFLICT"


def test_mlb_request_context_treats_alias_equivalent_opponents_as_same_event() -> None:
    rows = [
        SimpleNamespace(
            sport="MLB",
            player="Max Scherzer",
            event_start_time=START,
            event_id="MLB:777001",
            opponent="Baltimore Orioles",
        ),
        SimpleNamespace(
            sport="MLB",
            player="Max Scherzer",
            event_start_time=START,
            event_id="MLB:777001",
            opponent="BAL",
        ),
    ]

    with router.hydration_request_context(rows):
        canonical, opponent = router._context_identity(
            sport="MLB",
            player="Max Scherzer",
            event_start_time=START,
            canonical_event_id=None,
            opponent=None,
        )

    assert canonical == "MLB:777001"
    assert opponent in {"Baltimore Orioles", "BAL"}
