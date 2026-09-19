"""Regression tests for the V17 canonical publication admission gate."""
from datetime import datetime, timedelta, timezone

from publication_admission import (
    GOVERNED_PREDICTION_PK_COLUMNS,
    GOVERNED_PREDICTION_TABLES,
    evaluate_publication_eligibility,
    fetch_governed_predictions,
)

NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)
EVENT_START = (NOW + timedelta(hours=6)).isoformat()
LOCKED_AT = (NOW - timedelta(hours=1)).isoformat()


def base_row(**overrides):
    row = {
        "event_id": "MLB:NYY:BOS:20260919",
        "participant": "Gerrit Cole",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 5.5,
        "direction": "MORE",
        "event_start_time": EVENT_START,
        "probability_publishable": True,
        "terminal_label": "MODEL_QUALIFIED_RANK_ELIGIBLE",
        "governed_prediction_id": "11111111-1111-1111-1111-111111111111",
        "governed_prediction_table": "wow_predictions",
        "calibrated_probability": 0.62,
        "calibrated_probability_lower_bound": 0.55,
        "final_refresh": {"status": "PASS", "checked_at": NOW.isoformat()},
    }
    row.update(overrides)
    return row


def base_prediction(**overrides):
    prediction = {
        "prediction_id": "11111111-1111-1111-1111-111111111111",
        "event_id": "MLB:NYY:BOS:20260919",
        "player": "Gerrit Cole",
        "stat_type": "PITCHER_STRIKEOUTS",
        "line": 5.5,
        "direction": "MORE",
        "probability_publishable": True,
        "blockers": [],
        "calibrated_probability": 0.62,
        "calibrated_probability_lower_bound": 0.55,
        "locked_at": LOCKED_AT,
    }
    prediction.update(overrides)
    return prediction


def test_non_publishable_row_is_never_admitted_but_not_an_error():
    admission = evaluate_publication_eligibility(base_row(probability_publishable=False), None, now=NOW)
    assert admission.admitted is False
    assert admission.blockers == ("PUBLICATION_NOT_CLAIMED",)


def test_exact_match_against_frozen_prediction_is_admitted():
    admission = evaluate_publication_eligibility(base_row(), base_prediction(), now=NOW)
    assert admission.admitted is True
    assert admission.blockers == ()
    assert admission.governed_prediction_id == "11111111-1111-1111-1111-111111111111"


def test_no_matching_prediction_row_blocks_admission():
    admission = evaluate_publication_eligibility(base_row(), None, now=NOW)
    assert admission.admitted is False
    assert "PUBLICATION_NO_MATCHING_GOVERNED_PREDICTION_ROW" in admission.blockers


def test_line_mismatch_blocks_admission():
    admission = evaluate_publication_eligibility(base_row(), base_prediction(line=6.5), now=NOW)
    assert admission.admitted is False
    assert "PUBLICATION_EXACT_IDENTITY_MISMATCH:line" in admission.blockers


def test_direction_mismatch_blocks_admission():
    admission = evaluate_publication_eligibility(base_row(), base_prediction(direction="LESS"), now=NOW)
    assert admission.admitted is False
    assert "PUBLICATION_EXACT_IDENTITY_MISMATCH:direction" in admission.blockers


def test_event_id_mismatch_blocks_admission():
    admission = evaluate_publication_eligibility(base_row(), base_prediction(event_id="MLB:OTHER"), now=NOW)
    assert admission.admitted is False
    assert "PUBLICATION_EXACT_IDENTITY_MISMATCH:event_id" in admission.blockers


def test_upstream_prediction_not_publishable_blocks_admission():
    admission = evaluate_publication_eligibility(
        base_row(), base_prediction(probability_publishable=False), now=NOW
    )
    assert admission.admitted is False
    assert "PUBLICATION_UPSTREAM_PREDICTION_NOT_PUBLISHABLE" in admission.blockers


def test_upstream_prediction_blockers_are_not_admitted():
    admission = evaluate_publication_eligibility(
        base_row(), base_prediction(blockers=["SOME_DATA_GAP"]), now=NOW
    )
    assert admission.admitted is False
    assert "PUBLICATION_UPSTREAM_PREDICTION_HAS_BLOCKERS" in admission.blockers


def test_claimed_probability_must_match_governed_prediction_exactly():
    admission = evaluate_publication_eligibility(
        base_row(calibrated_probability=0.70), base_prediction(), now=NOW
    )
    assert admission.admitted is False
    assert "PUBLICATION_CLAIMED_PROBABILITY_DOES_NOT_MATCH_GOVERNED_PREDICTION" in admission.blockers


def test_invalid_calibration_package_blocks_admission():
    admission = evaluate_publication_eligibility(
        base_row(), base_prediction(calibrated_probability_lower_bound=0.90), now=NOW
    )
    assert admission.admitted is False
    assert "PUBLICATION_CALIBRATED_PROBABILITY_PACKAGE_INVALID" in admission.blockers


def test_prediction_locked_after_event_start_fails_pregame_check():
    late_lock = (NOW + timedelta(hours=7)).isoformat()
    admission = evaluate_publication_eligibility(base_row(), base_prediction(locked_at=late_lock), now=NOW)
    assert admission.admitted is False
    assert "PUBLICATION_PREDICTION_NOT_PROVEN_PREGAME" in admission.blockers


def test_missing_final_refresh_evidence_blocks_admission():
    row = base_row()
    row.pop("final_refresh")
    admission = evaluate_publication_eligibility(row, base_prediction(), now=NOW)
    assert admission.admitted is False
    assert "PUBLICATION_FINAL_REFRESH_EVIDENCE_MISSING" in admission.blockers


def test_final_refresh_not_pass_blocks_admission():
    admission = evaluate_publication_eligibility(
        base_row(final_refresh={"status": "HOLD", "checked_at": NOW.isoformat()}),
        base_prediction(),
        now=NOW,
    )
    assert admission.admitted is False
    assert any(b.startswith("PUBLICATION_FINAL_REFRESH_NOT_PASS") for b in admission.blockers)


def test_final_refresh_checked_in_future_blocks_admission():
    future_check = (NOW + timedelta(hours=1)).isoformat()
    admission = evaluate_publication_eligibility(
        base_row(final_refresh={"status": "PASS", "checked_at": future_check}),
        base_prediction(),
        now=NOW,
    )
    assert admission.admitted is False
    assert "PUBLICATION_FINAL_REFRESH_CHECKED_AT_IN_FUTURE" in admission.blockers


def test_research_hold_terminal_label_can_never_be_admitted():
    admission = evaluate_publication_eligibility(
        base_row(terminal_label="MODEL_QUALIFIED_RESEARCH_INTEREST"), base_prediction(), now=NOW
    )
    assert admission.admitted is False
    assert any(b.startswith("PUBLICATION_TERMINAL_LABEL_NOT_ELIGIBLE") for b in admission.blockers)


def test_unrecognized_governed_prediction_table_is_rejected():
    admission = evaluate_publication_eligibility(
        base_row(governed_prediction_table="anything_a_caller_names"), None, now=NOW
    )
    assert admission.admitted is False
    assert any(
        b.startswith("PUBLICATION_GOVERNED_PREDICTION_TABLE_NOT_RECOGNIZED") for b in admission.blockers
    )


def test_fetch_governed_predictions_groups_by_table_and_skips_unrecognized():
    calls = []

    class FakeQuery:
        def __init__(self, table_name, ids):
            self.table_name = table_name
            self.ids = ids

        def execute(self):
            calls.append((self.table_name, tuple(self.ids)))
            if self.table_name == "wow_predictions":
                return type("R", (), {"data": [base_prediction()]})()
            return type("R", (), {"data": []})()

    class FakeTable:
        def __init__(self, name):
            self.name = name

        def select(self, _cols):
            return self

        def in_(self, _col, ids):
            return FakeQuery(self.name, ids)

    class FakeClient:
        def table(self, name):
            return FakeTable(name)

    fetched = fetch_governed_predictions(
        lambda: FakeClient(),
        [
            ("wow_predictions", "11111111-1111-1111-1111-111111111111"),
            ("not_a_governed_table", "22222222-2222-2222-2222-222222222222"),
            (None, None),
        ],
    )
    assert ("wow_predictions", "11111111-1111-1111-1111-111111111111") in fetched
    assert len(calls) == 1
    assert calls[0][0] == "wow_predictions"


def test_pk_columns_cover_every_governed_table():
    assert set(GOVERNED_PREDICTION_PK_COLUMNS) == GOVERNED_PREDICTION_TABLES
