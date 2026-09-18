"""Regression coverage for fail-closed cross-sport prop activation plumbing."""
from __future__ import annotations

import pick_request_runtime as runtime
import pick_request_runtime_core as core
import prop_auto_hydration_router


class _ArtifactMissingMarket:
    @staticmethod
    def _prop_route_artifact(_sport, _stat):
        return {
            "ok": False,
            "code": "PROP_CERTIFIED_MODEL_ARTIFACT_NOT_FOUND",
            "probability_publishable": False,
            "can_execute": False,
        }


class _ArtifactLookupFailedMarket:
    @staticmethod
    def _prop_route_artifact(_sport, _stat):
        return {
            "ok": False,
            "code": "PROP_CERTIFIED_MODEL_ARTIFACT_LOOKUP_FAILED",
            "probability_publishable": False,
            "can_execute": False,
        }


def test_wnba_board_aliases_normalize_before_specialist_and_artifact_lookup():
    assert core._canonical_stat("WNBA", "PTS") == "POINTS"
    assert core._canonical_stat("WNBA", "reb") == "REBOUNDS"
    assert core._canonical_stat("WNBA", "ast") == "ASSISTS"
    assert core._canonical_stat("WNBA", "3PM") == "THREE_POINTERS_MADE"
    assert core._canonical_stat("WNBA", "3-pt made") == "THREE_POINTERS_MADE"
    assert core._canonical_stat("WNBA", "PRA") == "PRA"
    assert core._canonical_stat("WNBA", "PTS+REB+AST") == "PRA"
    assert core._canonical_stat("WNBA", "points+rebounds+assists") == "PRA"


def test_cross_sport_aliases_normalize_without_granting_probability_authority():
    assert core._canonical_stat("NBA", "PTS+REB+AST") == "PRA"
    assert core._canonical_stat("NFL", "PASS_YDS") == "PASSING_YARDS"
    assert core._canonical_stat("NCAAF", "RUSH_YDS") == "RUSHING_YARDS"


def test_pick_request_facade_defaults_to_reviewed_sport_aware_hydrator():
    assert runtime.auto_hydrate_prop_evidence is prop_auto_hydration_router.auto_hydrate_prop_evidence


def test_core_route_uses_facade_delegate_without_granting_probability_authority():
    assert core.auto_hydrate_prop_evidence is runtime._auto_hydrate_prop_evidence_delegate
    assert core.PROP_STAT_ALIASES[("WNBA", "PTS")] == "POINTS"
    assert core.PROP_STAT_ALIASES[("WNBA", "3PM")] == "THREE_POINTERS_MADE"
    assert core.PROP_STAT_ALIASES[("WNBA", "PTS+REB+AST")] == "PRA"
    assert core.PROP_STAT_ALIASES[("NFL", "PASS_YDS")] == "PASSING_YARDS"
    # This plumbing change never touches execution authority.
    assert prop_auto_hydration_router.WNBA_PROVIDER


def test_wnba_pra_missing_artifact_is_typed_to_composite_model_gap():
    route = runtime._ScoringReceiptMarketApi(_ArtifactMissingMarket())._prop_route_artifact(
        "WNBA", "PRA"
    )
    assert route["ok"] is False
    assert route["code"] == "WNBA_COMPOSITE_FITTED_MODEL_ARTIFACT_MISSING"
    assert route["capability_lane_status"] == "CANDIDATE_ONLY"
    assert route["controlling_specialist"] == "wow.wnba-composite-prop-expert"
    assert route["probability_publishable"] is False
    assert route["can_execute"] is False


def test_known_cross_sport_model_gap_is_typed_as_build_required():
    route = runtime._ScoringReceiptMarketApi(_ArtifactMissingMarket())._prop_route_artifact(
        "NBA", "POINTS"
    )
    assert route["ok"] is False
    assert route["code"] == "PROP_FITTED_SPECIALIST_BUILD_REQUIRED"
    assert route["capability_lane_status"] == "BUILD_REQUIRED"
    assert route["probability_publishable"] is False
    assert route["can_execute"] is False


def test_truly_unknown_prop_lane_is_typed_as_undeclared():
    route = runtime._ScoringReceiptMarketApi(_ArtifactMissingMarket())._prop_route_artifact(
        "LACROSSE", "GROUND_BALLS"
    )
    assert route["ok"] is False
    assert route["code"] == "PROP_LANE_NOT_DECLARED"
    assert route["capability_lane_status"] == "NOT_DECLARED"
    assert route["probability_publishable"] is False
    assert route["can_execute"] is False


def test_registry_transport_or_lookup_failure_is_never_rewritten_as_capability_absence():
    route = runtime._ScoringReceiptMarketApi(_ArtifactLookupFailedMarket())._prop_route_artifact(
        "WNBA", "PRA"
    )
    assert route["ok"] is False
    assert route["code"] == "PROP_CERTIFIED_MODEL_ARTIFACT_LOOKUP_FAILED"
    assert "capability_lane_status" not in route
