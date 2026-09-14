from datetime import datetime, timezone

from v17.scout_research_promotion import evaluate_candidate, promote_handoff

NOW = datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc)


def candidate(*, evidence=None, blockers=None, contradictions=None, red_flags=None):
    return {
        "official_event_id": "evt-1",
        "sport_key": "baseball_mlb",
        "commence_time": "2026-09-14T18:00:00Z",
        "home_team": "Home",
        "away_team": "Away",
        "route": "LLP_TEAM_BETTING_ENGINE",
        "controlling_specialist_route": "LLP_TEAM_BETTING_ENGINE",
        "market_evidence": evidence if evidence is not None else [],
        "market_evidence_source_blockers": blockers or [],
        "contradictory_evidence": contradictions or [],
        "red_team_flags": red_flags or [],
        "can_execute": False,
    }


def market(book="book-a", captured="2026-09-14T13:55:00Z"):
    return {
        "bookmaker": book,
        "market_key": "h2h",
        "outcome_name": "Home",
        "price": -120,
        "market_last_update": captured,
    }


def test_missing_evidence_stays_watch():
    out = evaluate_candidate(candidate(), now=NOW)
    assert out["research_status"] == "WATCH"
    assert out["research_reason"] == "RESEARCH_EVIDENCE_MISSING"
    assert out["probability"] is None
    assert out["can_execute"] is False


def test_stale_market_evidence_cannot_promote():
    out = evaluate_candidate(candidate(evidence=[market(captured="2026-09-14T12:00:00Z")]), now=NOW)
    assert out["research_status"] == "WATCH"
    assert out["research_reason"] == "NO_FRESH_RESEARCH_USABLE_EVIDENCE"


def test_fresh_single_source_promotes_only_to_low_interest():
    out = evaluate_candidate(candidate(evidence=[market()]), now=NOW)
    assert out["research_status"] == "RESEARCH_INTEREST_LOW"
    assert out["research_bookmaker_count"] == 1
    assert out["probability"] is None


def test_fresh_cross_book_evidence_promotes_medium_and_high():
    medium = evaluate_candidate(candidate(evidence=[market("a"), market("b")]), now=NOW)
    high = evaluate_candidate(candidate(evidence=[market("a"), market("b"), market("c")]), now=NOW)
    assert medium["research_status"] == "RESEARCH_INTEREST_MEDIUM"
    assert high["research_status"] == "RESEARCH_INTEREST_HIGH"


def test_material_conflict_quarantines_even_with_fresh_evidence():
    out = evaluate_candidate(candidate(evidence=[market()], contradictions=["SAME_TIER_MATERIAL_CONFLICT"]), now=NOW)
    assert out["research_status"] == "QUARANTINED"
    assert out["red_team"]["status"] == "QUARANTINED"
    assert out["probability"] is None
    assert out["can_execute"] is False


def test_partial_source_blocker_caps_interest_at_low():
    out = evaluate_candidate(candidate(evidence=[market("a"), market("b"), market("c")], blockers=[{"reason_code": "SECONDARY_BLOCKED"}]), now=NOW)
    assert out["research_status"] == "RESEARCH_INTEREST_LOW"


def test_handoff_preserves_discovery_only_probability_governance():
    payload = {
        "can_execute": False,
        "model_handoff": {
            "team_event_candidates": [candidate(evidence=[market("a"), market("b")])],
            "prop_candidates": [],
        },
    }
    out = promote_handoff(payload, now=NOW)
    row = out["model_handoff"]["team_event_candidates"][0]
    assert row["research_status"] == "RESEARCH_INTEREST_MEDIUM"
    assert row["controlling_specialist_route"] == "LLP_TEAM_BETTING_ENGINE"
    assert row["probability"] is None
    assert row["probability_authority"] is False
    assert row["can_execute"] is False
    assert out["research_promotion"]["final_probability_requires_controlling_specialist"] is True
    assert out["research_promotion"]["can_execute"] is False
