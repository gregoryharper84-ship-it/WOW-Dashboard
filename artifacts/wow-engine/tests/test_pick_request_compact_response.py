from pick_request_runtime_core import _compact_pick_outcome


def test_compact_pick_outcome_preserves_governed_probability_and_typed_blocker():
    source = {
        "row_key": "r1", "terminal_status": "HELD", "code": "PROP_MODEL_REGISTRY_UNAVAILABLE",
        "terminal_label": "MODEL_UNAVAILABLE", "probability_publishable": False,
        "detail": {"blocker_code": "PROP_MODEL_REGISTRY_UNAVAILABLE", "sport": "MLB", "stat_type": "PITCHER_STRIKEOUTS"},
        "acquisition": {"very_large": "x" * 10000}, "specialist_utilization_audit": {"trace": ["x"] * 1000},
        "can_execute": False,
    }
    out = _compact_pick_outcome(source)
    assert out["code"] == "PROP_MODEL_REGISTRY_UNAVAILABLE"
    assert out["blocker_code"] == "PROP_MODEL_REGISTRY_UNAVAILABLE"
    assert out["terminal_label"] == "MODEL_UNAVAILABLE"
    assert out["detail_available"] is True
    assert "detail" not in out and "acquisition" not in out and "specialist_utilization_audit" not in out
    assert out["can_execute"] is False


def test_compact_pick_outcome_keeps_probability_package_without_nested_result():
    source = {
        "row_key": "r2", "terminal_status": "COMPLETED", "code": "FINAL_APPROVED",
        "result": {"prediction": {"raw_model_probability": .72, "calibrated_probability": .69,
          "calibrated_probability_lower_bound": .63, "calibration_status": "PASS", "model_version": "v1"},
          "probability_qualification": {"rank_eligible": True, "model_supported": True, "confidence_tier": "HIGH"}},
        "probability_publishable": True, "can_execute": False,
    }
    out = _compact_pick_outcome(source)
    assert out["model_probability"] == .72
    assert out["calibrated_probability"] == .69
    assert out["calibrated_probability_lower_bound"] == .63
    assert out["rank_eligible"] is True
    assert "result" not in out
    assert out["can_execute"] is False
