from __future__ import annotations

from datetime import datetime, timedelta, timezone

from v17.scout_intelligence_dossier import build_scout_dossier
from v17.scout_prior_dossier import compact_previous_candidates, hydrate_prior_dossiers
from v17.sport_scout_enrichment import enrich_handoff

NOW = datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc)


def _handoff():
    return {
        "run_id": "wow-scout-current",
        "research_run_id": "wow-scout-current",
        "can_execute": False,
        "governance": {"can_execute": False},
        "model_handoff": {
            "team_event_candidates": [{
                "official_event_id": "nba-1",
                "sport_key": "basketball_nba",
                "commence_time": (NOW + timedelta(hours=4)).isoformat(),
                "home_team": "Home",
                "away_team": "Away",
                "route": "LLP_TEAM_BETTING_ENGINE",
                "market_evidence": [],
            }],
            "prop_candidates": [{
                "official_event_id": "nba-1",
                "sport_key": "basketball_nba",
                "commence_time": (NOW + timedelta(hours=4)).isoformat(),
                "home_team": "Home",
                "away_team": "Away",
                "route": "WOW_PROP_LANE",
                "market_evidence": {
                    "market_key": "player_points",
                    "description": "Player A",
                    "outcome_name": "Over",
                    "point": 20.5,
                    "price": -110,
                    "bookmaker": "book-a",
                },
            }],
        },
    }


def test_compact_prior_lookup_sends_identity_not_full_market_payload():
    rows = compact_previous_candidates(_handoff())

    assert len(rows) == 2
    team, prop = rows
    assert team == {
        "lane": "team_event_candidates",
        "source_index": 1,
        "sport_key": "basketball_nba",
        "official_event_id": "nba-1",
        "route": "LLP_TEAM_BETTING_ENGINE",
    }
    assert prop["market_evidence"] == {
        "market_key": "player_points",
        "description": "Player A",
        "outcome_name": "Over",
        "point": 20.5,
    }
    assert "price" not in prop["market_evidence"]
    assert "bookmaker" not in prop["market_evidence"]


def test_found_prior_dossier_is_attached_only_as_temporary_comparison_state():
    previous = {
        "schema_version": "wow.v17.scout-intelligence-dossier.v1",
        "market_state": {"bookmaker_count": 1},
        "prediction_authority": False,
        "can_execute": False,
    }

    def fake_post(_url, body):
        assert body["persist_phase"] == "READ_PREVIOUS"
        assert body["can_execute"] is False
        return {
            "ok": True,
            "persist_phase": "READ_PREVIOUS",
            "research_run_id": "wow-scout-current",
            "candidates": [
                {
                    "lane": "team_event_candidates",
                    "source_index": 1,
                    "candidate_id": "scout-team",
                    "lookup_status": "FOUND",
                    "previous_research_run_id": "wow-scout-prior",
                    "previous_recorded_at": "2026-10-07T12:00:00Z",
                    "prior_dossier": previous,
                    "prediction_authority": False,
                    "can_execute": False,
                },
                {
                    "lane": "prop_candidates",
                    "source_index": 1,
                    "candidate_id": "scout-prop",
                    "lookup_status": "NOT_FOUND",
                    "previous_research_run_id": None,
                    "previous_recorded_at": None,
                    "prior_dossier": None,
                    "prediction_authority": False,
                    "can_execute": False,
                },
            ],
            "prediction_authority": False,
            "can_execute": False,
        }

    hydrated = hydrate_prior_dossiers(_handoff(), post_fn=fake_post, url="https://example.invalid")
    team = hydrated["model_handoff"]["team_event_candidates"][0]
    prop = hydrated["model_handoff"]["prop_candidates"][0]

    assert team["prior_dossier_lookup_status"] == "FOUND"
    assert team["_prior_scout_dossier"] == previous
    assert team["previous_research_run_id"] == "wow-scout-prior"
    assert prop["prior_dossier_lookup_status"] == "NOT_FOUND"
    assert "_prior_scout_dossier" not in prop
    assert hydrated["prior_dossier_hydration"]["status_counts"] == {
        "FOUND": 1,
        "NOT_FOUND": 1,
    }


def test_lookup_failure_is_explicit_and_does_not_drop_current_candidates():
    def broken(_url, _body):
        raise RuntimeError("boom")

    hydrated = hydrate_prior_dossiers(_handoff(), post_fn=broken, url="https://example.invalid")

    assert hydrated["prior_dossier_hydration"]["status"] == "UNAVAILABLE"
    assert hydrated["prior_dossier_hydration"]["code"] == "PRIOR_DOSSIER_READ_EXCEPTION:RuntimeError"
    for lane in ("team_event_candidates", "prop_candidates"):
        assert len(hydrated["model_handoff"][lane]) == 1
        assert hydrated["model_handoff"][lane][0]["prior_dossier_lookup_status"] == "UNAVAILABLE"


def test_enrichment_consumes_prior_dossier_and_persists_only_current_dossier():
    row = _handoff()["model_handoff"]["team_event_candidates"][0]
    current_seed = {
        **row,
        "market_evidence": [{
            "source_class": "SPORTSBOOK_FEED",
            "bookmaker": "book-a",
            "market_key": "h2h",
            "outcome_name": "Home",
            "price": -110,
            "market_last_update": NOW.isoformat(),
        }],
        "research_domain_evidence": {
            "availability": [{
                "source_class": "LEAGUE_OFFICIAL",
                "source_provider": "nba-official",
                "captured_at": NOW.isoformat(),
                "confirmed": True,
            }],
            "rotation": [{
                "source_class": "ESTABLISHED_STATS_PROVIDER",
                "source_provider": "stats",
                "captured_at": NOW.isoformat(),
                "confirmed": True,
            }],
            "matchup": [{
                "source_class": "ESTABLISHED_STATS_PROVIDER",
                "source_provider": "stats",
                "captured_at": NOW.isoformat(),
                "confirmed": True,
            }],
        },
    }
    previous = build_scout_dossier(current_seed, now=NOW - timedelta(minutes=10))
    current_seed["_prior_scout_dossier"] = previous
    current_seed["prior_dossier_lookup_status"] = "FOUND"

    payload = {
        "can_execute": False,
        "governance": {"can_execute": False},
        "model_handoff": {
            "team_event_candidates": [current_seed],
            "prop_candidates": [],
        },
    }
    enriched = enrich_handoff(payload)
    current = enriched["model_handoff"]["team_event_candidates"][0]

    assert "_prior_scout_dossier" not in current
    assert current["scout_dossier"]["prior_dossier_lookup_status"] == "FOUND"
    assert current["scout_dossier"]["change_detection"]["status"] in {"UNCHANGED", "CHANGED"}
    assert current["scout_dossier"]["prediction_authority"] is False
    assert current["scout_dossier"]["can_execute"] is False


def test_unavailable_prior_state_is_not_mislabeled_initial_snapshot():
    candidate = _handoff()["model_handoff"]["team_event_candidates"][0]
    candidate["prior_dossier_lookup_status"] = "UNAVAILABLE"

    dossier = build_scout_dossier(candidate, now=NOW)

    assert dossier["change_detection"]["status"] == "PRIOR_STATE_UNAVAILABLE"
    assert dossier["prior_dossier_lookup_status"] == "UNAVAILABLE"
