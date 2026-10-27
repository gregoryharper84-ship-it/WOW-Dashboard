"""Contract tests for the V17 advisory continuous-intelligence cycle."""
from unittest.mock import patch

import pytest

from v17.continuous_intelligence_cycle import run_continuous_intelligence_cycle

BASE = "v17.continuous_intelligence_cycle."
AUTHORITY = "ADVISORY_ONLY"


def payload(**fields):
    return {"status": "PASS", "authority": AUTHORITY, "can_execute": False, **fields}


def test_complete_cycle_captures_both_lanes_before_summary_and_compounding():
    order = []
    prop = lambda *a, **k: (order.append("prop") or payload(candidate_n=2, observation_n=2, skipped_n=0))
    event = lambda *a, **k: (order.append("event") or payload(candidate_n=3, observation_n=3, skipped_n=0))
    summary = lambda *a, **k: (order.append("summary") or payload(observation_n=5, cohort_n=2, hypothesis_n=1, hypotheses=[{"hypothesis_id": "research-only"}]))
    memory = lambda *a, **k: (order.append("memory") or payload(observation_n=5, challenger_proposal_n=1))

    with patch(BASE + "capture_settled_prop_observations", side_effect=prop), \
         patch(BASE + "capture_settled_event_observations", side_effect=event), \
         patch(BASE + "intelligence_summary", side_effect=summary) as summarizer, \
         patch(BASE + "run_compounding_memory_cycle", side_effect=memory) as compound:
        result = run_continuous_intelligence_cycle(object(), max_outcomes=100, min_samples=30)

    assert order == ["prop", "event", "summary", "memory"]
    assert result["status"] == "PASS"
    assert result["completed_stages"] == 4
    assert result["stages"][0]["candidate_n"] == 2
    assert result["stages"][1]["candidate_n"] == 3
    assert result["stages"][2]["hypothesis_n"] == 1
    assert result["can_execute"] is False
    assert result["automatic_promotion_allowed"] is False
    assert result["model_parameters_changed"] is False
    assert summarizer.call_args.kwargs["persist_hypotheses"] is True
    assert compound.call_args.kwargs["base_summary"]["hypothesis_n"] == 1


def test_independent_capture_failure_is_typed_and_downstream_stays_skipped():
    with patch(BASE + "capture_settled_prop_observations", side_effect=RuntimeError("hidden credential")), \
         patch(BASE + "capture_settled_event_observations", return_value=payload(candidate_n=1, observation_n=1)), \
         patch(BASE + "intelligence_summary") as summarizer, \
         patch(BASE + "run_compounding_memory_cycle") as compound:
        result = run_continuous_intelligence_cycle(object())

    assert result["status"] == "PARTIAL_FAILURE"
    assert result["stages"][0]["status"] == "FAILED"
    assert result["stages"][0]["error_type"] == "RuntimeError"
    assert result["stages"][1]["status"] == "PASS"
    assert result["stages"][2]["status"] == "SKIPPED_UPSTREAM_FAILURE"
    assert "hidden credential" not in str(result)
    summarizer.assert_not_called()
    compound.assert_not_called()


def test_unsafe_stage_fails_closed_without_claiming_learning():
    with patch(BASE + "capture_settled_prop_observations", return_value={**payload(), "can_execute": True}), \
         patch(BASE + "capture_settled_event_observations", return_value=payload()), \
         patch(BASE + "intelligence_summary") as summarizer:
        result = run_continuous_intelligence_cycle(object())
    assert result["status"] == "PARTIAL_FAILURE"
    assert result["stages"][0]["error_type"] == "ValueError"
    assert result["can_execute"] is False
    summarizer.assert_not_called()


def test_summary_failure_preserves_capture_evidence_without_compounding():
    with patch(BASE + "capture_settled_prop_observations", return_value=payload(candidate_n=1, observation_n=1)), \
         patch(BASE + "capture_settled_event_observations", return_value=payload(candidate_n=0, observation_n=0)), \
         patch(BASE + "intelligence_summary", side_effect=ValueError("bad data")), \
         patch(BASE + "run_compounding_memory_cycle") as compound:
        result = run_continuous_intelligence_cycle(object())
    assert result["stages"][0]["status"] == "PASS"
    assert result["stages"][1]["status"] == "PASS"
    assert result["stages"][2]["status"] == "FAILED"
    assert result["stages"][3]["status"] == "SKIPPED_UPSTREAM_FAILURE"
    compound.assert_not_called()


@pytest.mark.parametrize("kwargs", [
    {"max_outcomes": 0},
    {"max_outcomes": 5001},
    {"min_samples": 0},
    {"min_samples": True},
    {"max_observations": 20001},
    {"max_observations": "5000"},
])
def test_invalid_bounds_rejected_before_data_access(kwargs):
    with patch(BASE + "capture_settled_prop_observations") as source:
        with pytest.raises(ValueError, match="INVALID_"):
            run_continuous_intelligence_cycle(object(), **kwargs)
    source.assert_not_called()
