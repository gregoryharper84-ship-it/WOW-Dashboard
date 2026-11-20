"""Offline provenance, chronology, conflict and rescore-isolation regressions."""
from datetime import datetime, timezone

from v17.news_event_research_handoff import reconcile_news_handoff

NOW = datetime(2026, 11, 17, 18, 0, tzinfo=timezone.utc)
URL = "https://league.example/news"
EVENT = {
    "sport_key": "basketball_nba",
    "official_event_id": "NBA-2026-11-17-A",
    "event_start_utc": "2026-11-17T23:30:00Z",
}
DISCOVERY = {
    "source_status": "OK",
    "source_code": "SOURCE_FETCH_OK",
    "source_observed_at": "2026-11-17T17:00:00Z",
    "sport_key": "basketball_nba",
    "articles": [{"source_url": URL, "title": "Team update",
                  "official_event_identity_verified": False,
                  "historical_pregame_eligible": False}],
    "can_execute": False, "prediction_authority": False,
}
FACT = {
    "source_url": URL,
    "sport_key": EVENT["sport_key"],
    "official_event_id": EVENT["official_event_id"],
    "publisher_published_at_utc": "2026-11-17T16:00:00Z",
    "independently_reviewed_at_utc": "2026-11-17T17:30:00Z",
    "independent_review_ref": "external-source-check-1",
    "source_group": "official-league",
    "source_class": "LEAGUE_OFFICIAL",
    "confirmed": True,
    "evidence_kind": "INJURY_STATUS",
    "subject_id": "player-22",
    "fact_value": "OUT",
    "material_change": True,
}


def _result(discovery=None, event=None, reviewed=None, at=NOW):
    return reconcile_news_handoff(
        DISCOVERY if discovery is None else discovery,
        EVENT if event is None else event,
        [] if reviewed is None else reviewed,
        observed_at=at,
    )


def test_unverified_index_article_never_enters_model():
    out = _result()
    assert out["research_status"] == "NEWS_DISCOVERY_UNVERIFIED"
    assert out["research_rescore_requested"] is False
    assert out["model_feature_eligible"] is False
    assert out["calibrated_probability"] is None
    assert out["prediction_authority"] is False
    assert out["can_execute"] is False


def test_explicit_official_pregame_review_requests_not_executes_rescore():
    out = _result(reviewed=[FACT])
    assert out["research_status"] == "NEWS_RESEARCH_RESCORE_REQUESTED"
    assert out["research_rescore_requested"] is True
    assert out["model_rescore_verified"] is False
    assert out["model_feature_eligible"] is False
    assert out["historical_pregame_eligible"] is False
    assert out["news_context"][0]["fact_value"] == "OUT"
    assert len(out["receipt_sha256"]) == 64
    assert out == _result(reviewed=[FACT])


def test_forged_prediction_authority_and_wrong_sport_are_blocked():
    assert _result(discovery={**DISCOVERY, "prediction_authority": True})["research_status"] == "NEWS_DISCOVERY_AUTHORITY_INVALID"
    assert _result(discovery={**DISCOVERY, "sport_key": "icehockey_nhl"})["research_status"] == "NEWS_SPORT_MISMATCH"


def test_missing_event_or_naive_time_fails_closed():
    assert _result(event={**EVENT, "official_event_id": ""})["research_status"] == "NEWS_CANONICAL_EVENT_UNVERIFIED"
    assert _result(event={**EVENT, "event_start_utc": "2026-11-17T23:30:00"})["research_status"] == "NEWS_CANONICAL_EVENT_UNVERIFIED"


def test_post_event_discovery_never_backfills_pregame_evidence():
    assert _result(at=datetime(2026, 11, 18, tzinfo=timezone.utc))["research_status"] == "NEWS_EVENT_ALREADY_STARTED"
    bad = {**DISCOVERY, "source_observed_at": "2026-11-18T00:00:00Z"}
    assert _result(discovery=bad)["research_status"] == "NEWS_DISCOVERY_TIME_INVALID"


def test_index_seendate_is_not_publisher_published_time():
    unverified = {**FACT, "publisher_published_at_utc": None}
    assert _result(reviewed=[unverified])["research_status"] == "NEWS_DISCOVERY_UNVERIFIED"
    late = {**FACT, "publisher_published_at_utc": "2026-11-18T00:00:00Z"}
    assert _result(reviewed=[late])["research_status"] == "NEWS_DISCOVERY_UNVERIFIED"


def test_unmatched_url_and_event_are_ignored():
    assert _result(reviewed=[{**FACT, "source_url": "https://another.example/path"}])["research_status"] == "NEWS_DISCOVERY_UNVERIFIED"
    assert _result(reviewed=[{**FACT, "official_event_id": "OTHER-GAME"}])["research_status"] == "NEWS_DISCOVERY_UNVERIFIED"


def test_beat_reporter_high_impact_requires_independent_confirmations():
    one = {**FACT, "source_class": "PRIMARY_BEAT_REPORTER", "source_group": "reporter-1"}
    assert _result(reviewed=[one])["research_status"] == "NEWS_HIGH_IMPACT_CONFIRMATION_REQUIRED"
    second = {**one, "source_group": "reporter-2", "independent_review_ref": "source-review-2"}
    assert _result(reviewed=[one, second])["research_status"] == "NEWS_SOURCE_CONFLICT"
    distinct = "https://another-league.example/second-report"
    with_distinct_article = {**DISCOVERY, "articles": DISCOVERY["articles"] + [{"source_url": distinct, "title": "Separate independent official report"}]}
    second = {**second, "source_url": distinct}
    assert _result(discovery=with_distinct_article, reviewed=[one, second])["research_status"] == "NEWS_RESEARCH_RESCORE_REQUESTED"


def test_conflicting_sources_quarantined_before_rescore():
    second_url = "https://other.example/team-roster"
    other = {**FACT, "source_url": second_url, "source_group": "team",
             "source_class": "TEAM_OFFICIAL", "fact_value": "ACTIVE"}
    discovery = {**DISCOVERY, "articles": DISCOVERY["articles"] + [{"source_url": second_url}]}
    out = _result(discovery=discovery, reviewed=[FACT, other])
    assert out["research_status"] == "NEWS_SOURCE_CONFLICT"
    assert out["research_rescore_requested"] is False
    assert out["source_conflicts"] == ["CONTRADICTORY_SOURCE_FACTS"]


def test_nonmaterial_news_is_context_only_even_when_reviewed():
    out = _result(reviewed=[{**FACT, "material_change": False}])
    assert out["research_status"] == "NEWS_CONTEXT_RESEARCH_ONLY"
    assert out["research_rescore_requested"] is False


def test_blocked_source_returns_typed_receipt_without_throwing():
    out = _result(discovery={**DISCOVERY, "source_status": "BLOCKED", "source_code": "SOURCE_HTTP_429"})
    assert out["research_status"] == "NEWS_DISCOVERY_BLOCKED"
    assert out["source_code"] == "SOURCE_HTTP_429"
    assert out["can_execute"] is False


def test_malformed_or_stale_article_fails_closed():
    assert _result(discovery={**DISCOVERY, "articles": None})["research_status"] == "NEWS_DISCOVERY_SHAPE_INVALID"
    old = {**FACT, "publisher_published_at_utc": "2026-11-16T01:00:00Z"}
    assert _result(reviewed=[old])["research_status"] == "NEWS_DISCOVERY_UNVERIFIED"


def test_scout_handoff_surfaces_context_without_promoting_probability():
    from v17.sport_scout_enrichment import enrich_candidate
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    late_event = {**EVENT, "event_start_utc": (now + timedelta(days=1)).isoformat()}
    fresh_discovery = {**DISCOVERY, "source_observed_at": (now - timedelta(hours=2)).isoformat()}
    fresh_fact = {**FACT,
                  "publisher_published_at_utc": (now - timedelta(hours=3)).isoformat(),
                  "independently_reviewed_at_utc": (now - timedelta(hours=1)).isoformat()}
    candidate = {
        "sport_key": late_event["sport_key"],
        "official_event_id": late_event["official_event_id"],
        "event_start_utc": late_event["event_start_utc"],
        "news_discovery_receipt": fresh_discovery,
        "news_reviewed_facts": [fresh_fact],
    }
    row = enrich_candidate(candidate, "TEAM_EVENT")
    handoff = row["news_research_handoff"]
    assert handoff["research_status"] == "NEWS_RESEARCH_RESCORE_REQUESTED"
    assert handoff["model_rescore_verified"] is False
    assert handoff["model_feature_eligible"] is False
    assert row["route"] == "LLP_TEAM_BETTING_ENGINE"
    assert row["can_execute"] is False


def test_inconsistent_duplicate_materiality_quarantined_in_any_order():
    duplicate = {**FACT, "material_change": False}
    for evidence in ([FACT, duplicate], [duplicate, FACT]):
        out = _result(reviewed=evidence)
        assert out["research_status"] == "NEWS_SOURCE_CONFLICT"
        assert out["source_conflicts"] == ["MATERIALITY_DISAGREEMENT"]
        assert out["research_rescore_requested"] is False


def test_bad_unhashable_json_fields_cannot_crash_discovery_or_candidates():
    assert _result(reviewed=[{**FACT, "source_url": ["bad"]}])["research_status"] == "NEWS_DISCOVERY_UNVERIFIED"
    assert _result(reviewed=[{**FACT, "source_class": {"bad": 1}}])["research_status"] == "NEWS_DISCOVERY_UNVERIFIED"
    from v17.sport_scout_enrichment import enrich_candidate
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    future_event = {**EVENT, "event_start_utc": (now + timedelta(days=1)).isoformat()}
    recent_discovery = {**DISCOVERY, "source_observed_at": (now - timedelta(hours=1)).isoformat()}
    row = enrich_candidate({**future_event, "news_discovery_receipt": recent_discovery,
                            "news_reviewed_facts": [{**FACT, "source_url": ["bad"]}]}, "TEAM_EVENT")
    assert row["news_research_handoff"]["research_status"] == "NEWS_DISCOVERY_UNVERIFIED"
    assert row["can_execute"] is False


def test_roof_status_secondary_requires_independent_corrob():
    secondary = {**FACT, "evidence_kind": "ROOF_STATUS",
                 "source_class": "SECONDARY_MEDIA", "source_group": "reporter-x"}
    assert _result(reviewed=[secondary])["research_status"] == "NEWS_HIGH_IMPACT_CONFIRMATION_REQUIRED"


def test_syndicated_headline_copies_cannot_masquerade_as_independent_confirmation():
    """Two domains and groups cannot turn a duplicated headline into 2 sources."""
    second_url = "https://second.example/republished"
    discovery = {
        **DISCOVERY,
        "articles": [
            {"source_url": URL,
             "title": "Lions moving on after player joins injury reserve"},
            {"source_url": second_url,
             "title": "LIONS moving on — after player joins injury reserve!"},
        ],
    }
    first = {**FACT, "source_class": "PRIMARY_BEAT_REPORTER",
             "source_group": "reporter-domain-a"}
    second = {**first, "source_url": second_url,
              "source_group": "reporter-domain-b",
              "independent_review_ref": "review-b"}
    output = _result(discovery=discovery, reviewed=[first, second])
    assert output["research_status"] == "NEWS_HIGH_IMPACT_CONFIRMATION_REQUIRED"
    assert output["research_rescore_requested"] is False
    assert output["model_rescore_verified"] is False
    assert output["source_conflicts"] == ["SYNDICATED_OR_UNATTRIBUTED_HEADLINES"]
    assert len(output["news_context"]) == 2  # no lost provenance rows
    assert output["can_execute"] is False


def test_distinct_headlines_preserve_separate_corrob_when_reviewed():
    second_url = "https://second.example/independent"
    discovery = {
        **DISCOVERY,
        "articles": [
            {"source_url": URL, "title": "Report from beat reporter on availability"},
            {"source_url": second_url, "title": "Second independently authored player update"},
        ],
    }
    first = {**FACT, "source_class": "PRIMARY_BEAT_REPORTER",
             "source_group": "reporter-domain-a"}
    second = {**first, "source_url": second_url,
              "source_group": "reporter-domain-b",
              "independent_review_ref": "review-b"}
    output = _result(discovery=discovery, reviewed=[first, second])
    assert output["research_status"] == "NEWS_RESEARCH_RESCORE_REQUESTED"
    assert output["research_rescore_requested"] is True
    assert output["model_rescore_verified"] is False
    assert output["model_feature_eligible"] is False
    assert len(output["news_context"]) == 2


def test_unknown_secondary_headline_independence_holds():
    second_url = "https://second.example/no-title"
    discovery = {
        **DISCOVERY,
        "articles": [
            {"source_url": URL, "title": "First source reported player injury"},
            {"source_url": second_url},
        ],
    }
    first = {**FACT, "source_class": "SECONDARY_MEDIA",
             "source_group": "source-a"}
    second = {**first, "source_url": second_url,
              "source_group": "source-b",
              "independent_review_ref": "review-b"}
    output = _result(discovery=discovery, reviewed=[first, second])
    assert output["research_status"] == "NEWS_HIGH_IMPACT_CONFIRMATION_REQUIRED"
    assert output["source_conflicts"] == ["SYNDICATED_OR_UNATTRIBUTED_HEADLINES"]
    assert len(output["news_context"]) == 2


def test_official_injury_report_not_blocked_by_headline_absence():
    output = _result(reviewed=[FACT])
    assert output["research_status"] == "NEWS_RESEARCH_RESCORE_REQUESTED"
    assert output["research_rescore_requested"] is True
    assert output["model_rescore_verified"] is False
    assert output["can_execute"] is False


def test_mixed_official_and_syndicated_secondary_still_accepts_official_fact():
    secondary_url = "https://second.example/wire-copy"
    discovery = {
        **DISCOVERY,
        "articles": [
            {"source_url": URL, "title": "League roster bulletin"},
            {"source_url": secondary_url, "title": "League roster bulletin"},
        ],
    }
    secondary = {**FACT, "source_url": secondary_url,
                 "source_group": "wire-distributor",
                 "source_class": "SECONDARY_MEDIA",
                 "independent_review_ref": "review-secondary"}
    out = _result(discovery=discovery, reviewed=[FACT, secondary])
    assert out["research_status"] == "NEWS_RESEARCH_RESCORE_REQUESTED"
    assert out["research_rescore_requested"] is True
    assert out["model_rescore_verified"] is False
    assert len(out["news_context"]) == 2


def test_malformed_discovery_urls_cannot_crash_headline_provenance_check():
    discovery = {
        **DISCOVERY,
        "articles": [
            {"source_url": ["not-hashable"], "title": "Bad payload"},
            {"source_url": URL, "title": "Official league injury bulletin"},
        ],
    }
    out = _result(discovery=discovery, reviewed=[FACT])
    assert out["research_status"] == "NEWS_RESEARCH_RESCORE_REQUESTED"
    assert out["can_execute"] is False
