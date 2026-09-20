from pick_request_runtime_core import _compact_pick_outcome
from v17.top10_model_reconciliation import has_valid_model_package

def test_compact_receipt_is_bounded_and_preserves_probability_package():
    source = {"row_key": "r1", "terminal_status": "COMPLETED", "code": "FINAL_APPROVED", "model_evaluated": True, "detail": {"event_id": "evt-1", "event_start_time": "2099-09-20T20:00:00Z", "sport": "MLB", "league": "MLB", "player": "Pitcher One", "stat_type": "PITCHER_STRIKEOUTS", "line": 6.5, "direction": "MORE", "controlling_specialist": "wow.mlb-pitcher-failure-path-expert", "blockers": []}, "result": {"prediction": {"prediction_id": "pred-1", "raw_model_probability": 0.72, "calibrated_probability": 0.69, "calibrated_probability_lower_bound": 0.63, "calibrated_probability_upper_bound": 0.75, "calibration_status": "PASS", "model_version": "v1"}, "probability_qualification": {"rank_eligible": True, "model_supported": True, "confidence_tier": "HIGH", "terminal_label": "MODEL_QUALIFIED"}}, "detailed_evidence": {"huge": "x" * 10000}, "model_evidence": {"huge": "x" * 10000}, "failure_path_evidence": {"huge": "x" * 10000}, "directional_probability_assessments": [{"x": "y" * 1000}], "v17_numerical_engine": {"huge": "x" * 10000}, "objective_lanes": {"huge": "x" * 10000}, "backend_traversal": {"huge": "x" * 10000}, "specialist_utilization_audit": {"huge": "x" * 10000}, "can_execute": False}
    out = _compact_pick_outcome(source)
    assert out["prediction_id"] == "pred-1"
    assert out["model_probability"] == 0.72
    assert out["calibrated_probability"] == 0.69
    assert out["calibrated_probability_lower_bound"] == 0.63
    assert out["calibrated_probability_upper_bound"] == 0.75
    assert out["rank_eligible"] is True
    assert out["detail_ref"]["prediction_id"] == "pred-1"
    assert has_valid_model_package(out) is True
    for forbidden in ("result", "detail", "detailed_evidence", "model_evidence", "failure_path_evidence", "directional_probability_assessments", "v17_numerical_engine", "objective_lanes", "backend_traversal", "specialist_utilization_audit"):
        assert forbidden not in out
    assert out["can_execute"] is False
