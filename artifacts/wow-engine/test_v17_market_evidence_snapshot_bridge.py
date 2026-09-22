from __future__ import annotations

from datetime import datetime, timezone

from v17.market_evidence_snapshot_bridge import attach_snapshot_evidence
from v17.scout_research_promotion import evaluate_candidate

NOW = datetime(2026, 9, 14, 20, 1, tzinfo=timezone.utc)


def _handoff(*, duplicate=False, sport_key="baseball_mlb", home="Texas Rangers", away="Houston Astros", start="2026-09-14T20:00:00Z"):
    candidate = {
        "official_event_id": "primary-evt-1",
        "sport_key": sport_key,
        "commence_time": start,
        "home_team": home,
        "away_team": away,
        "route": "LLP_TEAM_BETTING_ENGINE",
        "market_evidence": [],
        "market_evidence_status": "NO_MARKET_EVIDENCE",
        "market_evidence_source_blockers": [],
        "probability": None,
        "probability_authority": False,
        "can_execute": False,
    }
    candidates = [candidate]
    if duplicate:
        candidates.append(dict(candidate, official_event_id="primary-evt-2"))
    return {
        "status": "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS",
        "model_handoff": {"team_event_candidates": candidates, "prop_candidates": []},
        "governance": {"can_execute": False},
        "can_execute": False,
    }


def _event(
    provider,
    book,
    price,
    *,
    sport_key="baseball_mlb",
    home="Texas Rangers",
    away="Houston Astros",
    outcome_home=None,
    outcome_away=None,
    start="2026-09-14T20:03:00Z",
    updated="2026-09-14T19:59:00Z",
    capability="events",
):
    return {
        "id": f"{provider}-evt",
        "sport_key": sport_key,
        "commence_time": start,
        "home_team": home,
        "away_team": away,
        "_wow_market_evidence": {
            "provider": provider,
            "provider_detail": capability,
            "prediction_authority": False,
            "exact_line_authority": False,
            "research_only": True,
            "can_execute": False,
        },
        "bookmakers": [{
            "key": book,
            "title": book,
            "last_update": updated,
            "markets": [{
                "key": "h2h",
                "last_update": updated,
                "outcomes": [
                    {"name": outcome_home or home, "price": price},
                    {"name": outcome_away or away, "price": 110},
                ],
            }],
        }],
    }


def _snapshot(events, **extra):
    payload = {
        "status": "MARKET_EVIDENCE_CAPTURED",
        "generated_at": "2026-09-14T20:00:00Z",
        "events": events,
        "reconciliation": {"captured_rows": len(events), "balanced": True},
        "prediction_authority": False,
        "exact_line_authority": False,
        "research_only": True,
        "can_execute": False,
    }
    payload.update(extra)
    return payload


def test_exact_event_match_attaches_cross_book_evidence_without_probability_authority():
    result = attach_snapshot_evidence(
        _handoff(),
        _snapshot([
            _event("RUNDOWN", "book-a", -120),
            _event("SHARPAPI", "book-b", -118),
        ]),
        now=NOW,
    )
    candidate = result["model_handoff"]["team_event_candidates"][0]
    assert len(candidate["market_evidence"]) == 4
    assert {row["bookmaker"] for row in candidate["market_evidence"]} == {"book-a", "book-b"}
    assert all(row["freshness_state"] == "FRESH" for row in candidate["market_evidence"])
    assert all(row["prediction_authority"] is False for row in candidate["market_evidence"])
    assert all(row["can_execute"] is False for row in candidate["market_evidence"])
    assert candidate["probability"] is None
    assert result["market_evidence_snapshot_bridge"]["candidates_touched"] == 1
    assert result["market_evidence_snapshot_bridge"]["evidence_rows_attached"] == 4
    assert result["can_execute"] is False

    evaluation = evaluate_candidate(candidate, now=NOW)
    assert evaluation["research_status"] in {"RESEARCH_INTEREST_MEDIUM", "RESEARCH_INTEREST_HIGH"}
    assert evaluation["probability"] is None
    assert evaluation["prediction_authority"] is False


def test_provider_city_labels_match_only_when_full_franchise_names_are_explicit_h2h_outcomes():
    handoff = _handoff(
        sport_key="americanfootball_nfl",
        home="Kansas City Chiefs",
        away="Denver Broncos",
        start="2026-09-15T00:15:00Z",
    )
    event = _event(
        "RUNDOWN",
        "book-a",
        -120,
        sport_key="americanfootball_nfl",
        home="Kansas City",
        away="Denver",
        outcome_home="Kansas City Chiefs",
        outcome_away="Denver Broncos",
        start="2026-09-15T00:15:00Z",
        updated="2026-09-15T00:10:00Z",
    )
    result = attach_snapshot_evidence(
        handoff, _snapshot([event]), now=datetime(2026, 9, 15, 0, 11, tzinfo=timezone.utc),
    )
    candidate = result["model_handoff"]["team_event_candidates"][0]
    assert len(candidate["market_evidence"]) == 2
    assert {row["outcome_name"] for row in candidate["market_evidence"]} == {"Kansas City Chiefs", "Denver Broncos"}
    assert result["market_evidence_snapshot_bridge"]["matched_events"] == 1
    assert result["market_evidence_snapshot_bridge"]["evidence_rows_attached"] == 2
    assert candidate["probability"] is None
    assert all(row["prediction_authority"] is False for row in candidate["market_evidence"])


def test_provider_city_prefix_without_exact_full_h2h_identity_is_rejected():
    handoff = _handoff(
        sport_key="americanfootball_nfl",
        home="Kansas City Chiefs",
        away="Denver Broncos",
        start="2026-09-15T00:15:00Z",
    )
    event = _event(
        "RUNDOWN",
        "book-a",
        -120,
        sport_key="americanfootball_nfl",
        home="Kansas City",
        away="Denver",
        outcome_home="Kansas City",
        outcome_away="Denver",
        start="2026-09-15T00:15:00Z",
        updated="2026-09-15T00:10:00Z",
    )
    result = attach_snapshot_evidence(
        handoff, _snapshot([event]), now=datetime(2026, 9, 15, 0, 11, tzinfo=timezone.utc),
    )
    candidate = result["model_handoff"]["team_event_candidates"][0]
    assert candidate["market_evidence"] == []
    assert result["market_evidence_snapshot_bridge"]["matched_events"] == 0
    assert result["market_evidence_snapshot_bridge"]["unmatched_events"] == 1


def test_stale_current_market_rows_are_quarantined_at_consumption():
    result = attach_snapshot_evidence(
        _handoff(),
        _snapshot([_event("RUNDOWN", "book-a", -120, updated="2026-09-14T19:30:00Z")]),
        now=NOW,
    )
    candidate = result["model_handoff"]["team_event_candidates"][0]
    assert candidate["market_evidence"] == []
    assert len(candidate["market_evidence_stale"]) == 2
    assert candidate["market_evidence_status"] == "STALE_OR_HISTORICAL_ONLY"
    assert "MARKET_EVIDENCE_STALE_AT_CONSUMPTION" in candidate["market_evidence_source_blockers"]
    assert result["market_evidence_snapshot_bridge"]["stale_or_unknown_rows_quarantined"] == 2
    evaluation = evaluate_candidate(candidate, now=NOW)
    assert evaluation["research_status"] == "WATCH"
    assert evaluation["probability"] is None


def test_historical_openers_never_become_current_market_evidence():
    result = attach_snapshot_evidence(
        _handoff(),
        _snapshot([_event("RUNDOWN", "book-a", -120, capability="openers")]),
        now=NOW,
    )
    candidate = result["model_handoff"]["team_event_candidates"][0]
    assert candidate["market_evidence"] == []
    assert len(candidate["market_evidence_historical"]) == 2
    assert "MARKET_EVIDENCE_HISTORICAL_ONLY" in candidate["market_evidence_source_blockers"]
    assert result["market_evidence_snapshot_bridge"]["historical_opener_rows_quarantined"] == 2


def test_snapshot_disagreement_alerts_are_forwarded_without_probability_mutation():
    alert = {
        "code": "MARKET_SOURCE_DISAGREEMENT",
        "prediction_authority": False,
        "can_execute": False,
    }
    result = attach_snapshot_evidence(
        _handoff(),
        _snapshot([_event("RUNDOWN", "book-a", -120)], source_disagreement_alert_count=1, source_disagreement_alerts=[alert]),
        now=NOW,
    )
    assert result["source_disagreement_alerts"] == [alert]
    assert result["market_evidence_snapshot_bridge"]["source_disagreement_alert_count"] == 1
    candidate = result["model_handoff"]["team_event_candidates"][0]
    assert candidate["probability"] is None


def test_team_or_time_mismatch_never_attaches():
    snapshot = _snapshot([
        _event("RUNDOWN", "book-a", -120, home="Seattle Mariners"),
        _event("SHARPAPI", "book-b", -118, start="2026-09-15T05:00:00Z"),
    ])
    result = attach_snapshot_evidence(_handoff(), snapshot, now=NOW)
    candidate = result["model_handoff"]["team_event_candidates"][0]
    assert candidate["market_evidence"] == []
    assert result["market_evidence_snapshot_bridge"]["evidence_rows_attached"] == 0
    assert result["market_evidence_snapshot_bridge"]["unmatched_events"] == 2


def test_ambiguous_candidate_identity_is_left_unattached():
    result = attach_snapshot_evidence(
        _handoff(duplicate=True), _snapshot([_event("RUNDOWN", "book-a", -120)]), now=NOW,
    )
    assert all(not candidate["market_evidence"] for candidate in result["model_handoff"]["team_event_candidates"])
    assert result["market_evidence_snapshot_bridge"]["ambiguous_events"] == 1
    assert result["market_evidence_snapshot_bridge"]["evidence_rows_attached"] == 0


def test_prop_without_existing_player_market_identity_is_not_inferred_from_team_market():
    handoff = _handoff()
    team = handoff["model_handoff"]["team_event_candidates"].pop()
    prop = dict(team, route="WOW_PROP_LANE", market_evidence=[])
    handoff["model_handoff"]["prop_candidates"] = [prop]
    result = attach_snapshot_evidence(handoff, _snapshot([_event("RUNDOWN", "book-a", -120)]), now=NOW)
    candidate = result["model_handoff"]["prop_candidates"][0]
    assert candidate["market_evidence"] == []
    assert result["market_evidence_snapshot_bridge"]["evidence_rows_attached"] == 0

def test_snapshot_can_seed_fresh_exact_prop_when_primary_feed_has_zero_props():
    handoff = _handoff()
    event = _event("RUNDOWN", "book-a", -120)
    event["bookmakers"][0]["markets"].append({
        "key": "pitcher_strikeouts",
        "last_update": "2026-09-14T19:59:00Z",
        "outcomes": [
            {"name": "Over", "description": "Example Pitcher", "price": -115, "point": 5.5},
            {"name": "Under", "description": "Example Pitcher", "price": -105, "point": 5.5},
        ],
    })
    result = attach_snapshot_evidence(handoff, _snapshot([event]), now=NOW)
    props = result["model_handoff"]["prop_candidates"]
    assert len(props) == 2
    assert {p["market_evidence"]["outcome_name"] for p in props} == {"Over", "Under"}
    assert all(p["route"] == "WOW_PROP_LANE" for p in props)
    assert all(p["research_ceiling"] == "RESEARCH_INTEREST" for p in props)
    assert all(p["probability_authority"] is False for p in props)
    assert all(p["can_execute"] is False for p in props)
    assert result["market_evidence_snapshot_bridge"]["prop_candidates_seeded"] == 2


def test_snapshot_never_seeds_stale_or_identity_incomplete_prop():
    handoff = _handoff()
    event = _event("RUNDOWN", "book-a", -120, updated="2026-09-14T19:30:00Z")
    event["bookmakers"][0]["markets"].append({
        "key": "pitcher_strikeouts",
        "last_update": "2026-09-14T19:30:00Z",
        "outcomes": [{"name": "Over", "description": "Example Pitcher", "price": -115, "point": 5.5}],
    })
    result = attach_snapshot_evidence(handoff, _snapshot([event]), now=NOW)
    assert result["model_handoff"]["prop_candidates"] == []
    assert result["market_evidence_snapshot_bridge"]["prop_seed_rows_stale_quarantined"] >= 1
