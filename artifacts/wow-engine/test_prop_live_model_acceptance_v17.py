import httpx

from prop_live_model_acceptance import _is_model_path_pass


def _response(body):
    return httpx.Response(200, json=body)


def _base_governed_body():
    return {
        "ok": True,
        "prediction": {"prediction_id": "pred-1"},
        "model_evidence": {
            "provider_identity": "WOW_PROP_FITTED_MODEL_V1",
            "model_family": "MLB_PITCHER_SO_FAILURE_PATH_NB_V1",
            "calibration_status": "PRECALIBRATION_SHRINKAGE",
            "calibrated_probability": 0.62,
            "calibrated_probability_lower_bound": 0.56,
            "probability_publishable": True,
            "can_execute": False,
        },
        "objective_lanes": {
            "MODEL": {
                "status": "PASS",
                "probability_publishable": True,
                "can_execute": False,
            }
        },
        "probability_publishable": True,
        "can_execute": False,
    }


def test_accepts_full_sporting_probability_under_official_publication_hold():
    body = _base_governed_body()
    body.update(
        {
            "governed_sporting_probability_completed": True,
            "sporting_probability_publishable": True,
            "governed_publishable": False,
            "official_final_publishable": False,
            "final_approved": False,
            "official_publication_blockers": ["FORWARD_SHADOW_NOT_COMPLETED"],
        }
    )
    passed, _, prediction_id, mode = _is_model_path_pass(_response(body))
    assert passed is True
    assert prediction_id == "pred-1"
    assert mode == "SPORTING_PROBABILITY_COMPLETE_PUBLICATION_HOLD"


def test_hold_contract_fails_closed_without_completed_sporting_probability_marker():
    body = _base_governed_body()
    body.update(
        {
            "sporting_probability_publishable": True,
            "governed_publishable": False,
            "official_final_publishable": False,
            "final_approved": False,
            "official_publication_blockers": ["FORWARD_SHADOW_NOT_COMPLETED"],
        }
    )
    passed, _, _, mode = _is_model_path_pass(_response(body))
    assert passed is False
    assert mode == "UNRECOGNIZED_200"


def test_can_execute_true_never_passes_acceptance():
    body = _base_governed_body()
    body.update(
        {
            "governed_sporting_probability_completed": True,
            "sporting_probability_publishable": True,
            "governed_publishable": False,
            "official_final_publishable": False,
            "final_approved": False,
            "official_publication_blockers": ["FORWARD_SHADOW_NOT_COMPLETED"],
            "can_execute": True,
        }
    )
    passed, _, _, _ = _is_model_path_pass(_response(body))
    assert passed is False
