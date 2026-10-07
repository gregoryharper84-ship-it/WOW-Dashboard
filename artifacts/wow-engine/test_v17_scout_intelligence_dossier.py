from datetime import datetime, timedelta, timezone

from pick_request_runtime_core import PickRequestRow
from v17.scout_intelligence_dossier import build_scout_dossier

NOW = datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc)


def _market(book, price=-110, *, captured=None):
    return {
        "bookmaker": book,
        "market_key": "h2h",
        "outcome_name": "Home",
        "price": price,
        "market_last_update": captured or (NOW - timedelta(minutes=5)).isoformat(),
        "source_class": "SPORTSBOOK_FEED",
    }


def _domain(source_class, source, *, captured=None, confirmed=True, **extra):
    return {
        "source_class": source_class,
        "source_provider": source,
        "captured_at": captured or (NOW - timedelta(minutes=10)).isoformat(),
        "confirmed": confirmed,
        **extra,
    }


def _nba_candidate():
    return {
        "official_event_id": "nba-pre-1",
        "sport_key": "basketball_nba",
        "sport_title": "NBA",
        "commence_time": (NOW + timedelta(hours=4)).isoformat(),
        "home_team": "Home",
        "away_team": "Away",
        "season_year": 2026,
        "season_type": 1,
        "season_slug": "preseason",
        "season_phase": "PRESEASON",
        "season_phase_source": "ESPN_SCOREBOARD",
        "market_evidence": [_market("a"), _market("b"), _market("c")],
        "research_domain_evidence": {
            "availability": [_domain("LEAGUE_OFFICIAL", "nba-official")],
            "rotation": [_domain("ESTABLISHED_STATS_PROVIDER", "rotation-feed")],
            "matchup": [_domain("ESTABLISHED_STATS_PROVIDER", "matchup-feed")],
        },
        "can_execute": False,
    }


def test_complete_nba_dossier_separates_market_breadth_from_independent_research():
    dossier = build_scout_dossier(_nba_candidate(), now=NOW)

    assert dossier["event_context"]["season_phase"] == "PRESEASON"
    assert dossier["event_context"]["regime_certainty"] == "CONFIRMED"
    assert dossier["required_domain_coverage"] == {
        "availability": True,
        "rotation": True,
        "matchup": True,
        "market": True,
    }
    assert dossier["domain_completeness"] == 100.0
    assert dossier["market_state"]["bookmaker_count"] == 3
    assert dossier["market_state"]["source_family_count"] == 1
    assert dossier["source_independence_graph"]["MARKET"]["independent_family"] is False
    assert dossier["red_team_results"]["status"] == "PASSED"
    assert dossier["research_worker_barrier_status"] == "READY"
    assert all(
        report["research_status"] == "READY"
        for report in dossier["research_worker_reports"].values()
    )
    assert dossier["prediction_authority"] is False
    assert dossier["can_execute"] is False
    assert len(dossier["dossier_hash"]) == 64


def test_dynamic_freshness_tightens_volatile_status_near_event():
    candidate = _nba_candidate()
    candidate["commence_time"] = (NOW + timedelta(minutes=20)).isoformat()
    candidate["research_domain_evidence"]["availability"] = [
        _domain(
            "LEAGUE_OFFICIAL",
            "nba-official",
            captured=(NOW - timedelta(minutes=40)).isoformat(),
        )
    ]

    dossier = build_scout_dossier(candidate, now=NOW)
    availability = next(
        row for row in dossier["evidence_ledger"] if row["domain"] == "availability"
    )

    assert availability["base_max_age_minutes"] == 720
    assert availability["dynamic_max_age_minutes"] == 15
    assert availability["dynamic_stale"] is True
    assert availability["research_usable"] is False
    assert dossier["required_domain_coverage"]["availability"] is False
    assert "availability" in dossier["missing_required_domains"]
    assert dossier["refresh_plan"]["status"] == "FINAL_REFRESH_DUE"
    assert dossier["refresh_plan"]["checkpoint"] == "T_MINUS_30M"


def test_material_same_claim_conflict_is_generated_by_red_team():
    candidate = {
        "official_event_id": "mlb-1",
        "sport_key": "baseball_mlb",
        "commence_time": (NOW + timedelta(hours=3)).isoformat(),
        "home_team": "Home",
        "away_team": "Away",
        "season_phase": "REGULAR_SEASON",
        "season_phase_source": "LEAGUE_OFFICIAL",
        "market_evidence": [_market("book")],
        "research_domain_evidence": {
            "starter": [
                _domain(
                    "LEAGUE_OFFICIAL",
                    "mlb-official-a",
                    evidence_type="STARTER_STATUS",
                    claim_key="HOME_STARTER_STATUS",
                    value="CONFIRMED_A",
                ),
                _domain(
                    "TEAM_OFFICIAL",
                    "team-official",
                    evidence_type="STARTER_STATUS",
                    claim_key="HOME_STARTER_STATUS",
                    value="CONFIRMED_B",
                ),
            ],
            "lineup": [_domain("TEAM_OFFICIAL", "team-official", value="PROJECTED")],
            "bullpen": [_domain("ESTABLISHED_STATS_PROVIDER", "stats", value="READY")],
            "weather": [_domain("WEATHER_PROVIDER", "weather", value="CLEAR")],
        },
    }

    dossier = build_scout_dossier(candidate, now=NOW)

    assert dossier["contradictions"]
    assert dossier["contradictions"][0]["code"] == "MATERIAL_DOMAIN_CONFLICT"
    assert dossier["contradictions"][0]["domain"] == "starter"
    assert dossier["red_team_results"]["status"] == "QUARANTINED"
    assert dossier["can_execute"] is False


def test_older_status_update_is_not_misclassified_as_contemporaneous_conflict():
    candidate = {
        "official_event_id": "mlb-2",
        "sport_key": "baseball_mlb",
        "commence_time": (NOW + timedelta(hours=6)).isoformat(),
        "home_team": "Home",
        "away_team": "Away",
        "season_phase": "REGULAR_SEASON",
        "season_phase_source": "LEAGUE_OFFICIAL",
        "market_evidence": [_market("book")],
        "research_domain_evidence": {
            "starter": [
                _domain(
                    "LEAGUE_OFFICIAL",
                    "mlb-official",
                    captured=(NOW - timedelta(minutes=50)).isoformat(),
                    claim_key="HOME_STARTER_STATUS",
                    value="PROJECTED",
                ),
                _domain(
                    "TEAM_OFFICIAL",
                    "team-official",
                    captured=(NOW - timedelta(minutes=5)).isoformat(),
                    claim_key="HOME_STARTER_STATUS",
                    value="CONFIRMED",
                ),
            ],
            "lineup": [_domain("TEAM_OFFICIAL", "team-official", value="PROJECTED")],
            "bullpen": [_domain("ESTABLISHED_STATS_PROVIDER", "stats", value="READY")],
            "weather": [_domain("WEATHER_PROVIDER", "weather", value="CLEAR")],
        },
    }

    dossier = build_scout_dossier(candidate, now=NOW)

    assert dossier["contradictions"] == []
    assert dossier["red_team_results"]["status"] == "PASSED"


def test_observed_values_are_scrubbed_of_predictive_authority():
    candidate = _nba_candidate()
    candidate["research_domain_evidence"]["availability"] = [
        _domain(
            "LEAGUE_OFFICIAL",
            "nba-official",
            claim_key="PLAYER_STATUS",
            value={
                "status": "OUT",
                "model_probability": 0.91,
                "edge": 0.12,
                "stake": 100,
            },
        )
    ]

    dossier = build_scout_dossier(candidate, now=NOW)
    entry = next(
        row for row in dossier["evidence_ledger"]
        if row["domain"] == "availability"
    )

    assert entry["observed_value"] == {"status": "OUT"}
    assert "model_probability" not in entry["observed_value"]
    assert "edge" not in entry["observed_value"]
    assert "stake" not in entry["observed_value"]
    assert dossier["prediction_authority"] is False
    assert dossier["can_execute"] is False


def test_missing_data_is_explicit_and_never_inferred():
    candidate = _nba_candidate()
    candidate["research_domain_evidence"] = {}
    candidate["market_evidence"] = [_market("a")]

    dossier = build_scout_dossier(candidate, now=NOW)

    assert dossier["required_domain_coverage"]["market"] is True
    assert set(dossier["missing_required_domains"]) == {
        "availability", "rotation", "matchup"
    }
    codes = {(row["code"], row.get("domain")) for row in dossier["uncertainty_ledger"]}
    assert ("MISSING_REQUIRED_DOMAIN", "availability") in codes
    assert ("MISSING_REQUIRED_DOMAIN", "rotation") in codes
    assert ("MISSING_REQUIRED_DOMAIN", "matchup") in codes
    assert dossier["red_team_results"]["status"] == "WATCH"
    assert dossier["research_worker_barrier_status"] == "PARTIAL"


def test_change_detection_records_material_dossier_section_changes():
    first = build_scout_dossier(_nba_candidate(), now=NOW)
    changed = _nba_candidate()
    changed["market_evidence"].append(_market("d", price=-170))

    second = build_scout_dossier(changed, now=NOW, previous=first)

    assert second["change_detection"]["status"] == "CHANGED"
    assert "market_state" in second["change_detection"]["changed_sections"]


def test_prop_request_schema_accepts_scout_context_as_metadata_only():
    context = {
        "schema_version": "wow.v17.scout-intelligence-dossier.v1",
        "prediction_authority": False,
        "can_execute": False,
    }
    row = PickRequestRow(
        row_key="scout-prop-1",
        event_id="evt-1",
        event_start_time=(NOW + timedelta(hours=2)).isoformat(),
        sport="NBA",
        player="Example Player",
        stat_type="PLAYER_POINTS",
        line=20.5,
        direction="MORE",
        source_type="AUTONOMOUS_DISCOVERY",
        scout_context=context,
    )

    assert row.scout_context == context
    assert row.scout_context["prediction_authority"] is False
    assert row.scout_context["can_execute"] is False
