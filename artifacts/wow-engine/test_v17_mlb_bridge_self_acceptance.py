from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import v17_mlb_bridge_self_acceptance as acceptance
from v17_mlb_bridge_self_acceptance import evaluate_bridge_response


def _complete_package() -> dict:
    return {
        "code": "LLP_EVENT_GOVERNANCE_NOT_PROVEN",
        "calibrated_home_probability": 0.61,
        "calibrated_away_probability": 0.39,
        "calibrated_home_lower_bound": 0.56,
        "calibrated_away_lower_bound": 0.34,
        "model_version": "MLB_TEST",
        "model_timestamp": "2026-09-08T23:00:00+00:00",
        "can_execute": False,
    }


def test_rejects_complete_probability_package_when_llp_governance_not_proven():
    result = evaluate_bridge_response(200, _complete_package())
    assert result["accepted"] is False
    assert result["complete_probability_package"] is True
    assert result["governance_reached"] is False
    assert result["can_execute"] is False


def test_accepts_complete_package_with_governed_no_pick():
    payload = {
        **_complete_package(),
        "llp_governance": {
            "status": "HOLD",
            "probability_audit_result": "PROBABILITY_AUDIT_FAILURE",
            "event_decision": "NO_PICK_UNCALIBRATED",
            "event_mutex_status": "PASS",
            "global_terminal_reducer": "V17_TERMINAL_REDUCER",
            "can_execute": False,
        },
    }
    result = evaluate_bridge_response(200, payload)
    assert result["accepted"] is True
    assert result["complete_probability_package"] is True
    assert result["governance_reached"] is True
    assert result["bridge_unavailable"] is False


def test_rejects_canonical_failure_when_governance_bridge_unavailable_is_blocker():
    payload = {
        "detail": {
            "code": "MODEL_SCORER_FAILED",
            "blocker_code": "V17_EVENT_GOVERNANCE_BRIDGE_UNAVAILABLE",
            "can_execute": False,
        }
    }
    result = evaluate_bridge_response(503, payload)
    assert result["accepted"] is False
    assert result["canonical_failure"] is True
    assert result["bridge_unavailable"] is True


def test_accepts_canonical_model_failure_without_bridge_unavailable_diagnosis():
    payload = {
        "detail": {
            "code": "MODEL_SCORER_FAILED",
            "can_execute": False,
        }
    }
    result = evaluate_bridge_response(503, payload)
    assert result["accepted"] is True
    assert result["canonical_failure"] is True
    assert result["bridge_unavailable"] is False


def test_rejects_legacy_bridge_unavailable_as_only_diagnosis():
    payload = {"detail": {"code": "EVENT_MODEL_BRIDGE_UNAVAILABLE", "can_execute": False}}
    result = evaluate_bridge_response(503, payload)
    assert result["accepted"] is False
    assert result["sole_legacy_bridge_diagnosis"] is True
    assert result["bridge_unavailable"] is True


def test_rejects_any_response_that_claims_execution_capability():
    payload = {
        "detail": {
            "code": "MODEL_INPUTS_INSUFFICIENT",
            "can_execute": True,
        }
    }
    result = evaluate_bridge_response(422, payload)
    assert result["accepted"] is False
    assert result["can_execute_false"] is False


def _shadow(event_id, **overrides):
    row = {
        "official_event_id": event_id,
        "official_date": "2026-09-12",
        "event_start_time": "2026-09-12T17:10:00+00:00",
        "event_status": "Scheduled",
        "home_team": "Detroit Tigers",
        "away_team": "Colorado Rockies",
        "snapshot_id": f"snap-{event_id}",
        "snapshot_timestamp": "2026-09-12T06:02:00+00:00",
        "feature_hydration_status": "PASS",
        "lineup_status": "NOT_YET_AVAILABLE",
        "model_score_status": "SHADOW_SCORED_LINEUP_PENDING",
    }
    row.update(overrides)
    return row


def _event_api_with(rows):
    class _Query:
        def select(self, *a, **k):
            return self

        gt = order = limit = select

        def execute(self):
            return SimpleNamespace(data=list(rows))

    return SimpleNamespace(get_client=lambda: SimpleNamespace(table=lambda name: _Query()))


def test_skipped_run_reports_why_no_candidate_was_eligible():
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    candidate, breakdown = acceptance._latest_confirmed_candidate(
        _event_api_with([_shadow("824224"), _shadow("824225")]), now
    )

    # An unposted lineup is the normal pregame state, not a producer gap.
    assert candidate is None
    assert breakdown["future_events"] == 2
    assert breakdown["rejected_lineup_pending"] == 2
    assert breakdown["rejected_hydration_not_pass"] == 0
    assert breakdown["eligible"] == 0


def test_confirmed_pregame_event_is_still_selected():
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    ready = _shadow("824900", lineup_status="CONFIRMED", model_score_status="SHADOW_SCORED_PREGAME")
    candidate, breakdown = acceptance._latest_confirmed_candidate(_event_api_with([ready]), now)

    assert candidate is not None
    assert candidate["official_event_id"] == "824900"
    assert breakdown["eligible"] == 1
