"""Regression contract for the restored V17 cross-sport prop accuracy inventory."""
from __future__ import annotations

import pick_request_runtime_core as core

from v17.fantasy_score_pick_request_bridge import research_candidate_preflight
from v17.prop_capability_manifest import (
    BUILD_REQUIRED,
    CANDIDATE_ONLY,
    SUPPORTED_HOLD_ONLY,
    declared_prop_lane_manifest,
    prop_capability,
)


class _NoDbMarket:
    class _Prod:
        @staticmethod
        def get_client():
            raise RuntimeError("no db should be required for non-research route typing")

    prod = _Prod()


def _artifact_absence():
    return {
        "ok": False,
        "code": "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
        "probability_publishable": False,
        "can_execute": False,
    }


def test_known_cross_sport_routes_are_explicit_build_gaps_not_undeclared():
    nba = prop_capability("NBA", "POINTS")
    nfl = prop_capability("NFL", "PASS_YDS")
    nhl = prop_capability("NHL", "SHOTS_ON_GOAL")
    soccer = prop_capability("SOCCER", "SHOTS_ON_TARGET")
    tennis = prop_capability("TENNIS", "ACES")
    golf = prop_capability("GOLF", "BIRDIES")
    mma = prop_capability("UFC", "SIGNIFICANT_STRIKES")
    boxing = prop_capability("BOXING", "PUNCHES_LANDED")

    for row in (nba, nfl, nhl, soccer, tennis, golf, mma, boxing):
        assert row.lane_status == BUILD_REQUIRED
        assert row.blocker == "PROP_FITTED_SPECIALIST_BUILD_REQUIRED"
        assert row.publication_allowed is False
        assert row.can_execute is False

    assert nfl.stat_type == "PASSING_YARDS"
    assert mma.sport == "MMA"


def test_wnba_composite_and_component_lifecycle_states_remain_distinct():
    component = prop_capability("WNBA", "PTS")
    composite = prop_capability("WNBA", "PTS+REB+AST")

    assert component.lane_status == SUPPORTED_HOLD_ONLY
    assert component.blocker == "WNBA_PROP_PROSPECTIVE_NOT_PUBLISHABLE"
    assert component.publication_allowed is False

    assert composite.stat_type == "PRA"
    assert composite.lane_status == CANDIDATE_ONLY
    assert composite.controlling_specialist == "wow.wnba-composite-prop-expert"
    assert composite.blocker == "WNBA_COMPOSITE_FITTED_MODEL_ARTIFACT_MISSING"
    assert composite.publication_allowed is False


def test_runtime_aliases_reach_exact_manifest_keys_without_granting_authority():
    assert core._canonical_stat("WNBA", "PTS+REB+AST") == "PRA"
    assert core._canonical_stat("NFL", "PASS_YDS") == "PASSING_YARDS"
    assert core._canonical_stat("NCAAF", "RUSH_YDS") == "RUSHING_YARDS"
    assert prop_capability("NFL", core._canonical_stat("NFL", "PASS_YDS")).publication_allowed is False


def test_genuine_artifact_absence_is_refined_to_exact_lifecycle_blocker():
    nba = research_candidate_preflight(
        _NoDbMarket(), "NBA", "POINTS", _artifact_absence()
    )
    assert nba is not None
    assert nba["ok"] is False
    assert nba["code"] == "PROP_FITTED_SPECIALIST_BUILD_REQUIRED"
    assert nba["capability_lane_status"] == BUILD_REQUIRED
    assert nba["probability_publishable"] is False
    assert nba["rank_eligible"] is False
    assert nba["can_execute"] is False

    wnba_pra = research_candidate_preflight(
        _NoDbMarket(), "WNBA", "PRA", _artifact_absence()
    )
    assert wnba_pra is not None
    assert wnba_pra["ok"] is False
    assert wnba_pra["code"] == "WNBA_COMPOSITE_FITTED_MODEL_ARTIFACT_MISSING"
    assert wnba_pra["capability_lane_status"] == CANDIDATE_ONLY
    assert wnba_pra["controlling_specialist"] == "wow.wnba-composite-prop-expert"


def test_non_absence_registry_failures_are_never_rewritten_as_model_gaps():
    failed = {
        "ok": False,
        "code": "PROP_CERTIFIED_MODEL_ARTIFACT_LOOKUP_FAILED",
        "probability_publishable": False,
        "can_execute": False,
    }
    assert research_candidate_preflight(_NoDbMarket(), "NBA", "POINTS", failed) is None


def test_manifest_reports_all_major_sports_without_fabricating_capability():
    manifest = declared_prop_lane_manifest()
    required = {
        "MLB", "NFL", "NCAAF", "NBA", "WNBA", "NCAAB",
        "NHL", "SOCCER", "TENNIS", "GOLF", "MMA", "BOXING",
    }
    assert required.issubset(set(manifest["sports_declared"]))
    assert manifest["manifest_version"] == "WOW_V17_PROP_LANE_MANIFEST_V4"
    assert manifest["build_required_lane_count"] > 0
    assert manifest["build_target_presence_does_not_grant_probability_authority"] is True
    assert manifest["legacy_provisional_formulas_grant_v17_authority"] is False
    assert manifest["can_execute"] is False
