"""Regression coverage for the NBA/WNBA fitted-candidate graduation plumbing."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import v17.prop_exact_route_settlement as settlement
import v17.prop_universal_forward_evidence as universal
from v17.basketball_candidate_forward_overlay import RESEARCH_GENERIC_ROUTES
from v17.nba_scalar_candidate_registry import build_candidate_rows as build_nba_rows
from v17.nba_scalar_fitted_candidate import derive_candidate_payload as derive_nba, simulate_exact_line as simulate_nba
from v17.wnba_composite_candidate_registry import build_candidate_rows as build_wnba_rows
from v17.wnba_composite_auto_hydration import _composite_game_log
from v17.wnba_composite_settlement_overlay import COMPOSITE_ROUTES


def _source(lane: str):
    cohort = lane
    return {
        "artifact_id": f"{lane.lower()}-source",
        "provider_identity": "WOW_PROP_FITTED_MODEL_V1",
        "model_family": f"{lane}_FANTASY_SCORE_EMPIRICAL_RESIDUAL_V1",
        "model_artifact_version": f"{lane}_FANTASY_SOURCE_V1",
        "artifact_checksum": "a" * 64,
        "training_dataset_hash": "b" * 64,
        "training_code_sha": "c" * 40,
        "feature_schema_version": "PROP_FEATURES_V1",
        "feature_transform_version": f"{lane}_SOURCE_V1",
        "training_rows": 100,
        "validation_metrics": {"train_rows": 70, "calibration_rows": 15, "untouched_test_rows": 15},
        "artifact_payload": {
            "lane": lane,
            "fit_contract": {
                "whole_event_chronological_split": True,
                "strictly_prior_residuals": True,
                "untouched_test_required": True,
            },
            "player_means": {
                "Test Player": {
                    "points": 20.0, "rebounds": 8.0, "assists": 6.0,
                    "steals": 1.0, "blocks": 1.0, "turnovers": 2.0,
                }
            },
            "cohort_means": {
                cohort: {
                    "points": 18.0, "rebounds": 7.0, "assists": 5.0,
                    "steals": 1.0, "blocks": 1.0, "turnovers": 2.0,
                }
            },
            "residual_vectors": {
                cohort: [
                    {"points": 3.0, "rebounds": 1.0, "assists": 1.0},
                    {"points": -3.0, "rebounds": -1.0, "assists": -1.0},
                    {"points": 0.0, "rebounds": 0.0, "assists": 0.0},
                ]
            },
        },
    }


def test_nba_registry_derives_seven_exact_candidate_routes_without_authority():
    rows = build_nba_rows(_source("NBA"))
    assert {row["stat_type"] for row in rows} == {
        "POINTS", "REBOUNDS", "ASSISTS", "PRA",
        "POINTS_REBOUNDS", "POINTS_ASSISTS", "REBOUNDS_ASSISTS",
    }
    assert all(row["model_family"] == "NBA_PLAYER_PROP_EMPIRICAL_RESIDUAL_JOINT_V1" for row in rows)
    assert all(row["specialist_version"] == "wow.nba-player-prop-probability-expert@1" for row in rows)
    assert all(row["lifecycle_state"] == "CANDIDATE" for row in rows)
    assert all(row["candidate_research_active"] is True for row in rows)
    assert all(row["promoted"] is False and row["active"] is False for row in rows)
    assert all(row["probability_publishable"] is False and row["can_execute"] is False for row in rows)


def test_nba_candidate_produces_raw_probability_but_never_calibrated_authority():
    source = _source("NBA")
    payload = derive_nba(
        source["artifact_payload"],
        source_model_artifact_version=source["model_artifact_version"],
        source_artifact_checksum=source["artifact_checksum"],
        source_training_dataset_hash=source["training_dataset_hash"],
        source_training_code_sha=source["training_code_sha"],
    )
    scored = simulate_nba(
        payload,
        player="Test Player",
        stat_type="PRA",
        exact_line=33.5,
        side="MORE",
        simulation_count=50_000,
        seed=7,
    )
    assert 0.0 < scored["raw_candidate_probability"] < 1.0
    assert scored["calibrated_probability"] is None
    assert scored["calibrated_lower_bound"] is None
    assert scored["probability_publishable"] is False
    assert scored["rank_eligible"] is False
    assert scored["can_execute"] is False


def test_wnba_registry_derives_all_four_exact_composites_without_authority():
    rows = build_wnba_rows(_source("WNBA"))
    assert {row["stat_type"] for row in rows} == {
        "PRA", "POINTS_REBOUNDS", "POINTS_ASSISTS", "REBOUNDS_ASSISTS",
    }
    assert all(row["lifecycle_state"] == "CANDIDATE" for row in rows)
    assert all(row["candidate_research_active"] is True for row in rows)
    assert all(row["probability_publishable"] is False and row["can_execute"] is False for row in rows)


def test_candidate_forward_overlay_collects_only_explicit_exact_research_routes():
    for route in RESEARCH_GENERIC_ROUTES:
        collector, status, blocker = universal._collector_for(route)
        assert collector == universal.COLLECTOR_GENERIC
        assert status == universal.COLLECTION_AVAILABLE
        assert blocker is None
    collector, status, blocker = universal._collector_for(("NBA", "THREE_POINTERS_MADE"))
    assert collector == universal.COLLECTOR_NONE
    assert status == universal.MODEL_BUILD_REQUIRED
    assert blocker == "PROP_FITTED_SPECIALIST_BUILD_REQUIRED"


def test_wnba_composite_official_game_log_sums_components_on_same_rows(monkeypatch):
    rows = []
    start = datetime(2026, 9, 20, tzinfo=timezone.utc)
    for index in range(10):
        date = (start - timedelta(days=index + 1)).date().isoformat()
        rows.append({
            "PLAYER_ID": "42", "PLAYER_NAME": "Test Player", "GAME_ID": f"g{index}",
            "GAME_DATE": date, "MIN": 30, "PTS": 10 + index, "REB": 5, "AST": 3,
            "TEAM_ABBREVIATION": "TST", "MATCHUP": "TST vs OPP",
        })
    import wnba_prop_auto_hydration as base
    monkeypatch.setattr(base, "_request", lambda *args, **kwargs: {"fake": True})
    monkeypatch.setattr(base, "_result_rows", lambda payload, name: rows)
    game_log, box = _composite_game_log(
        "42", "Test Player", ("PTS", "REB", "AST"), 2026, start, http_get=lambda *a, **k: None
    )
    assert game_log[0] == 18.0
    assert game_log[-1] == 27.0
    assert len(box) == 10
    assert box[0]["components"] == {"PTS": 10, "REB": 5, "AST": 3}


def test_wnba_composite_settlement_routes_are_explicitly_supported():
    assert COMPOSITE_ROUTES.issubset(settlement.SUPPORTED_SETTLEMENT_ROUTES)
    assert settlement.settle_wnba_scalar.__module__.endswith("wnba_composite_settlement_overlay")
