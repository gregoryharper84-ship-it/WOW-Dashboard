"""Regression tests for default research-only matchup discovery."""
from datetime import datetime, timezone

from v17.matchup_discovery import discover_matchup, feature_requirements, profile_for
from v17.sport_scout_enrichment import enrich_candidate

NOW = datetime(2026, 11, 19, 17, 5, tzinfo=timezone.utc)
START = "2026-11-19T23:00:00Z"


def candidate(sport="icehockey_nhl"):
    return {
        "official_event_id": "evt-10",
        "sport_key": sport,
        "commence_time": START,
        "home_team": "Home",
        "away_team": "Away",
        "can_execute": False,
    }


def row(*, source_class="ESTABLISHED_STATS_PROVIDER", captured_at="2026-11-19T17:00:00Z",
        confirmed=True, event_id="evt-10", value=1):
    return {
        "official_event_id": event_id,
        "source_id": "source-1",
        "source_class": source_class,
        "captured_at": captured_at,
        "confirmed": confirmed,
        "value": value,
    }


def test_every_requested_sport_has_specialist_feature_map():
    expected = {
        "americanfootball_nfl": "FOOTBALL", "americanfootball_ncaaf": "FOOTBALL",
        "basketball_nba": "BASKETBALL", "basketball_wnba": "BASKETBALL",
        "basketball_ncaab": "BASKETBALL", "baseball_mlb": "BASEBALL",
        "icehockey_nhl": "HOCKEY", "soccer_epl": "SOCCER",
        "mma_ufc": "COMBAT", "boxing": "COMBAT",
        "tennis_atp": "TENNIS", "golf_pga": "GOLF", "cricket_ipl": "CRICKET",
    }
    for sport, profile in expected.items():
        assert profile_for(sport) == profile
        requirements = feature_requirements(sport)
        assert len(requirements["SPORT_SPECIFIC"]) >= 5
        assert len(requirements["GAME_SCRIPT"]) >= 3


def test_default_audit_is_requested_but_empty_evidence_never_claims_completed_review():
    result = discover_matchup(candidate(), now=NOW)
    assert result["review_requested"] is True
    assert result["review_status"] == "MATCHUP_EVIDENCE_NOT_OBSERVED"
    assert "confirmed_starting_goalies" in result["missing_critical_features"]
    assert "five_on_five_xg_and_shots" in result["missing_critical_features"]
    assert result["screened_count"] == 0
    assert result["prediction_authority"] is False
    assert result["can_execute"] is False
    assert result["confidence_or_model_probability_created"] is False


def test_screened_sourced_evidence_is_not_specialist_probability_or_verification():
    c = candidate("basketball_nba")
    c["matchup_metric_evidence"] = {
        "opponent_adjusted_strength": row(value=0),
        "minutes_and_foul_rotation": row(),
    }
    result = discover_matchup(c, now=NOW)
    assert result["screened_features"] == ["opponent_adjusted_strength", "minutes_and_foul_rotation"]
    assert result["review_status"] == "PARTIAL_MATCHUP_REVIEW"
    assert result["source_screening_is_not_independent_model_verification"] is True
    assert result["specialist_rescore_verified"] is False
    assert "opponent_adjusted_strength" not in result["missing_critical_features"]


def test_wrong_event_future_stale_unverified_and_conflicted_sources_never_pass():
    c = candidate()
    c["matchup_metric_evidence"] = {
        "confirmed_starting_goalies": row(event_id="wrong", source_class="TEAM_OFFICIAL"),
        "goalie_gsax_and_workload": row(captured_at="2026-11-19T18:00:00Z"),
        "five_on_five_xg_and_shots": row(captured_at="2026-11-17T17:00:00Z"),
        "power_play_vs_penalty_kill": row(source_class="SOCIAL_UNVERIFIED"),
        "score_effects_and_empty_net": {**row(), "conflict": True},
    }
    result = discover_matchup(c, now=NOW)
    assert result["review_status"] == "CONFLICT_QUARANTINED"
    assert result["invalid_or_stale_features"]["confirmed_starting_goalies"] == "EVENT_ID_MISMATCH"
    assert result["invalid_or_stale_features"]["goalie_gsax_and_workload"] == "TIMESTAMP_NOT_VALID_PREGAME"
    assert result["invalid_or_stale_features"]["five_on_five_xg_and_shots"] == "STALE_OR_UNCONFIRMED_SOURCE"
    assert result["invalid_or_stale_features"]["score_effects_and_empty_net"] == "CONFLICT_QUARANTINED"


def test_evidence_captured_after_kickoff_is_not_replayed_as_pregame():
    c = candidate()
    c["matchup_metric_evidence"] = {
        "opponent_adjusted_strength": row(captured_at="2026-11-19T23:01:00Z"),
    }
    result = discover_matchup(c, now=datetime(2026, 11, 20, tzinfo=timezone.utc))
    assert result["review_status"] == "PREGAME_WINDOW_CLOSED"
    assert result["screened_count"] == 0
    assert result["invalid_or_stale_features"]["opponent_adjusted_strength"] == "TIMESTAMP_NOT_VALID_PREGAME"


def test_nightly_enrichment_requests_matchup_review_for_prop_and_event():
    for lane in ("PROP", "TEAM_EVENT"):
        candidate_row = candidate("americanfootball_nfl")
        enriched = enrich_candidate(candidate_row, lane)
        assert enriched["matchup_discovery"]["review_requested"] is True
        assert enriched["matchup_discovery"]["profile"] == "FOOTBALL"
        assert "ol_vs_dl_pressure" in enriched["matchup_discovery"]["missing_critical_features"]
        assert enriched["matchup_discovery"]["prediction_authority"] is False
        assert enriched["can_execute"] is False


def test_existing_market_evidence_is_screened_only_when_timestamped_exact_quote():
    c = candidate("basketball_nba")
    c["market_evidence"] = [{
        "market_key": "totals", "outcome_name": "Over", "point": 225.5,
        "price": -110, "bookmaker": "book-1",
        "market_last_update": "2026-11-19T17:00:00Z",
    }]
    result = discover_matchup(c, now=NOW)
    assert "exact_current_quote" in result["screened_features"]
    assert "opening_to_current_movement" in result["missing_features"]
    assert "cross_book_comparison" in result["missing_features"]
    c["market_evidence"][0]["market_last_update"] = "2026-11-19T16:00:00Z"
    stale = discover_matchup(c, now=NOW)
    assert "exact_current_quote" not in stale["screened_features"]


def test_existing_typed_domain_metrics_are_reused_with_exact_identity():
    c = candidate("americanfootball_nfl")
    c["research_domain_evidence"] = {
        "matchup": {
            "official_event_id": "evt-10",
            "source_id": "stats-feed-1",
            "source_class": "ESTABLISHED_STATS_PROVIDER",
            "captured_at": "2026-11-19T17:00:00Z",
            "confirmed": True,
            "matchup_metrics": {"ol_vs_dl_pressure": {"home": 0.3, "away": 0.4}},
        }
    }
    result = discover_matchup(c, now=NOW)
    assert "ol_vs_dl_pressure" in result["screened_features"]
    assert result["prediction_authority"] is False


def test_interactive_team_event_barrier_also_attaches_matchup_audit(monkeypatch):
    from types import SimpleNamespace
    from v17 import team_event_request_runtime as runtime

    monkeypatch.setattr(
        runtime, "execute_envelope",
        lambda _: SimpleNamespace(status="SUCCEEDED", blockers=[], output={}),
    )
    req = runtime.TeamEventRequest(
        requester_host_identity="LLP_TEAM_BETTING_ENGINE",
        research_run_id="matchup-regression",
        requested_slate_date="2026-11-19",
        requested_timezone="America/Chicago",
        event_key="nhl-evt-10",
        official_event_id="evt-10",
        event_start_time_utc=START,
        sport="NHL",
        league="NHL",
        settlement_basis="FULL_GAME_INCLUDING_OVERTIME",
        home_team="Home",
        away_team="Away",
        source_snapshot_id="snapshot-10",
    )
    result = runtime._run_mandatory_scout_research(req)
    assert result["status"] == "SUCCEEDED"
    assert result["matchup_discovery"]["review_requested"] is True
    assert result["matchup_discovery"]["profile"] == "HOCKEY"
    assert "confirmed_starting_goalies" in result["matchup_discovery"]["missing_critical_features"]
    assert result["matchup_discovery"]["can_execute"] is False


def test_nhl_goalie_confirmation_requires_two_distinct_official_providers():
    c = candidate()
    one = row(source_class="TEAM_OFFICIAL",
              value={"home": "HomeStarter", "away": "AwayStarter"})
    one["evidence_type"] = "STARTING_GOALIE_STATUS"
    c["matchup_metric_evidence"] = {"confirmed_starting_goalies": [one]}
    first = discover_matchup(c, now=NOW)
    assert first["invalid_or_stale_features"]["confirmed_starting_goalies"] == (
        "TWO_INDEPENDENT_OFFICIAL_GOALIE_SOURCES_REQUIRED"
    )
    second = {**one, "source_class": "LEAGUE_OFFICIAL", "source_id": "league-2"}
    c["matchup_metric_evidence"]["confirmed_starting_goalies"].append(second)
    reviewed = discover_matchup(c, now=NOW)
    assert "confirmed_starting_goalies" in reviewed["screened_features"]
    assert reviewed["prediction_authority"] is False


def test_shared_registered_sport_bridge_adds_audit_without_changing_scorer(monkeypatch):
    from v17 import team_event_bridge_runtime as bridge
    from v17 import team_event_request_runtime as runtime

    req = runtime.TeamEventRequest(
        requester_host_identity="LLP_TEAM_BETTING_ENGINE",
        research_run_id="shared-bridge-matchup",
        requested_slate_date="2026-11-19",
        requested_timezone="UTC",
        event_key="nhl-evt-10",
        official_event_id="evt-10",
        event_start_time_utc=START,
        sport="NHL",
        league="NHL",
        settlement_basis="OFFICIAL_FINAL_RESULT",
        home_team="Home",
        away_team="Away",
        source_snapshot_id="snapshot-10",
    )
    monkeypatch.setitem(
        bridge.TEAM_EVENT_BRIDGES, "NHL",
        bridge.TeamEventBridgeRegistration(
            sport="NHL", adapter_name="UNIT_TEST_ONLY",
            controlling_specialist="UNIT_TEST_SPECIALIST",
            required_inputs=(),
            scorer=lambda *_args, **_kwargs: {"can_execute": False, "test_scorer_value": 7},
            standard_package_validation=False,
        )
    )
    monkeypatch.setattr(
        bridge, "_attach_feature_consumption_receipt",
        lambda _req, _registration, result: dict(result),
    )
    result = bridge.score_registered_team_event_request(req, event_api=None)
    assert result["test_scorer_value"] == 7
    assert result["matchup_discovery"]["profile"] == "HOCKEY"
    assert result["matchup_discovery"]["review_requested"] is True
    assert result["can_execute"] is False


def test_empty_payloads_do_not_count_as_reviewed_metrics():
    for empty in ("", "   ", [], {}, None):
        c = candidate("basketball_nba")
        c["matchup_metric_evidence"] = {"opponent_adjusted_strength": row(value=empty)}
        report = discover_matchup(c, now=NOW)
        assert report["screened_count"] == 0
        assert report["invalid_or_stale_features"]["opponent_adjusted_strength"] == "MEASUREMENT_MISSING"


def test_high_impact_metric_confirmation_cannot_be_bypassed_by_missing_evidence_type():
    c = candidate("icehockey_nhl")
    c["matchup_metric_evidence"] = {
        "availability_and_lineups": row(source_class="TEAM_OFFICIAL", confirmed=False),
        "confirmed_starting_goalies": [
            row(source_class="TEAM_OFFICIAL", confirmed=False,
                value={"home": "H", "away": "A"}),
            {**row(source_class="LEAGUE_OFFICIAL", confirmed=True,
                   value={"home": "H", "away": "A"}), "source_id": "second"},
        ],
    }
    report = discover_matchup(c, now=NOW)
    assert "availability_and_lineups" in report["missing_critical_features"]
    assert report["invalid_or_stale_features"]["availability_and_lineups"] == "STALE_OR_UNCONFIRMED_SOURCE"
    assert "confirmed_starting_goalies" in report["missing_critical_features"]


def test_same_tier_conflicting_official_starters_are_quarantined():
    c = candidate()
    one = row(source_class="TEAM_OFFICIAL",
              value={"home": "HomeStarterA", "away": "AwayStarter"})
    two = {**one, "source_id": "second-team-source",
           "value": {"home": "HomeStarterB", "away": "AwayStarter"}}
    c["matchup_metric_evidence"] = {"confirmed_starting_goalies": [one, two]}
    report = discover_matchup(c, now=NOW)
    assert report["review_status"] == "CONFLICT_QUARANTINED"
    assert "confirmed_starting_goalies" in report["missing_critical_features"]
    assert report["invalid_or_stale_features"]["confirmed_starting_goalies"] == "CONFLICT_QUARANTINED"


def test_two_distinct_team_scopes_are_not_contradictory():
    c = candidate("basketball_nba")
    c["matchup_metric_evidence"] = {
        "recent_form_vs_baseline": [
            {**row(value={"net_rating": 5}), "side": "home"},
            {**row(value={"net_rating": -2}), "side": "away",
             "source_id": "stats-source-2"},
        ]
    }
    report = discover_matchup(c, now=NOW)
    assert "recent_form_vs_baseline" in report["screened_features"]


def test_multiple_opposing_book_quotes_do_not_trigger_personnel_conflict():
    c = candidate("basketball_nba")
    c["market_evidence"] = [
        {"market_key": "h2h", "outcome_name": side, "price": odds,
         "bookmaker": "book-1", "market_last_update": "2026-11-19T17:00:00Z"}
        for side, odds in (("Home", -120), ("Away", 100))
    ]
    report = discover_matchup(c, now=NOW)
    assert "exact_current_quote" in report["screened_features"]
    assert report["review_status"] != "CONFLICT_QUARANTINED"
