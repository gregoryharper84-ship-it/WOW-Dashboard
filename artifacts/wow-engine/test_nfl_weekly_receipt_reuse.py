"""A single immutable ID must never satisfy multiple NFL games."""
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
