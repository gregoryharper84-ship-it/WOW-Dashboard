from pathlib import Path

SQL = (Path(__file__).parent / "sql" / "20260903_v17_projected_lineup_score_bridge.sql").read_text()


def test_bridge_preserves_legacy_scorer_and_only_wraps_lineup_holds():
    assert "wow_mlb_score_event_bridge_pre_projected_lineup" in SQL
    assert "REAL_FITTED_MODEL_PATH_PROVEN" in SQL
    assert "LINEUP_NOT_CONFIRMED" in SQL
    assert "POST_LINEUP_SCORE_SNAPSHOT_REQUIRED" in SQL
    assert "OFFICIAL_LINEUP_REFRESH_%" in SQL
    assert "v_non_lineup_blocker_n <> 0" in SQL


def test_projected_probability_requires_current_governed_deployment():
    for token in (
        "deployment_contract_status",
        "runtime_capability_status",
        "governed_probability_capability",
        "ratification_status",
        "calibration_health_status",
        "production_feature_ready",
        "probability_publishable",
    ):
        assert token in SQL


def test_projected_probability_is_numeric_but_never_rank_or_execution_approved():
    assert "'code','LINEUP_PROJECTED_PROBABILITY_AVAILABLE'" in SQL
    assert "'sporting_probability_publishable',true" in SQL
    assert "'probability_publishable',true" in SQL
    assert "'rank_eligible',false" in SQL
    assert "'terminal_label','MODEL_QUALIFIED_HOLD'" in SQL
    assert "'final_refresh_required',true" in SQL
    assert "'can_execute',false" in SQL


def test_scenario_weights_are_not_manufactured():
    assert "'scenario_weights_invented_by_governor',false" in SQL
    assert "CERTIFIED_CONTEXTUAL_PROJECTED_LINEUP_MODEL" in SQL
