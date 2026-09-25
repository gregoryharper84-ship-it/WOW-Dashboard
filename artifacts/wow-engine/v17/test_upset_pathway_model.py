from __future__ import annotations

import copy
import pytest

from v17.upset_pathway_model import UpsetPathwayInvalid, validate_and_aggregate_upset_pathways


def package():
    return {
        "schema_version": "WOW_V17_UPSET_PATHWAY_V1", "sport": "MLB",
        "fitted_artifact_id": "mlb-upset-path-v1", "feature_schema_hash": "sha256:test",
        "probability_source_types": ["MODEL_INPUT", "REGIME_INPUT"],
        "regimes": [
            {"regime_id": "starter_platoon", "probability": .30,
             "upset_probability_given_regime": .70, "mechanism_families": ["STARTER", "PLATOON"],
             "favorite_failure": True, "underdog_success": True},
            {"regime_id": "bullpen", "probability": .25,
             "upset_probability_given_regime": .50, "mechanism_families": ["BULLPEN"],
             "favorite_failure": True, "underdog_success": True},
            {"regime_id": "normal", "probability": .45,
             "upset_probability_given_regime": .30, "mechanism_families": ["DEFENSE"],
             "favorite_failure": False, "underdog_success": False},
        ],
    }


def test_aggregates_mutually_exclusive_regimes_and_fragility():
    out = validate_and_aggregate_upset_pathways(package(), governed_raw_underdog_probability=.47)
    assert out["unconditional_raw_upset_probability"] == pytest.approx(.47)
    assert out["favorite_fragility_probability"] == pytest.approx(.55)
    assert out["diagnostics"]["credible_regime_count"] == 3
    assert out["diagnostics"]["miracle_dependency"] == "LOW"
    assert out["market_inputs_used"] is False
    assert out["can_execute"] is False


def test_price_or_narrative_cannot_enter_probability_sources():
    for forbidden in ("MARKET", "SPORTSBOOK", "SHARP_ACTION", "REVENGE", "MOMENTUM", "HAND_WEIGHTED"):
        row = package(); row["probability_source_types"].append(forbidden)
        with pytest.raises(UpsetPathwayInvalid, match="SOURCE_FORBIDDEN"):
            validate_and_aggregate_upset_pathways(row)


def test_sport_mechanisms_are_not_universal():
    row = package(); row["sport"] = "NFL"
    with pytest.raises(UpsetPathwayInvalid, match="SPORT_MECHANISM_INVALID"):
        validate_and_aggregate_upset_pathways(row)


def test_regimes_must_be_complete_and_probability_consistent():
    row = package(); row["regimes"][0]["probability"] = .20
    with pytest.raises(UpsetPathwayInvalid, match="SUM_TO_ONE"):
        validate_and_aggregate_upset_pathways(row)
    with pytest.raises(UpsetPathwayInvalid, match="PROBABILITY_MISMATCH"):
        validate_and_aggregate_upset_pathways(package(), governed_raw_underdog_probability=.40)


def test_breadth_is_diagnostic_only_and_input_is_not_mutated():
    row = package(); before = copy.deepcopy(row)
    out = validate_and_aggregate_upset_pathways(row)
    assert row == before
    assert out["probability_mutated_downstream"] is False
