"""Read-only exact-week receipt conservation (no scorer invocation)."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v17.nfl_weekly_receipt_conservation import audit_weekly_receipts

WEEK = 10
SEASON = 2026
T0 = datetime(2026, 11, 12, 23, 15, tzinfo=timezone.utc)


def schedule(n=14):
    rows = []
    for i in range(n):
        # Real event-key syntax; fictitious abbreviations represent synthetic fixtures.
        aid, hid = f"A{i:02d}", f"H{i:02d}"
        event_id = f"{SEASON}_{WEEK:02d}_{aid}_{hid}"
        t = T0 + timedelta(days=i // 5, minutes=i)
        rows.append({"official_event_id": event_id, "season": SEASON, "week": WEEK,
                     "away_team": aid, "home_team": hid,
                     "event_start_time_utc": t.isoformat()})
    rows[-1].update(official_event_id="2026_10_LAC_BAL", away_team="LAC", home_team="BAL")
    return rows


def predictions(schedule_rows):
    result = []
    for i, event in enumerate(schedule_rows):
        start = datetime.fromisoformat(event["event_start_time_utc"])
        result.append({"event_prediction_id": f"pred-{i}",
                       "official_event_id": event["official_event_id"],
                       "event_key": f"NFL:{event['official_event_id']}",
                       "event_start_time_utc": event["event_start_time_utc"],
                       "created_at": (start-timedelta(hours=2)).isoformat(),
                       "model_package_valid": True,"provenance_complete": True,
                       "model_probability_publishable": False,"can_execute": False})
    return result


def run(expected=14):
    return {"run_id":"week-10-authenticated-observed", "request_payload":
            {"requested_season":2026,"requested_week":10,
             "requested_slate_dates":[], "expected_game_count":expected},
            "run_status":"COMPLETED", "can_execute":False}


def audit(rows=None,preds=None,runs=None,expected=14,source="schedule-snapshot-1"):
    rows = schedule() if rows is None else rows
    preds = predictions(rows) if preds is None else preds
    return audit_weekly_receipts(rows,preds, [] if runs is None else runs,
                season=SEASON,week=WEEK,expected_game_count=expected,
                schedule_snapshot_id=source)


def test_13_of_14_exact_receipts_catches_missing_monday():
    rows=schedule()
    report=audit(rows,predictions(rows[:-1]))
    assert report["unique_schedule_event_count"] == 14
    assert report["pregame_receipt_count"] == 13
    assert report["missing_prediction_event_ids"] == ["2026_10_LAC_BAL"]
    assert report["status"] == "HOLD"
    assert "PICKEM_WEEKLY_RUN_NOT_OBSERVED" in report["blockers"]
    assert report["probability_publishable"] is False
    assert report["can_execute"] is False


def test_legacy_prefix_collision_cannot_create_fourteenth_receipt():
    rows=schedule()
    preds=predictions(rows[:-1])
    preds.append({**preds[0],"event_prediction_id":"old-week4", "official_event_id":"2026_04_ATL_NO",
                  "event_key":"NFL:2026-10-05:ATL@NO:401872979"})
    report=audit(rows,preds)
    assert report["pregame_receipt_count"] == 13
    assert report["missing_prediction_event_ids"] == ["2026_10_LAC_BAL"]
    assert "2026_04_ATL_NO" in report["unmatched_prediction_event_ids"]


def test_weekly_schedule_missing_one_does_not_pass_as_complete():
    rows=schedule()[:-1]
    report=audit(rows,predictions(rows),runs=[run()])
    assert "PICKEM_OFFICIAL_SLATE_COUNT_MISMATCH" in report["blockers"]
    assert report["unique_schedule_event_count"]==13
    assert report["status"] == "HOLD"


def test_latest_pregame_hold_overrides_older_true_flag():
    rows=schedule(1)
    preds=predictions(rows)
    older={**preds[0],"event_prediction_id":"older", "created_at":"2026-11-12T20:00:00+00:00","model_probability_publishable":True}
    newer={**preds[0],"event_prediction_id":"newer", "created_at":"2026-11-12T21:00:00+00:00","model_probability_publishable":False}
    report=audit(rows,[older,newer],runs=[run()],expected=1)
    assert report["pregame_receipt_count"]==1
    assert report["latest_stored_publishable_flag_count"]==0
    assert report["events"][0]["latest_prediction_id"] == "newer"
    assert report["probability_publishable"] is False


def test_duplicate_schedule_id_is_typed_not_double_counted():
    rows=schedule()
    rows.append(deepcopy(rows[0]))
    report=audit(rows,predictions(rows[:-1]),runs=[run()])
    assert report["unique_schedule_event_count"] == 14
    assert "PICKEM_DUPLICATE_CANONICAL_SCHEDULE_EVENT" in report["blockers"]
    assert report["status"] == "HOLD"


def test_post_kickoff_forecast_does_not_count():
    rows=schedule(1)
    preds=predictions(rows)
    start=datetime.fromisoformat(rows[0]["event_start_time_utc"])
    preds[0]["created_at"]=(start+timedelta(seconds=1)).isoformat()
    report=audit(rows,preds,runs=[run()],expected=1)
    assert report["pregame_receipt_count"]==0
    assert "PICKEM_PREGAME_PREDICTION_MISSING" in report["blockers"]


def test_kickoff_mismatch_does_not_count():
    rows=schedule(1)
    preds=predictions(rows)
    preds[0]["event_start_time_utc"]=(T0+timedelta(days=3)).isoformat()
    report=audit(rows,preds,runs=[run()],expected=1)
    assert report["pregame_receipt_count"]==0
    assert "PICKEM_PREGAME_PREDICTION_MISSING" in report["blockers"]


def test_no_official_schedule_snapshot_holds():
    rows=schedule(1)
    report=audit(rows,predictions(rows),runs=[run()],expected=1,source="")
    assert "PICKEM_SCHEDULE_SOURCE_UNVERIFIED" in report["blockers"]


def test_completed_week9_run_is_not_a_week10_run():
    rows=schedule(1)
    prior={**run(),"request_payload":{"requested_season":2026,"requested_week":9}}
    report=audit(rows,predictions(rows),runs=[prior],expected=1)
    assert report["observed_matching_completed_runs"] == 0
    assert "PICKEM_WEEKLY_RUN_NOT_OBSERVED" in report["blockers"]


def test_complete_14_is_research_only_not_certified_or_executable():
    rows=schedule()
    report=audit(rows,predictions(rows),runs=[run()])
    assert report["status"] == "COVERAGE_OBSERVED_NOT_CERTIFIED"
    assert report["unique_schedule_event_count"]==14
    assert report["pregame_receipt_count"]==14
    assert report["blockers"]==[]
    assert report["official_schedule_source_authenticated"] is False
    assert report["probability_publishable"] is False
    assert report["rank_eligible"] is False
    assert report["can_execute"] is False


def test_wrong_week_id_cannot_qualify():
    rows=schedule(1)
    rows[0]["official_event_id"]="2026_04_A00_H00"
    report=audit(rows,[],runs=[run()],expected=1)
    assert "PICKEM_SCHEDULE_EVENT_WEEK_IDENTITY_MISMATCH" in report["blockers"]
    assert report["unique_schedule_event_count"] == 0
    assert report["status"] == "HOLD"


def test_malformed_inputs_hold_without_crash():
    rows=schedule(1)
    rows.append({"official_event_id":None,"season":"n/a", "week":{},"home_team":{},"away_team":[],"event_start_time_utc":"NaN"})
    report=audit(rows,predictions(rows[:1]),runs=[run()],expected=1)
    assert "PICKEM_SCHEDULE_EVENT_IDENTITY_INVALID" in report["blockers"]
    assert report["status"]=="HOLD"


def test_equal_timestamp_conflicting_publishable_receipts_hold():
    rows = schedule(1)
    first = predictions(rows)[0]
    conflict = {**first, "event_prediction_id": "different-immutable-id",
                "model_probability_publishable": True}
    report = audit(rows, [first, conflict], runs=[run()], expected=1)
    assert report["status"] == "HOLD"
    assert "PICKEM_PREGAME_RECEIPT_ORDER_AMBIGUOUS" in report["blockers"]
    assert report["pregame_receipt_count"] == 0
    assert report["events"][0]["latest_prediction_id"] is None


def test_invalid_expected_count_rejects_request():
    rows = schedule(1)
    with pytest.raises(ValueError, match="PICKEM_AUDIT_REQUEST_INVALID"):
        audit(rows, predictions(rows), runs=[run()], expected=0)


def test_partial_date_only_scope_cannot_satisfy_full_week_run():
    rows = schedule()
    malformed_scope = {**run(), "request_payload":
                       {**run()["request_payload"], "requested_slate_dates": ["2026-11-12"]}}
    report = audit(rows, predictions(rows), runs=[malformed_scope])
    assert report["status"] == "HOLD"
    assert report["observed_matching_completed_runs"] == 0
    assert "PICKEM_WEEKLY_RUN_NOT_OBSERVED" in report["blockers"]


def test_wrong_game_count_scope_cannot_satisfy_full_week_run():
    rows = schedule()
    partial = {**run(), "request_payload":
               {**run()["request_payload"], "expected_game_count": 1}}
    report = audit(rows, predictions(rows), runs=[partial])
    assert report["status"] == "HOLD"
    assert report["observed_matching_completed_runs"] == 0
    assert "PICKEM_WEEKLY_RUN_NOT_OBSERVED" in report["blockers"]
