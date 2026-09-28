from __future__ import annotations

import pytest

from v17.js_style.opportunity_chain import (
    JsOpportunityChainError,
    build_opportunity_chain_features,
)


def _base(**extra):
    row = {
        "direction": "LESS",
        "exact_line": 3.0,
        "role_adjusted_median": 2.0,
        "robust_dispersion": 1.0,
        "capture_phase": "PREGAME",
        "upstream_team_dependency": 0.9,
        "player_role_dependency": 0.8,
        "possession_dependency": 0.9,
        "territory_dependency": 0.8,
        "minutes_dependency": 0.7,
        "stat_self_generation_score": 0.2,
    }
    row.update(extra)
    return row


def test_less_opportunity_chain_is_research_only_and_non_authoritative():
    result = build_opportunity_chain_features(_base())
    assert result["threshold_burden_robust"] == pytest.approx(1.0)
    assert result["opportunity_chain_burden"] > 0.0
    assert result["archetype"] == "JS_OPPORTUNITY_CHAIN_LESS"
    assert result["research_only"] is True
    assert result["js_probability_authority"] is False
    assert result["probability_publishable"] is False
    assert result["rank_eligible"] is False
    assert result["can_execute"] is False


def test_more_floor_and_less_ceiling_are_direction_neutral_mirrors():
    less = build_opportunity_chain_features(_base(direction="LESS", exact_line=3.0, role_adjusted_median=2.0))
    more = build_opportunity_chain_features(_base(direction="MORE", exact_line=1.0, role_adjusted_median=2.0))
    assert less["threshold_burden_robust"] == pytest.approx(1.0)
    assert more["threshold_burden_robust"] == pytest.approx(1.0)
    assert less["opportunity_chain_burden"] == pytest.approx(more["opportunity_chain_burden"])
    assert more["archetype"] == "JS_FLOOR_MORE"


def test_direction_alone_does_not_create_positive_burden():
    less = build_opportunity_chain_features(_base(direction="LESS", exact_line=2.0, role_adjusted_median=2.0))
    more = build_opportunity_chain_features(_base(direction="MORE", exact_line=2.0, role_adjusted_median=2.0))
    assert less["threshold_burden_robust"] == pytest.approx(0.0)
    assert more["threshold_burden_robust"] == pytest.approx(0.0)
    assert less["opportunity_chain_burden"] == pytest.approx(0.0)
    assert more["opportunity_chain_burden"] == pytest.approx(0.0)


def test_live_capture_without_known_pregame_selection_is_excluded_from_pregame_training():
    result = build_opportunity_chain_features(
        _base(
            captured_at="2026-09-28T18:00:00Z",
            event_start_time="2026-09-28T17:00:00Z",
            capture_phase="LIVE",
        )
    )
    assert result["capture_phase"] == "LIVE"
    assert result["pregame_training_eligible"] is False
    assert result["selection_style_evidence_eligible"] is True
    assert result["research_state"] == "JS_LIVE_CAPTURE_EXCLUDED_FROM_PREGAME_COHORT"


def test_live_capture_with_known_pregame_pick_but_no_pregame_feature_snapshot_is_selection_only():
    result = build_opportunity_chain_features(
        _base(
            captured_at="2026-09-28T18:00:00Z",
            event_start_time="2026-09-28T17:00:00Z",
            original_selection_time="2026-09-28T16:30:00Z",
            capture_phase="LIVE",
        )
    )
    assert result["selection_proven_pregame"] is True
    assert result["pregame_training_eligible"] is False
    assert result["research_state"] == "JS_LIVE_CAPTURE_SELECTION_ONLY"


def test_live_capture_can_join_pregame_cohort_only_with_proven_pregame_feature_snapshot():
    result = build_opportunity_chain_features(
        _base(
            captured_at="2026-09-28T18:00:00Z",
            event_start_time="2026-09-28T17:00:00Z",
            original_selection_time="2026-09-28T16:30:00Z",
            feature_snapshot_time="2026-09-28T16:25:00Z",
            capture_phase="LIVE",
        )
    )
    assert result["selection_proven_pregame"] is True
    assert result["feature_snapshot_proven_pregame"] is True
    assert result["pregame_training_eligible"] is True
    assert result["live_feature_inputs_allowed"] is False
    assert result["research_state"] == "JS_RESEARCH_READY"


def test_live_box_score_values_cannot_change_research_features():
    base = _base(
        captured_at="2026-09-28T18:00:00Z",
        event_start_time="2026-09-28T17:00:00Z",
        original_selection_time="2026-09-28T16:30:00Z",
        feature_snapshot_time="2026-09-28T16:25:00Z",
        capture_phase="LIVE",
    )
    high_live = build_opportunity_chain_features({**base, "live_stats": {"shots": 5, "passes": 26}})
    low_live = build_opportunity_chain_features({**base, "live_stats": {"shots": 0, "passes": 0}})
    for field in (
        "threshold_burden_robust",
        "opportunity_chain_dependency",
        "opportunity_chain_burden",
        "archetype",
    ):
        assert high_live[field] == low_live[field]


def test_high_self_generation_reduces_chain_dependency():
    low_self_generation = build_opportunity_chain_features(_base(stat_self_generation_score=0.0))
    high_self_generation = build_opportunity_chain_features(_base(stat_self_generation_score=1.0))
    assert low_self_generation["opportunity_chain_dependency"] > high_self_generation["opportunity_chain_dependency"]


def test_invalid_dispersion_fails_closed():
    with pytest.raises(JsOpportunityChainError, match="robust_dispersion"):
        build_opportunity_chain_features(_base(robust_dispersion=0.0))


def test_dependency_inputs_must_be_unit_interval():
    with pytest.raises(JsOpportunityChainError, match="possession_dependency"):
        build_opportunity_chain_features(_base(possession_dependency=1.2))
