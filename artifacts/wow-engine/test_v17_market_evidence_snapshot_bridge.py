from __future__ import annotations

from datetime import datetime, timezone

from v17.market_evidence_snapshot_bridge import attach_snapshot_evidence
from v17.scout_research_promotion import evaluate_candidate


def _handoff(*, duplicate=False):
    candidate = {
        "official_event_id": "primary-evt-1",
        "sport_key": "baseball_mlb",
        "commence_time": "2026-09-14T20:00:00Z",
        "home_team": "Texas Rangers",
        "away_team": "Houston Astros",
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


def _event(provider, book, price, *, home="Texas Rangers", away="Houston Astros", start="2026-09-14T20:03:00Z"):
    return {
        "id": f"{provider}-evt",
        "sport_key": "baseball_mlb",
        "commence_time": start,
        "home_team": home,
        "away_team": away,
        "_wow_market_evidence": {
            "provider": provider,
            "capability": "events",
            "prediction_authority": False,
            "exact_line_authority": False,
            "research_only": True,
            "can_execute": False,
        },
        "bookmakers": [{
            "key": book,
            "title": book,
            "last_update": "2026-09-14T19:59:00Z",
            "markets": [{
                "key": "h2h",
                "last_update": "2026-09-14T19:59:00Z",
                "outcomes": [
                    {"name": "Texas Rangers", "price": price},
                    {"name": "Houston Astros", "price": 110},
                ],
            }],
        }],
    }


def _snapshot(events):
    return {
        "status": "MARKET_EVIDENCE_CAPTURED",
        "generated_at": "2026-09-14T20:00:00Z",
        "events": events,
        "reconciliation": {"captured_rows": len(events), "balanced": True},
        "prediction_authority": False,
        "exact_line_authority": False,
        "research_only": True,
        "can_execute": False,
    }


def test_exact_event_match_attaches_cross_book_evidence_without_probability_authority():
    result = attach_snapshot_evidence(
        _handoff(),
        _snapshot([
            _event("RUNDOWN", "book-a", -120),
            _event("SHARPAPI", "book-b", -118),
        ]),
    )
    candidate = result["model_handoff"]["team_event_candidates"][0]
    assert len(candidate["market_evidence"]) == 4
    assert {row["bookmaker"] for row in candidate["market_evidence"]} == {"book-a", "book-b"}
    assert all(row["prediction_authority"] is False for row in candidate["market_evidence"])
    assert all(row["can_execute"] is False for row in candidate["market_evidence"])
    assert candidate["probability"] is None
    assert result["market_evidence_snapshot_bridge"]["candidates_touched"] == 1
    assert result["market_evidence_snapshot_bridge"]["evidence_rows_attached"] == 4
    assert result["can_execute"] is False

    evaluation = evaluate_candidate(candidate, now=datetime(2026, 9, 14, 20, 1, tzinfo=timezone.utc))
    assert evaluation["research_status"] in {"RESEARCH_INTEREST_MEDIUM", "RESEARCH_INTEREST_HIGH"}
    assert evaluation["probability"] is None
    assert evaluation["prediction_authority"] is False


def test_team_or_time_mismatch_never_attaches():
    snapshot = _snapshot([
        _event("RUNDOWN", "book-a", -120, home="Seattle Mariners"),
        _event("SHARPAPI", "book-b", -118, start="2026-09-15T05:00:00Z"),
    ])
    result = attach_snapshot_evidence(_handoff(), snapshot)
    candidate = result["model_handoff"]["team_event_candidates"][0]
    assert candidate["market_evidence"] == []
    assert result["market_evidence_snapshot_bridge"]["evidence_rows_attached"] == 0
    assert result["market_evidence_snapshot_bridge"]["unmatched_events"] == 2


def test_ambiguous_candidate_identity_is_left_unattached():
    result = attach_snapshot_evidence(_handoff(duplicate=True), _snapshot([_event("RUNDOWN", "book-a", -120)]))
    assert all(not candidate["market_evidence"] for candidate in result["model_handoff"]["team_event_candidates"])
    assert result["market_evidence_snapshot_bridge"]["ambiguous_events"] == 1
    assert result["market_evidence_snapshot_bridge"]["evidence_rows_attached"] == 0


def test_prop_without_existing_player_market_identity_is_not_inferred_from_team_market():
    handoff = _handoff()
    team = handoff["model_handoff"]["team_event_candidates"].pop()
    prop = dict(team, route="WOW_PROP_LANE", market_evidence=[])
    handoff["model_handoff"]["prop_candidates"] = [prop]
    result = attach_snapshot_evidence(handoff, _snapshot([_event("RUNDOWN", "book-a", -120)]))
    candidate = result["model_handoff"]["prop_candidates"][0]
    assert candidate["market_evidence"] == []
    assert result["market_evidence_snapshot_bridge"]["evidence_rows_attached"] == 0
