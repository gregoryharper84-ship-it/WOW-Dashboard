"""Unit tests for the pure, network-free parts of live_gate_validation.py
(Step-5 review STEP5-VALIDATOR-FIX-01 through 04 + cleanup reconciliation,
following run fcec80e3's crash-and-false-negative evidence).

Everything else in this script talks to a live Supabase project by design
and is exercised only by actually running scripts/live_gate_validation.py,
not by this local suite -- these tests cover only the fixture-building,
timestamp-comparison, failure-isolation, and cleanup-classification logic
that does not require a live project.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone

import pytest

import live_gate_validation as lgv


# ---------------------------------------------------------------------
# STEP5-VALIDATOR-FIX-01: timestamp representation equivalence
# ---------------------------------------------------------------------

def test_z_and_offset_timestamps_compare_equal_as_instants():
    from calibration import _parse_ts
    assert _parse_ts("2026-12-01T00:00:00Z") == _parse_ts("2026-12-01T00:00:00+00:00")


def test_naive_timestamps_remain_rejected():
    from calibration import _parse_ts
    with pytest.raises(ValueError):
        _parse_ts("2026-12-01T00:00:00")


def test_section3_eligibility_uses_parsed_datetimes_not_string_order():
    from calibration import _parse_ts
    fixture = lgv.build_section3_fixture(n_normal=5)
    as_of_dt = _parse_ts(fixture["candidate_as_of"])
    normal = [r for r in fixture["rows"] if r["suffix"].startswith("past_")]
    assert len(normal) == 5
    for row in normal:
        assert _parse_ts(row["settlement_timestamp"]) < as_of_dt


# ---------------------------------------------------------------------
# STEP5-VALIDATOR-FIX-02: nondegenerate live Phase B fixture
# ---------------------------------------------------------------------

def test_positive_phase_b_fixture_has_at_least_200_eligible_rows():
    fixture = lgv.build_section3_fixture()
    normal_rows = [r for r in fixture["rows"] if r["suffix"] not in ("late_settle", "no_settlement")]
    assert len(normal_rows) >= 200


def test_fixture_training_n_agrees_with_positive_live_history():
    # Section 2 promotes v2 with training_n=SECTION4_TRAINING_N; Section 3/4's
    # default fixture must supply exactly that many eligible historical rows.
    fixture = lgv.build_section3_fixture()
    normal_rows = [r for r in fixture["rows"] if r["suffix"] not in ("late_settle", "no_settlement")]
    assert len(normal_rows) == lgv.SECTION4_TRAINING_N


def test_fixture_raw_probabilities_valid_and_outcomes_mixed():
    fixture = lgv.build_section3_fixture()
    normal_rows = [r for r in fixture["rows"] if r["suffix"] not in ("late_settle", "no_settlement")]
    probs = [r["raw_probability"] for r in normal_rows]
    outcomes = {r["hit"] for r in normal_rows}
    assert all(0.0 < p < 1.0 for p in probs)
    assert outcomes == {True, False}


def test_fixture_prediction_suffixes_unique():
    fixture = lgv.build_section3_fixture()
    suffixes = [r["suffix"] for r in fixture["rows"]]
    assert len(suffixes) == len(set(suffixes))


# ---------------------------------------------------------------------
# STEP5-VALIDATOR-FIX-03: fixtures stay deletable, chronology stays valid
# ---------------------------------------------------------------------

def test_normal_fixture_rows_have_future_event_starts_and_are_deletable():
    from calibration import _parse_ts
    before_call = datetime.now(timezone.utc)
    fixture = lgv.build_section3_fixture(n_normal=5)
    normal = [r for r in fixture["rows"] if r["suffix"].startswith("past_")]
    for row in normal:
        # Anchored years past `before_call`, so the DB's post-start
        # immutability trigger never fires regardless of when this test
        # (or the live script) actually runs.
        assert _parse_ts(row["event_start_time"]) > before_call


def test_late_settlement_chronology_is_logically_correct():
    from calibration import _parse_ts
    fixture = lgv.build_section3_fixture(n_normal=5)
    as_of_dt = _parse_ts(fixture["candidate_as_of"])
    late_settle = next(r for r in fixture["rows"] if r["suffix"] == "late_settle")
    assert _parse_ts(late_settle["event_start_time"]) < as_of_dt
    assert as_of_dt < _parse_ts(late_settle["settlement_timestamp"])


def test_no_settlement_row_has_no_settlement_timestamp():
    fixture = lgv.build_section3_fixture(n_normal=5)
    no_settlement = next(r for r in fixture["rows"] if r["suffix"] == "no_settlement")
    assert no_settlement["settlement_timestamp"] is None


def test_categorize_cleanup_row_deleted():
    assert lgv.categorize_cleanup_row("LIVE_GATE_abc123_past_0001", True) == "deleted"


def test_categorize_cleanup_row_expected_neg3():
    assert lgv.categorize_cleanup_row("LIVE_GATE_abc123_neg3", False) == "neg3_locked"


def test_categorize_cleanup_row_unexpected_locked():
    assert lgv.categorize_cleanup_row("LIVE_GATE_abc123_past_0001", False) == "unexpected_locked"


# ---------------------------------------------------------------------
# STEP5-HARNESS-BLOCKER-05: endpoint/neg1/neg2 fixtures are run-relative,
# not hardcoded to a calendar date -- only neg3 stays intentionally past.
# ---------------------------------------------------------------------

def test_future_event_timestamp_is_after_current_run_time():
    from calibration import _parse_ts
    before_call = datetime.now(timezone.utc)
    ts = lgv.future_event_timestamp()
    assert _parse_ts(ts) > before_call


def test_future_event_timestamp_shares_anchor_with_section3_fixture():
    # build_section3_fixture()'s first normal row and future_event_timestamp()
    # both resolve to future_anchor() called moments apart -- proof they
    # share one anchor rather than drifting independently, which is the
    # whole point of STEP5-HARNESS-BLOCKER-05.
    from calibration import _parse_ts
    fixture = lgv.build_section3_fixture(n_normal=1)
    first_row_ts = _parse_ts(fixture["rows"][0]["event_start_time"])
    endpoint_ts = _parse_ts(lgv.future_event_timestamp())
    assert abs((endpoint_ts - first_row_ts).total_seconds()) < 5


def test_endpoint_and_neg_fixtures_no_longer_hardcode_a_calendar_date():
    source = inspect.getsource(lgv.section_5_7_real_endpoint) + inspect.getsource(lgv.section_8_negative_paths)
    # The literal that stranded run fcec80e3's Section-3 rows and that
    # STEP5-HARNESS-BLOCKER-05 flagged as still present in Section 5-7 and
    # neg1/neg2 -- must not reappear anywhere in these functions.
    assert "2026-08-28" not in source


def test_neg1_and_neg2_use_future_event_timestamp():
    source = inspect.getsource(lgv.section_8_negative_paths)
    assert source.count("future_event_timestamp()") >= 2  # neg1 and neg2


def test_neg3_remains_intentionally_past_and_permanent():
    source = inspect.getsource(lgv.section_8_negative_paths)
    assert "2020-01-01T00:00:00Z" in source
    assert "already in the past" in source


# ---------------------------------------------------------------------
# STEP5-VALIDATOR-FIX-04: a governed section failure must not crash main()
# ---------------------------------------------------------------------

def test_run_or_record_failure_records_check_and_returns_none_on_exception():
    before = len(lgv.RESULTS)

    def boom():
        raise RuntimeError("synthetic failure")

    result = lgv.run_or_record_failure("synthetic step", boom)
    assert result is None
    assert len(lgv.RESULTS) == before + 1
    name, passed, detail = lgv.RESULTS[-1]
    assert passed is False
    assert "synthetic step" in name
    assert "synthetic failure" in detail


def test_run_or_record_failure_passes_through_result_on_success():
    before = len(lgv.RESULTS)
    result = lgv.run_or_record_failure("synthetic step", lambda x: x * 2, 21)
    assert result == 42
    assert len(lgv.RESULTS) == before  # no failure recorded on success


def test_run_or_record_failure_reraises_system_exit():
    def boom():
        raise SystemExit(1)

    with pytest.raises(SystemExit):
        lgv.run_or_record_failure("synthetic step", boom)
