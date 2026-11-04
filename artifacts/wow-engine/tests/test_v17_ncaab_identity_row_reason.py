"""Permanent regression: NCAAB identity holds retain a diagnostic typed reason."""
from v17.cross_sport_winner_discovery import (
    DiscoveredEvent, DiscoveryInventory, IDENTITY_UNRESOLVED, route_discovered_slate,
)


def test_ncaab_identity_reason_survives_terminal_row():
    event = DiscoveredEvent(
        sport="NCAAB", league="NCAAB", sport_key="5",
        official_event_id=None, home_team="Butler Bulldogs",
        away_team="Fairleigh Dickinson Knights",
        commence_time_utc="2026-11-05T00:00:00Z",
        event_status="PREGAME", source="ESPN_SCOREBOARD",
        raw={"canonical_identity_blocker": "NCAAB_CANONICAL_EVENT_NOT_FOUND"},
    )
    inventory = DiscoveryInventory(
        requested_slate_date="2026-11-04",
        requested_timezone="America/Chicago",
        events=[event],
    )
    output = route_discovered_slate(
        inventory, resolve_model=lambda _row: None,
        score_row=lambda _row, _model: {},
    )
    assert len(output) == 1
    assert output[0].bucket == IDENTITY_UNRESOLVED
    assert output[0].detail["canonical_identity_blocker"] == "NCAAB_CANONICAL_EVENT_NOT_FOUND"
    assert output[0].detail["model_invoked"] is False
    assert output[0].rank_eligible is False
    assert output[0].probability_publishable is False
