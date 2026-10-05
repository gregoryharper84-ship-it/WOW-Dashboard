from __future__ import annotations

import pytest

from v17.js_style_intelligence import (
    ARCH_COMPOSITE_LESS,
    ARCH_DEFENSIVE_VOLUME_LESS,
    ARCH_FLOOR_MORE,
    ARCH_OPPORTUNITY_LESS,
    ARCH_PROMO_ANCHOR,
    JSStyleIntegrityError,
    build_historical_observation,
    classify_candidate,
    cluster_candidates,
)


def test_opportunity_ceiling_less_is_research_only() -> None:
    row = {
        "event_status": "PREGAME",
        "sport": "NFL",
        "player": "Example QB",
        "stat_type": "Pass Attempts",
        "line": 33.5,
        "direction": "LESS",
        "role_adjusted_median": 28.0,
        "robust_dispersion": 4.0,
        "opportunity_ceiling_score": 0.90,
        "matchup_suppression_score": 0.70,
        "distribution_support_score": 0.85,
        "stat_path_robustness_score": 0.80,
        "game_thesis_coherence_score": 0.75,
        "role_ceiling_score": 0.80,
        "game_script_dependency_score": 0.75,
    }
    result = classify_candidate(row)
    assert result.js_candidate is True
    assert ARCH_OPPORTUNITY_LESS in result.js_archetypes
    assert ARCH_DEFENSIVE_VOLUME_LESS in result.js_archetypes
    assert result.js_research_priority >= 55
    assert result.governed_probability is None
    assert result.calibrated_probability is None
    assert result.calibrated_lower_bound is None
    assert result.js_probability_authority is False
    assert result.can_execute is False


def test_composite_less_archetype_from_threshold_and_role() -> None:
    result = classify_candidate({
        "event_status": "PREGAME",
        "sport": "NBA",
        "stat_type": "Pts+Rebs+Asts",
        "line": 22.5,
        "direction": "LESS",
        "role_adjusted_median": 17.0,
        "robust_dispersion": 4.0,
        "opportunity_ceiling_score": 0.75,
        "distribution_support_score": 0.80,
        "stat_path_robustness_score": 0.70,
        "game_thesis_coherence_score": 0.65,
        "role_ceiling_score": 0.75,
    })
    assert ARCH_COMPOSITE_LESS in result.js_archetypes
    assert result.threshold_burden_z is not None
    assert result.threshold_burden_z > 1.0


def test_floor_more_and_promo_are_tags_not_probability() -> None:
    result = classify_candidate({
        "event_status": "PREGAME",
        "sport": "NFL",
        "stat_type": "Pass Yards",
        "line": 0.5,
        "direction": "MORE",
        "offer_type": "PROMO",
        "threshold_asymmetry_score": 0.99,
        "stat_path_robustness_score": 0.95,
        "distribution_support_score": 0.90,
        "game_thesis_coherence_score": 0.80,
    })
    assert ARCH_FLOOR_MORE in result.js_archetypes
    assert ARCH_PROMO_ANCHOR in result.js_archetypes
    assert result.governed_probability is None
    assert result.js_probability_authority is False


@pytest.mark.parametrize("status", ["LIVE", "STARTED", "FINAL", "SETTLED"])
def test_current_selection_rejects_non_pregame_status(status: str) -> None:
    with pytest.raises(JSStyleIntegrityError):
        classify_candidate({
            "event_status": status,
            "stat_type": "PRA",
            "line": 20.5,
            "direction": "LESS",
            "threshold_asymmetry_score": 0.8,
        })


def test_current_selection_rejects_postgame_feature_leak() -> None:
    with pytest.raises(JSStyleIntegrityError, match="POSTGAME_OR_LIVE_FEATURE_LEAK"):
        classify_candidate({
            "event_status": "PREGAME",
            "stat_type": "PRA",
            "line": 20.5,
            "direction": "LESS",
            "actual_result": "WIN",
        })


def test_same_event_shared_driver_is_typed_without_joint_probability() -> None:
    rows = [
        {
            "event_id": "NBA:OKC@GSW",
            "direction": "LESS",
            "shared_driver": "BLOWOUT_MINUTE_COMPRESSION",
        },
        {
            "event_id": "NBA:OKC@GSW",
            "direction": "LESS",
            "shared_driver": "BLOWOUT_MINUTE_COMPRESSION",
        },
    ]
    clusters = cluster_candidates(rows)
    assert len(clusters) == 1
    assert clusters[0]["dependence_type"] == "THESIS_COHERENT"
    assert clusters[0]["joint_probability"] is None
    assert clusters[0]["independence_product_allowed"] is False
    assert clusters[0]["can_execute"] is False


def test_same_event_neutral_cluster_still_cannot_use_independence_product() -> None:
    rows = [
        {"event_id": "NHL:VGK@VAN", "direction": "LESS"},
        {"event_id": "NHL:VGK@VAN", "direction": "LESS"},
    ]
    clusters = cluster_candidates(rows)
    assert clusters[0]["dependence_type"] == "THESIS_NEUTRAL"
    assert clusters[0]["joint_probability"] is None
    assert clusters[0]["independence_product_allowed"] is False


def test_mixed_same_event_drivers_are_unresolved_dependence() -> None:
    rows = [
        {"event_id": "NFL:DET@CAR", "direction": "LESS", "shared_driver": "LOW_PASS_VOLUME"},
        {"event_id": "NFL:DET@CAR", "direction": "LESS", "shared_driver": "QB_EFFICIENCY_SUPPRESSION"},
    ]
    clusters = cluster_candidates(rows)
    assert clusters[0]["dependence_type"] == "UNRESOLVED_DEPENDENCE"
    assert clusters[0]["joint_probability"] is None


def test_historical_observation_separates_outcome_from_pregame_snapshot() -> None:
    obs = build_historical_observation(
        {
            "sport": "NHL",
            "player": "Example Goalie",
            "stat_type": "Goalie Saves",
            "line": 24.5,
            "direction": "LESS",
            "pregame_feature_snapshot": {
                "opportunity_ceiling_score": 0.85,
                "threshold_asymmetry_score": 0.75,
            },
        },
        outcome={"official_result": "WIN", "settled_value": 18},
        source_ref="user_screenshot",
    )
    assert obs["pregame_snapshot_available"] is True
    assert obs["feature_replay_required"] is False
    assert obs["pregame_feature_snapshot"]["opportunity_ceiling_score"] == 0.85
    assert obs["outcome"]["settled_value"] == 18
    assert obs["hindsight_guard"] == "POSTGAME_FIELDS_NEVER_FEED_SELECTION_SCORE"
    assert obs["can_execute"] is False


def test_historical_observation_without_pregame_snapshot_requires_replay() -> None:
    obs = build_historical_observation(
        {
            "sport": "SOCCER",
            "player": "Example Attacker",
            "stat_type": "Passes Attempted",
            "line": 14.5,
            "direction": "LESS",
        },
        outcome={"settled_value": 5},
    )
    assert obs["pregame_snapshot_available"] is False
    assert obs["feature_replay_required"] is True
