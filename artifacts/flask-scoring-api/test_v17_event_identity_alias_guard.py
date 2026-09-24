from gate_engine.event_identity import build_event_key, build_event_key_from_row


def test_provider_alias_is_not_promoted_to_canonical_official_event_id():
    row = {
        "league": "NFL",
        "event_id": "401872948",
        "espn_event_id": "401872948",
        "scheduled_start_utc": "2026-09-24T23:00:00Z",
        "participants": ["GB", "ATL"],
        "settlement_market": "PLAYER_PROP",
    }

    actual = build_event_key_from_row(row)
    expected = build_event_key(
        league="NFL",
        official_event_id=None,
        scheduled_start_utc="2026-09-24T23:00:00Z",
        participants=["GB", "ATL"],
        settlement_market="PLAYER_PROP",
    )
    alias_promoted = build_event_key(
        league="NFL",
        official_event_id="401872948",
        scheduled_start_utc="2026-09-24T23:00:00Z",
        participants=["GB", "ATL"],
        settlement_market="PLAYER_PROP",
    )

    assert actual == expected
    assert actual != alias_promoted


def test_resolved_canonical_official_event_id_remains_authoritative():
    row = {
        "league": "NFL",
        "official_event_id": "2026_04_ATL_GB",
        "event_id": "401872948",
        "espn_event_id": "401872948",
        "scheduled_start_utc": "2026-09-24T23:00:00Z",
        "participants": ["GB", "ATL"],
        "settlement_market": "PLAYER_PROP",
    }

    assert build_event_key_from_row(row) == build_event_key(
        league="NFL",
        official_event_id="2026_04_ATL_GB",
        scheduled_start_utc="2026-09-24T23:00:00Z",
        participants=["GB", "ATL"],
        settlement_market="PLAYER_PROP",
    )
