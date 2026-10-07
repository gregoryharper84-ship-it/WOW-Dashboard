from datetime import datetime, timezone

from v17.scout_research_promotion import evaluate_candidate, promote_handoff

NOW = datetime(2026, 9, 14, 14, 0, tzinfo=timezone.utc)


def candidate(*, evidence=None, blockers=None, contradictions=None, red_flags=None, domains=None):
    return {
        "official_event_id": "evt-1",
        "sport_key": "baseball_mlb",
        "commence_time": "2026-09-14T18:00:00Z",
        "home_team": "Home",
        "away_team": "Away",
        "route": "LLP_TEAM_BETTING_ENGINE",
        "controlling_specialist_route": "LLP_TEAM_BETTING_ENGINE",
        "market_evidence": evidence if evidence is not None else [],
        "research_domain_evidence": domains or {},
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


def domain(source_class, source, *, captured="2026-09-14T13:45:00Z", confirmed=True):
    return {
        "source_class": source_class,
        "source_provider": source,
        "captured_at": captured,
        "confirmed": confirmed,
    }


def complete_mlb_domains():
    return {
        "starter": [domain("LEAGUE_OFFICIAL", "mlb-official")],
        "lineup": [domain("TEAM_OFFICIAL", "team-official")],
        "bullpen": [domain("ESTABLISHED_STATS_PROVIDER", "stats-provider")],
        "weather": [domain("WEATHER_PROVIDER", "weather-provider")],
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


def test_fresh_single_market_source_promotes_only_to_low_interest():
    out = evaluate_candidate(candidate(evidence=[market()]), now=NOW)
    assert out["research_status"] == "RESEARCH_INTEREST_LOW"
    assert out["research_bookmaker_count"] == 1
    assert out["research_source_family_count"] == 1
    assert out["market_observations_are_one_source_family"] is True
    assert out["probability"] is None


def test_cross_book_market_breadth_is_not_independent_sporting_corroboration():
    out = evaluate_candidate(
        candidate(evidence=[market("a"), market("b"), market("c")]),
        now=NOW,
    )
    assert out["research_bookmaker_count"] == 3
    assert out["research_source_family_count"] == 1
    assert out["research_independent_source_count"] == 1
    assert out["research_status"] == "RESEARCH_INTEREST_LOW"
    assert out["research_reason"] == "REQUIRED_RESEARCH_DOMAINS_INCOMPLETE"


def test_partial_required_domains_with_independent_evidence_promotes_medium_only():
    out = evaluate_candidate(
        candidate(
            evidence=[market("a"), market("b")],
            domains={
                "starter": [domain("LEAGUE_OFFICIAL", "mlb-official")],
                "lineup": [domain("TEAM_OFFICIAL", "team-official")],
            },
        ),
        now=NOW,
    )
    assert out["required_research_domains"] == ["starter", "lineup", "bullpen", "market", "weather"]
    assert out["research_domain_completeness"] == 60.0
    assert set(out["missing_required_domains"]) == {"bullpen", "weather"}
    assert out["research_source_family_count"] == 2
    assert out["research_status"] == "RESEARCH_INTEREST_MEDIUM"
    assert out["research_reason"] == "PARTIAL_REQUIRED_DOMAINS_INDEPENDENT_EVIDENCE"


def test_complete_required_domains_with_independent_evidence_promotes_high():
    out = evaluate_candidate(
        candidate(evidence=[market("a"), market("b"), market("c")], domains=complete_mlb_domains()),
        now=NOW,
    )
    assert out["research_domain_completeness"] == 100.0
    assert out["missing_required_domains"] == []
    assert out["research_source_family_count"] >= 4
    assert out["research_status"] == "RESEARCH_INTEREST_HIGH"
    assert out["research_reason"] == "REQUIRED_DOMAINS_COMPLETE_INDEPENDENT_EVIDENCE"
    assert out["probability"] is None
    assert out["prediction_authority"] is False
    assert out["can_execute"] is False


def test_wrong_source_class_does_not_satisfy_required_domain():
    domains = complete_mlb_domains()
    domains["lineup"] = [domain("SECONDARY_MEDIA", "generic-news")]
    out = evaluate_candidate(candidate(evidence=[market()], domains=domains), now=NOW)
    assert out["required_domain_coverage"]["lineup"] is False
    assert "lineup" in out["missing_required_domains"]
    assert out["research_status"] != "RESEARCH_INTEREST_HIGH"


def test_high_impact_confirmation_requirement_is_enforced():
    domains = complete_mlb_domains()
    domains["starter"] = [
        {
            **domain("LEAGUE_OFFICIAL", "mlb-official", confirmed=False),
            "evidence_type": "STARTER_STATUS",
        }
    ]
    out = evaluate_candidate(candidate(evidence=[market()], domains=domains), now=NOW)
    assert out["required_domain_coverage"]["starter"] is False
    assert "starter" in out["missing_required_domains"]
    assert out["research_status"] != "RESEARCH_INTEREST_HIGH"


def test_material_conflict_quarantines_even_with_complete_evidence():
    out = evaluate_candidate(
        candidate(
            evidence=[market()],
            domains=complete_mlb_domains(),
            contradictions=["SAME_TIER_MATERIAL_CONFLICT"],
        ),
        now=NOW,
    )
    assert out["research_status"] == "QUARANTINED"
    assert out["red_team"]["status"] == "QUARANTINED"
    assert out["probability"] is None
    assert out["can_execute"] is False


def test_partial_source_blocker_caps_interest_at_low():
    out = evaluate_candidate(
        candidate(
            evidence=[market("a"), market("b"), market("c")],
            domains=complete_mlb_domains(),
            blockers=[{"reason_code": "SECONDARY_BLOCKED"}],
        ),
        now=NOW,
    )
    assert out["research_status"] == "RESEARCH_INTEREST_LOW"
    assert out["research_reason"] == "PARTIAL_SOURCE_BLOCKED"


def test_handoff_preserves_discovery_only_probability_governance():
    payload = {
        "can_execute": False,
        "model_handoff": {
            "team_event_candidates": [
                candidate(evidence=[market("a"), market("b")], domains=complete_mlb_domains())
            ],
            "prop_candidates": [],
        },
    }
    out = promote_handoff(payload, now=NOW)
    row = out["model_handoff"]["team_event_candidates"][0]
    assert row["research_status"] == "RESEARCH_INTEREST_HIGH"
    assert row["controlling_specialist_route"] == "LLP_TEAM_BETTING_ENGINE"
    assert row["probability"] is None
    assert row["probability_authority"] is False
    assert row["can_execute"] is False
    assert out["research_promotion"]["market_observations_are_one_source_family"] is True
    assert out["research_promotion"]["promotion_requires_required_domain_coverage"] is True
    assert out["research_promotion"]["final_probability_requires_controlling_specialist"] is True
    assert out["research_promotion"]["can_execute"] is False
