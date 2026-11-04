"""Date-alignment telemetry must not turn an explicit next-day slate into a rejection."""
from datetime import datetime, timezone

import pytest

from v17 import daily_snapshot_runtime as daily


CROSSOVER = datetime(2026, 11, 3, 1, 10, tzinfo=timezone.utc)


def alignment(slate, zone="America/Chicago", now=CROSSOVER):
    return daily.evaluate_slate_date_alignment(slate, zone, now_utc=now)


def test_utc_crossover_observation_cannot_infer_request_intent():
    # Deliberate local "tomorrow" and erroneous UTC-derived "today" are the
    # exact SAME wire request. Neither can be conclusively labeled UTC-derived.
    explicit_tomorrow = alignment("2026-11-03")
    utc_derived_today = alignment("2026-11-03")
    assert explicit_tomorrow == utc_derived_today
    assert explicit_tomorrow["status"] == "UTC_LOCAL_DATE_DIVERGENCE"
    assert explicit_tomorrow["caller_local_date"] == "2026-11-02"
    assert explicit_tomorrow["utc_date"] == "2026-11-03"
    assert explicit_tomorrow["delta_days"] == 1
    assert explicit_tomorrow["caller_intent_verified"] is False
    assert explicit_tomorrow["can_execute"] is False


def test_aligned_slate_in_same_utc_crossover_is_not_flagged():
    result = alignment("2026-11-02")
    assert result["status"] == "ALIGNED"
    assert result["delta_days"] == 0


def test_utc_caller_has_no_utc_local_divergence():
    assert alignment("2026-11-03", "UTC")["status"] == "ALIGNED"


def test_explicit_forward_and_backward_dates_remain_legal():
    assert alignment("2026-11-04")["status"] == "AHEAD_OF_CALLER_LOCAL_DATE"
    assert alignment("2026-11-01")["status"] == "BEHIND_CALLER_LOCAL_DATE"
    assert alignment("2026-11-04")["delta_days"] == 2
    assert alignment("2026-11-01")["delta_days"] == -1


def test_invalid_zone_and_date_are_typed_not_fabricated():
    invalid_zone = alignment("2026-11-03", "Invalid/Timezone")
    invalid_date = alignment("2026-02-30")
    assert invalid_zone["status"] == "TIMEZONE_INVALID"
    assert invalid_zone["caller_local_date"] is None
    assert invalid_date["status"] == "REQUESTED_SLATE_DATE_INVALID"
    assert invalid_date["delta_days"] is None
    assert invalid_zone["can_execute"] is False
    assert invalid_date["can_execute"] is False


@pytest.mark.parametrize("observed", [
    datetime(2026, 3, 8, 7, 59, tzinfo=timezone.utc),  # 01:59 CST
    datetime(2026, 3, 8, 8, 1, tzinfo=timezone.utc),    # 03:01 CDT
    datetime(2026, 11, 1, 6, 59, tzinfo=timezone.utc), # 01:59 CDT
    datetime(2026, 11, 1, 7, 1, tzinfo=timezone.utc),  # 01:01 CST
])
def test_local_calendar_date_stable_across_dst_shift(observed):
    assert alignment(observed.date().isoformat(), "America/Chicago", observed)["status"] == "ALIGNED"


def _fixed_clock_runner(monkeypatch, *, slate, response_mode="COMPACT", max_props=6):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return CROSSOVER.astimezone(tz) if tz else CROSSOVER.replace(tzinfo=None)

    monkeypatch.setattr(daily, "datetime", FixedDateTime)
    monkeypatch.setattr(daily, "_prop_manifest_rows", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        daily,
        "acquire_daily_prop_snapshots",
        lambda **_kwargs: {
            "status": "COMPLETED", "attempted": 0, "persisted": 0,
            "receipts": [], "blockers": [], "can_execute": False,
        },
    )
    request = daily.DailySnapshotRequest(
        requested_slate_date=slate,
        requested_timezone="America/Chicago",
        lanes=["PROPS"],
        max_props=max_props,
        max_team_events=0,
        response_mode=response_mode,
    )
    return daily.run_daily_snapshot(
        request, db=object(), market_api=object(), event_api=object(),
        _heavy_permit=object(),
    )


@pytest.mark.parametrize("mode", ["COMPACT", "FULL"])
def test_empty_board_has_neutral_observation_without_changing_native_terminal(monkeypatch, mode):
    diverged = _fixed_clock_runner(monkeypatch, slate="2026-11-03", response_mode=mode)
    aligned = _fixed_clock_runner(monkeypatch, slate="2026-11-02", response_mode=mode)

    assert diverged["response_mode"] == mode
    assert diverged["slate_date_alignment"]["status"] == "UTC_LOCAL_DATE_DIVERGENCE"
    assert diverged["slate_date_warnings"] == ["WOW_V17_DAILY_SLATE_DATE_UTC_LOCAL_DIVERGENCE"]
    assert diverged["empty_board_diagnosis"]["observation"] == "ZERO_ROWS_WITH_DATE_DIVERGENCE"
    assert diverged["empty_board_diagnosis"]["caller_intent_verified"] is False
    assert "WOW_V17_DAILY_SLATE_DATE_UTC_LOCAL_DIVERGENCE" not in diverged["blockers"]
    assert diverged["run_status"] == aligned["run_status"]
    assert diverged["blockers"] == aligned["blockers"]
    assert diverged["reconciliation"] == aligned["reconciliation"]
    assert diverged["lane_reconciliation"] == aligned["lane_reconciliation"]
    assert diverged["rows"] == aligned["rows"] == []
    assert diverged["can_execute"] is aligned["can_execute"] is False
    assert aligned["slate_date_alignment"]["status"] == "ALIGNED"
    assert aligned["slate_date_warnings"] == []
    assert "empty_board_diagnosis" not in aligned


def test_intentional_zero_limit_is_not_diagnosed_as_missing_slate(monkeypatch):
    output = _fixed_clock_runner(monkeypatch, slate="2026-11-03", max_props=0)
    assert output["slate_date_alignment"]["status"] == "UTC_LOCAL_DATE_DIVERGENCE"
    assert "empty_board_diagnosis" not in output
    assert output["can_execute"] is False


def test_invalid_date_keeps_original_fail_closed_request_status(monkeypatch):
    result = _fixed_clock_runner(monkeypatch, slate="not-a-date")
    assert result["run_status"] == "RUN_INVALID_REQUEST"
    assert result["blockers"] == ["REQUESTED_SLATE_DATE_INVALID"]
    assert result["slate_date_alignment"]["status"] == "REQUESTED_SLATE_DATE_INVALID"
    assert result["can_execute"] is False
