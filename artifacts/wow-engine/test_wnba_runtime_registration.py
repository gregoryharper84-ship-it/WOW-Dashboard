import prop_calibration_adapters
import prop_discrete_engine
import prop_fitted_provider
import prop_model_adapters


def test_production_registration_includes_wnba_model_and_calibrator():
    prop_fitted_provider.clear_model_family_adapters()
    prop_discrete_engine.clear_prop_calibration_adapters()

    prop_model_adapters.register()
    prop_calibration_adapters.register()

    assert "MLB_PITCHER_SO_FAILURE_PATH_NB_V1" in prop_fitted_provider._ADAPTERS
    assert "WNBA_PROP_POISSON_LOGGLM_V1" in prop_fitted_provider._ADAPTERS
    assert "MLB_PITCHER_SO_CAL_V1" in prop_discrete_engine._CALIBRATION_ADAPTERS
    assert "WNBA_PROP_PRECALIBRATION_BOOTSTRAP_V1" in prop_discrete_engine._CALIBRATION_ADAPTERS
