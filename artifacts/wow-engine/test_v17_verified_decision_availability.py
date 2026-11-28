from __future__ import annotations

import pytest

from v17.verified_decision_availability import (
    AVAILABILITY_METRICS,
    evaluate_availability_targets,
    verified_decision_availability,
)


def row(**overrides):
    record = {
        "candidate_id": "cand-1",
        "request_id": "req-1",
        "sport": "NFL",
        "market_family": "MONEYLINE",
        "eligible": True,
        "disposition": "SCORED",
        "event_start_at": "2026-11-28T20:00:00Z",
        "scored_at": "2026-11-28T19:00:00Z",
        "action_invoked": True,
        "model_ready": True,
        "publishable": True,
    }
    record.update(overrides)
    return record


def test_full_delivery_reports_every_metric_without_execution_authority():
    report = verified_decision_availability([row()])
    assert report["audit_complete"] is True
    assert report["can_execute"] is False
    assert report["terminal_authority"] == "V17_TERMINAL_REDUCER"
    assert report["eligible_candidates"] == 1
    metrics = report["metrics"]
    assert metrics["action_completion_rate"] == 1
    assert metrics["model_ready_coverage"] == 1
    assert metrics["publishable_probability_rate"] == 1
    assert metrics["deadline_completion_rate"] == 1
    assert metrics["user_facing_availability"] == 1
    assert metrics["repeated_blocker_rate"] is None


def test_late_scoring_is_published_but_not_available_before_the_event():
    report = verified_decision_availability([row(scored_at="2026-11-28T21:00:00Z")])
    assert report["metrics"]["publishable_probability_rate"] == 1
    assert report["metrics"]["deadline_completion_rate"] == 0
    assert report["metrics"]["user_facing_availability"] == 0


def test_publishable_claim_without_scored_output_is_contested_not_counted():
    report = verified_decision_availability([row(scored_at=None)])
    assert "ROW_0_PUBLISHABLE_WITHOUT_SCORED_OUTPUT" in report["findings"]
    assert "ROW_0_SCORED_WITHOUT_TIMESTAMP" in report["findings"]
    assert report["metrics"]["publishable_probability_rate"] is None
    assert report["metrics"]["deadline_completion_rate"] is None
    assert report["metrics"]["user_facing_availability"] is None
    assert report["audit_complete"] is False


def test_held_candidate_keeps_its_native_failure_class():
    held = row(
        candidate_id="cand-2",
        disposition="HELD",
        publishable=False,
        scored_at=None,
        action_invoked=True,
        failure_class="INPUTS_MISSING",
    )
    report = verified_decision_availability([row(), held])
    assert report["failure_classes"] == {"INPUTS_MISSING": 1}
    assert report["metrics"]["publishable_probability_rate"] == 0.5
    assert report["metrics"]["repeated_blocker_rate"] is None


def test_unclassified_hold_is_a_finding_not_a_capability_absence():
    held = row(candidate_id="cand-2", disposition="HELD", publishable=False,
               scored_at=None, failure_class="MODEL_UNAVAILABLE")
    report = verified_decision_availability([held])
    assert "ROW_0_FAILURE_CLASS_UNATTESTED" in report["findings"]
    assert report["failure_classes"] == {}


def test_unknown_eligibility_makes_every_rate_unmeasurable():
    report = verified_decision_availability([row(eligible=None)])
    assert report["eligible_candidates"] is None
    for name in AVAILABILITY_METRICS:
        assert report["metrics"][name] is None
    assert report["audit_complete"] is False


def test_missing_event_start_blocks_only_deadline_metrics():
    report = verified_decision_availability([row(event_start_at=None)])
    assert report["metrics"]["deadline_completion_rate"] is None
    assert report["metrics"]["user_facing_availability"] is None
    assert report["metrics"]["publishable_probability_rate"] == 1
    assert "ROW_0_EVENT_START_UNATTESTED" in report["findings"]


def test_repeated_blocker_rate_measures_returning_repairs():
    history = [
        {"code": "MODEL_INPUTS_INSUFFICIENT",
         "repaired_at": "2026-11-20T00:00:00Z", "recurred_at": "2026-11-22T00:00:00Z"},
        {"code": "MODEL_SCORER_FAILED", "repaired_at": "2026-11-20T00:00:00Z"},
        {"code": "MODEL_UNAVAILABLE"},
    ]
    report = verified_decision_availability([row()], blocker_history=history)
    assert report["repaired_blockers"] == 2
    assert report["metrics"]["repeated_blocker_rate"] == 0.5


def test_absent_target_is_unknown_and_breach_is_never_a_pass():
    report = verified_decision_availability(
        [row(publishable=False)],
        thresholds={"publishable_probability_rate": 0.9},
    )
    targets = report["targets"]
    assert targets["publishable_probability_rate"]["status"] == "BREACH"
    assert targets["action_completion_rate"]["status"] == "UNKNOWN"
    assert evaluate_availability_targets({"publishable_probability_rate": 1},
                                         {"publishable_probability_rate": True})[
        "publishable_probability_rate"]["status"] == "INVALID_TARGET"


def test_empty_inventory_reports_no_rate_rather_than_zero():
    report = verified_decision_availability([])
    assert report["eligible_candidates"] == 0
    assert all(value is None for value in report["metrics"].values())
    assert all(entry["status"] == "UNKNOWN" for entry in report["targets"].values())


def test_duplicate_candidate_identity_prevents_double_counting():
    report = verified_decision_availability([row(), row()])
    assert report["eligible_candidates"] == 1
    assert "ROW_1_DUPLICATE_CANDIDATE_ID" in report["findings"]


def test_naive_timestamps_are_not_treated_as_attested():
    report = verified_decision_availability(
        [row(event_start_at="2026-11-28T20:00:00", scored_at="2026-11-28T19:00:00")]
    )
    assert report["metrics"]["deadline_completion_rate"] is None
    assert "ROW_0_EVENT_START_UNATTESTED" in report["findings"]


@pytest.mark.parametrize("field", ["action_invoked", "model_ready"])
def test_unattested_step_capability_is_unmeasured_not_passed(field):
    report = verified_decision_availability([row(**{field: None})])
    metric = "action_completion_rate" if field == "action_invoked" else "model_ready_coverage"
    assert report["metrics"][metric] is None
    assert report["audit_complete"] is False
