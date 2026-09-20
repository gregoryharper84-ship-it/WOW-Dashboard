from prop_live_model_acceptance import _expected_bootstrap_model_family, _is_governed_model_path_pass


def test_nfl_bootstrap_selects_exact_fitted_family():
    payload={"rows":[{"sport":"NFL","stat_type":"Rush Yards"}]}
    assert _expected_bootstrap_model_family(payload)=="NFL_PROP_ROLLING_FITTED_V1"


def test_governed_checker_accepts_nfl_family_only_when_explicit():
    body={
        "ok":True,
        "prediction":{"prediction_id":"nfl-test"},
        "model_evidence":{
            "provider_identity":"WOW_PROP_FITTED_MODEL_V1",
            "model_family":"NFL_PROP_ROLLING_FITTED_V1",
            "calibration_status":"PRECALIBRATION_SHRINKAGE",
            "probability_publishable":True,
            "can_execute":False,
        },
        "objective_lanes":{"MODEL":{"status":"PASS","probability_publishable":True,"can_execute":False}},
        "can_execute":False,
    }
    ok,pid=_is_governed_model_path_pass(body, expected_model_family="NFL_PROP_ROLLING_FITTED_V1")
    assert ok is True and pid=="nfl-test"
    old_ok,_=_is_governed_model_path_pass(body)
    assert old_ok is False
