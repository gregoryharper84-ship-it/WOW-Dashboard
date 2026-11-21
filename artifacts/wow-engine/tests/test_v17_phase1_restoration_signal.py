import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v17.phase1_restoration_signal import classify_refresh_run


def run(*, completed=0, held=0, rejected=0, purged=0, invalid=0, unclassified=0):
    return {
        "run_id": "v17-daily-test-001", "run_status": "COMPLETED", "can_execute": False,
        "global_terminal_authority": "V17_TERMINAL_REDUCER",
        "reconciliation": {
            "rows_in": completed + held + rejected + purged + invalid + unclassified,
            "rows_completed": completed, "rows_held": held,
            "rows_rejected": rejected, "rows_purged": purged,
            "rows_invalid": invalid, "rows_unclassified": unclassified,
            "balanced": True,
        }
    }


def test_all_held_does_not_count_as_restored():
    result = classify_refresh_run(run(held=26))
    assert result["signal"] == "ZERO_COMPLETED_ROWS"
    assert result["action_required"] is True
    assert result["rows_held"] == 26
    assert result["restoration_status"] == "WORKING_NOT_COMPLETE"
    assert result["can_execute"] is False
    assert result["model_certification_claimed"] is False


def test_scored_rows_are_not_proof_of_product_restoration():
    result = classify_refresh_run(run(completed=7))
    assert result["signal"] == "SCORING_OBSERVED_NOT_ACCEPTED"
    assert result["restoration_status"] == "WORKING_NOT_COMPLETE"
    assert result["model_certification_claimed"] is False


def test_partial_slate_is_visible_not_false_green():
    result = classify_refresh_run(run(completed=3, held=2, rejected=1))
    assert result["signal"] == "PARTIAL_SCORING"
    assert result["action_required"] is True


def test_zero_rows_is_not_a_claim_there_are_no_games():
    result = classify_refresh_run(run())
    assert result["signal"] == "NO_ROWS_OBSERVED"
    assert result["restoration_status"] == "WORKING_NOT_COMPLETE"


@pytest.mark.parametrize("bad", [
    {"can_execute": True},
    {"global_terminal_authority": "OTHER"},
    {"reconciliation": {"balanced": False}},
    {"reconciliation": {"rows_in": 2}},
])
def test_missing_or_invalid_governed_receipts_fail_closed(bad):
    data = run(held=1)
    data.update(bad)
    with pytest.raises(ValueError):
        classify_refresh_run(data)


def test_negative_or_boolean_counts_are_not_accepted():
    for bad_value in (-1, True, "1"):
        data = run(held=1)
        data["reconciliation"]["rows_held"] = bad_value
        with pytest.raises(ValueError):
            classify_refresh_run(data)


def test_existing_hourly_refresh_emits_phase1_signal_without_new_schedule():
    workflow = Path(__file__).resolve().parents[3] / '.github/workflows/wow-v17-team-event-recoverable-refresh.yml'
    contents = workflow.read_text()
    assert 'from v17.phase1_restoration_signal import classify_refresh_run' in contents
    assert 'restoration_phase1 = classify_refresh_run(result)' in contents
    assert '"phase1_restoration": restoration_phase1' in contents
    assert 'GITHUB_STEP_SUMMARY' in contents
    assert 'cron: "17 * * * *"' in contents

def _compact_row(sport: str, status: str) -> dict:
    return {
        "lane": "MONEYLINE",
        "identity": {"sport": sport},
        "row_status": status,
        "can_execute": False,
    }


@pytest.mark.parametrize("bad_status", [None, "FAILED", "RUN_INVALID_REQUEST", "PENDING", "SUCCEEDED"])
def test_noncompleted_daily_run_cannot_report_restoration(bad_status):
    receipt = run(held=1)
    receipt["run_status"] = bad_status
    with pytest.raises(ValueError, match="RESTORATION_RUN_STATUS_INVALID"):
        classify_refresh_run(receipt)


def test_acquisition_blockers_are_a_real_completed_daily_state():
    receipt = run(held=1)
    receipt["run_status"] = "COMPLETED_WITH_ACQUISITION_BLOCKERS"
    receipt["blockers"] = ["SOURCE_ACQUISITION_INCOMPLETE"]
    receipt["rows"] = [_compact_row("NBA", "HELD")]
    result = classify_refresh_run(receipt)
    assert result["acquisition_blocked"] is True
    assert result["run_status"] == "COMPLETED_WITH_ACQUISITION_BLOCKERS"
    assert result["signal"] == "ZERO_COMPLETED_ROWS"


def test_per_sport_reconciliation_prevents_one_sport_masking_others():
    receipt = run(completed=1, held=1, rejected=1)
    receipt["rows"] = [
        _compact_row("NBA", "HELD"),
        _compact_row("NHL", "COMPLETED"),
        _compact_row("NCAAB", "REJECTED"),
    ]
    result = classify_refresh_run(receipt)
    assert result["signal"] == "PARTIAL_SCORING"
    assert result["sport_breakdown_status"] == "AVAILABLE"
    assert result["sport_breakdown"]["NBA"]["signal"] == "ZERO_COMPLETED_ROWS"
    assert result["sport_breakdown"]["NHL"]["signal"] == "SCORING_OBSERVED_NOT_ACCEPTED"
    assert result["sport_breakdown"]["NCAAB"]["rows_rejected"] == 1
    assert result["restoration_status"] == "WORKING_NOT_COMPLETE"


@pytest.mark.parametrize("rows", [
    [_compact_row("NBA", "COMPLETED")],
    [_compact_row("NBA", "HELD"), _compact_row("NHL", "HELD")],
])
def test_malformed_compact_rows_do_not_silently_reconcile(rows):
    receipt = run(held=1)
    receipt["rows"] = rows
    with pytest.raises(ValueError, match="RESTORATION_SPORT_RECONCILIATION_INVALID"):
        classify_refresh_run(receipt)


def test_missing_row_manifest_is_explicitly_unknown_not_complete():
    result = classify_refresh_run(run(held=1))
    assert result["sport_breakdown_status"] == "ROW_DETAILS_NOT_AVAILABLE"
    assert result["sport_breakdown"] == {}
    assert result["restoration_status"] == "WORKING_NOT_COMPLETE"


def test_genuine_no_row_response_preserves_no_coverage_claim():
    receipt = run()
    receipt["rows"] = []
    result = classify_refresh_run(receipt)
    assert result["signal"] == "NO_ROWS_OBSERVED"
    assert result["sport_breakdown"] == {}
    assert result["restoration_status"] == "WORKING_NOT_COMPLETE"
