"""Deterministic negative-path tests; no live credentials, releases or wagers."""
from copy import deepcopy

from v17.nfl_week_acceptance_reconciliation import reconcile_week


def _case():
    expected = {
        "2026-10-15": [f"game-{i}" for i in range(4)],
        "2026-10-18": [f"game-{i}" for i in range(4, 12)],
        "2026-10-19": ["game-12"],
    }
    receipts = {}
    for date, ids in expected.items():
        receipts[date] = {
            "requested_slate_date": date, "run_status": "COMPLETE",
            "can_execute": False, "global_terminal_authority": "V17_TERMINAL_REDUCER",
            "reconciliation": {"balanced": True},
            "cross_sport_discovery_audit": {
                "reconciliation": {
                    "row_reconciliation": "PASS", "events_discovered": len(ids),
                    "events_accounted": len(ids),
                }
            },
            "rows": [
                {"lane": "MONEYLINE", "identity": {
                    "sport": "NFL", "official_event_id": event_id
                }, "can_execute": False, "result": {}}
                for event_id in ids
            ],
        }
    return expected, receipts


def test_multiday_complete_week_coverage_never_certifies_model_or_action():
    expected, receipts = _case()
    result = reconcile_week(expected, receipts)
    assert result["status"] == "WEEK_COVERAGE_RECONCILED_NOT_ACTION_CERTIFIED"
    assert result["unique_event_count"] == 13
    assert result["can_execute"] is False
    assert result["probability_publishable"] is False


def test_missing_date_is_hold():
    expected, receipts = _case()
    del receipts["2026-10-19"]
    assert reconcile_week(expected, receipts)["blockers"] == [
        "NFL_WEEK_DAILY_RECEIPT_COVERAGE_MISMATCH"
    ]


def test_duplicate_event_between_dates_is_hold():
    expected, receipts = _case()
    expected["2026-10-19"] = ["game-0"]
    assert reconcile_week(expected, receipts)["blockers"] == [
        "NFL_WEEK_INCOMPLETE_OR_DUPLICATE_EXPECTED"
    ]


def test_missing_event_is_hold():
    expected, receipts = _case()
    receipts["2026-10-18"]["rows"].pop()
    assert reconcile_week(expected, receipts)["blockers"] == [
        "NFL_WEEK_DAILY_EXACT_ONCE_FAILED"
    ]


def test_duplicate_row_is_hold():
    expected, receipts = _case()
    receipts["2026-10-18"]["rows"].append(
        deepcopy(receipts["2026-10-18"]["rows"][0])
    )
    assert reconcile_week(expected, receipts)["status"] == "WEEK_ACCEPTANCE_HELD"


def test_model_budget_exhaustion_is_hold():
    expected, receipts = _case()
    receipts["2026-10-18"]["rows"][0]["result"] = {
        "error": "MODEL_INVOCATION_BUDGET_REACHED"
    }
    assert reconcile_week(expected, receipts)["blockers"] == [
        "NFL_WEEK_MODEL_BUDGET_EXHAUSTED"
    ]


def test_partial_day_is_hold():
    expected, receipts = _case()
    receipts["2026-10-18"]["run_status"] = "PARTIAL"
    assert reconcile_week(expected, receipts)["blockers"] == [
        "NFL_WEEK_RESPONSE_INCOMPLETE"
    ]


def test_mismatched_day_is_hold():
    expected, receipts = _case()
    receipts["2026-10-18"]["requested_slate_date"] = "2026-10-17"
    assert reconcile_week(expected, receipts)["blockers"] == [
        "NFL_WEEK_RESPONSE_DATE_MISMATCH"
    ]


def test_false_governance_flag_is_hold():
    expected, receipts = _case()
    receipts["2026-10-18"]["can_execute"] = True
    assert reconcile_week(expected, receipts)["blockers"] == [
        "NFL_WEEK_RESPONSE_GOVERNANCE_INVALID"
    ]


def test_discovery_count_mismatch_is_hold():
    expected, receipts = _case()
    receipts["2026-10-18"]["cross_sport_discovery_audit"][
        "reconciliation"
    ]["events_discovered"] = 7
    assert reconcile_week(expected, receipts)["blockers"] == [
        "NFL_WEEK_DAILY_RECONCILIATION_INVALID"
    ]


def test_12_event_threshold_cannot_pass():
    expected, receipts = _case()
    del expected["2026-10-19"]
    del receipts["2026-10-19"]
    assert reconcile_week(expected, receipts)["blockers"] == [
        "NFL_WEEK_INCOMPLETE_OR_DUPLICATE_EXPECTED"
    ]


def test_lowered_threshold_is_hold():
    expected, receipts = _case()
    assert reconcile_week(expected, receipts, minimum_unique_events=12)[
        "blockers"
    ] == ["NFL_WEEK_THRESHOLD_INVALID"]


def test_malformed_identity_is_hold():
    expected, receipts = _case()
    receipts["2026-10-18"]["rows"][0]["identity"] = None
    assert reconcile_week(expected, receipts)["blockers"] == [
        "NFL_WEEK_IDENTITY_MALFORMED"
    ]


def test_extra_date_is_hold():
    expected, receipts = _case()
    receipts["2026-10-20"] = deepcopy(receipts["2026-10-19"])
    assert reconcile_week(expected, receipts)["blockers"] == [
        "NFL_WEEK_DAILY_RECEIPT_COVERAGE_MISMATCH"
    ]
