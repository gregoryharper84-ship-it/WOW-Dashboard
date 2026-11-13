"""A single immutable ID must never satisfy multiple NFL games."""
from datetime import datetime, timedelta
from test_nfl_weekly_receipt_conservation import schedule, predictions, run, audit

def test_reused_prediction_uuid_across_games_fails_closed():
    rows=schedule(2)
    preds=predictions(rows)
    preds[1]["event_prediction_id"]=preds[0]["event_prediction_id"]
    report=audit(rows,preds,runs=[run()],expected=2)
    assert "PICKEM_REUSED_IMMUTABLE_PREDICTION_ID" in report["blockers"]
    assert report["pregame_receipt_count"]==0
    assert sorted(report["missing_prediction_event_ids"]) == sorted(row["official_event_id"] for row in rows)
    assert report["status"]=="HOLD"


def test_postkickoff_reuse_invalidates_other_game():
    rows = schedule(2)
    preds = predictions(rows)
    reused = {**preds[1], "event_prediction_id": preds[0]["event_prediction_id"]}
    reused["created_at"] = (
        datetime.fromisoformat(rows[1]["event_start_time_utc"]) + timedelta(seconds=1)
    ).isoformat()
    report = audit(rows, [*preds, reused], runs=[run()], expected=2)
    assert report["status"] == "HOLD"
    assert "PICKEM_REUSED_IMMUTABLE_PREDICTION_ID" in report["blockers"]
    assert report["pregame_receipt_count"] == 1
    assert report["missing_prediction_event_ids"] == [rows[0]["official_event_id"]]


def test_whitespace_alias_reuse_invalidates_every_receipt():
    rows = schedule(2)
    preds = predictions(rows)
    preds[0]["event_prediction_id"] = "  same-id  "
    preds[1]["event_prediction_id"] = "same-id"
    report = audit(rows, preds, runs=[run(expected=2)], expected=2)
    assert report["status"] == "HOLD"
    assert "PICKEM_REUSED_IMMUTABLE_PREDICTION_ID" in report["blockers"]
    assert report["pregame_receipt_count"] == 0
    assert sorted(report["missing_prediction_event_ids"]) == sorted(
        row["official_event_id"] for row in rows
    )
