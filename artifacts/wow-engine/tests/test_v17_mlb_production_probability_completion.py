from pathlib import Path


SQL_PATH = (
    Path(__file__).resolve().parents[1]
    / "v17"
    / "sql"
    / "20260903_v17_mlb_production_probability_completion.sql"
)
SQL = SQL_PATH.read_text()


def test_probability_only_intents_are_explicit_and_market_relative_intents_stay_legacy():
    assert "decision_intent in ('WINNER','BEST_SIDE')" in SQL
    assert "upper(coalesce(p_decision_intent,'')) in ('WINNER','BEST_SIDE')" in SQL
    assert "wow_v17_mlb_team_event_governance_bridge_legacy" in SQL
    assert "FAVORITE','UNDERDOG','UPSET" in SQL


def test_probability_only_path_never_substitutes_model_probability_for_market():
    assert "market_prior_available=false" in SQL
    assert "market_prior_home_probability=null" in SQL
    assert "market_prior_away_probability=null" in SQL
    assert "market_prior_weight=0.0" in SQL
    assert "MARKET_WEIGHT_NONZERO_WITHOUT_MARKET" in SQL
    assert "market_required',false" in SQL


def test_failure_paths_are_derived_from_fitted_score_distribution_not_narrative():
    assert "wow_mlb_v17_failure_path_package" in SQL
    assert "FITTED_FULL_GAME_SCORE_DISTRIBUTION" in SQL
    assert "REGULATION_ONE_RUN_LOSS" in SQL
    assert "REGULATION_MULTI_RUN_LOSS" in SQL
    assert "EXTRA_INNING_LOSS" in SQL
    assert "MLB_FAILURE_REGIME_MIXTURE_V1" in SQL
    assert "wow_nb_pmf_array" in SQL


def test_event_handoff_requires_current_official_identity_and_post_lineup_score():
    assert "https://statsapi.mlb.com/api/v1.1/game/%s/feed/live" in SQL
    assert "STARTER_IDENTITY_CHANGED_REQUIRES_MODEL_RESCORE" in SQL
    assert "OFFICIAL_LINEUP_NOT_CONFIRMED" in SQL
    assert "POST_LINEUP_SCORE_SNAPSHOT_REQUIRED" in SQL
    assert "scoring_n < 10" in SQL


def test_weather_and_injury_roles_are_typed_without_fake_numeric_inputs():
    assert "CONTEXT_ONLY_NOT_NUMERIC_IN_CERTIFIED_V2D_BASELINE" in SQL
    assert "SEPARATE_INJURY_FEATURE_NOT_IN_CERTIFIED_V2D_SCHEMA" in SQL
    assert "NOT_APPLICABLE_AS_SEPARATE_NUMERIC_INPUT" in SQL


def test_mlb_calibration_adapter_uses_certified_v2d_artifacts_and_health():
    assert "wow_mlb_v2d_intercept_calibration" in SQL
    assert "wow_mlb_v2d_calibration_health" in SQL
    assert "wow_mlb_event_fitted_model_artifacts" in SQL
    assert "CERTIFIED_MLB_EVENT_ARTIFACT_UNAVAILABLE" in SQL
    assert "MLB_CALIBRATION_HEALTH_NOT_PASS" in SQL


def test_execution_remains_disabled_everywhere_in_new_contract():
    assert "can_execute',false" in SQL
    assert "can_execute',true" not in SQL
    assert "can_execute=true" not in SQL
