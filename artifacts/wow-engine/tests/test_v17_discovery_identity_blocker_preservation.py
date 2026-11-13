"""Regression: identity evidence survives terminal routing and is auditable.

No scoring, fitted-model registration, credential or canonical resolution is
introduced. Kept separate from the active NCAAB/Soccer resolver integration.
"""
from v17 import cross_sport_winner_discovery as discovery


def _event(sport, event_id, *, raw=None):
    return discovery.DiscoveredEvent(
        sport=sport,
        league=sport,
        sport_key=sport.lower(),
        official_event_id=event_id,
        home_team="Home",
        away_team="Away",
        commence_time_utc="2026-11-06T20:00:00+00:00",
        event_status="PREGAME",
        source="TEST_FIXTURE",
        raw=raw or {},
    )


def test_canonical_blockers_survive_purge_and_reconcile_without_model_invocation():
    inventory = discovery.DiscoveryInventory(
        requested_slate_date="2026-11-06",
        requested_timezone="UTC",
        events=[
            _event("NCAAF", None, raw={
                "canonical_identity_status": "ALIAS_ONLY_UNRESOLVED",
                "canonical_identity_blocker": "CFBD_API_KEY_MISSING",
                "identity_provider": "CFBD",
                "provider_event_id": "odds-1",
            }),
            _event("NCAAB", None, raw={
                "canonical_identity_status": "ALIAS_ONLY_UNRESOLVED",
                "canonical_identity_blocker": "NCAAB_CANONICAL_IDENTITY_RESOLVER_NOT_REGISTERED",
                "identity_provider": "ESPN",
                "provider_event_id": "espn-alias-1",
            }),
            _event("NBA", "NBA:4019"),
        ],
    )
    calls = []
    rows = discovery.route_discovered_slate(
        inventory,
        resolve_model=lambda event: calls.append(event.sport) or None,
        score_row=lambda *_: (_ for _ in ()).throw(AssertionError("no model exists")),
    )
    assert calls == ["NBA"]
    assert [row.bucket for row in rows] == [
        discovery.IDENTITY_UNRESOLVED,
        discovery.IDENTITY_UNRESOLVED,
        discovery.MODEL_UNAVAILABLE,
    ]
    for row in rows[:2]:
        assert row.detail["canonical_identity_status"] == "ALIAS_ONLY_UNRESOLVED"
        assert row.detail["provider_event_id_type"] == "ALIAS_ONLY_NOT_CANONICAL"
        assert row.rank_eligible is False
        assert row.probability_publishable is False
        assert row.as_dict()["can_execute"] is False
    assert rows[0].detail["canonical_identity_blocker"] == "CFBD_API_KEY_MISSING"
    assert rows[1].detail["canonical_identity_blocker"] == "NCAAB_CANONICAL_IDENTITY_RESOLVER_NOT_REGISTERED"

    receipt = discovery.reconcile(inventory, rows)
    assert receipt["events_discovered"] == receipt["events_accounted"] == 3
    assert receipt["row_reconciliation"] == "PASS"
    assert receipt["identity_status_counts"] == {"ALIAS_ONLY_UNRESOLVED": 2}
    assert receipt["identity_blocker_counts"] == {
        "CFBD_API_KEY_MISSING": 1,
        "NCAAB_CANONICAL_IDENTITY_RESOLVER_NOT_REGISTERED": 1,
    }
    assert receipt["held_reason_counts"] == {
        discovery.IDENTITY_UNRESOLVED: 2,
        "NBA_TEAM_EVENT_FITTED_MODEL_OR_ADAPTER_UNAVAILABLE": 1,
    }
    assert receipt["can_execute"] is False


def test_untrusted_identity_metadata_is_bounded_and_cannot_publish():
    inventory = discovery.DiscoveryInventory(
        requested_slate_date="2026-11-06",
        requested_timezone="UTC",
        events=[_event("NCAAF", None, raw={
            "canonical_identity_blocker": "CFBD_API_KEY_MISSING:provider diagnostic",
            "canonical_identity_status": "ALIAS_ONLY_UNRESOLVED",
            "provider_event_id": "x" * 400,
            "price": 999,
            "probability_publishable": True,
        })],
    )
    rows = discovery.route_discovered_slate(
        inventory,
        resolve_model=lambda _: (_ for _ in ()).throw(AssertionError("unresolved row must purge")),
        score_row=lambda *_: (_ for _ in ()).throw(AssertionError("must not score")),
    )
    detail = rows[0].detail
    assert detail["canonical_identity_blocker"] == "CFBD_API_KEY_MISSING"
    assert "provider_event_id" not in detail
    assert "price" not in detail
    assert detail["probability_publishable"] is False
    assert rows[0].as_dict()["market_probability_substitution_allowed"] is False
