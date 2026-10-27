"""Checks for the immutable, read-only MLB incumbent report."""
import pytest
from mlb_incumbent_baseline_audit import audit_baseline

def item(id="p1", game="e1", prob=0.7, winner="HOME"):
    return dict(prediction_id=id, official_event_id=game, sport="MLB",
      market_family="OUTRIGHT_WINNER",
      event_start_time="2026-09-03T18:00:00+00:00",
      created_at="2026-09-03T12:00:00+00:00",
      model_timestamp="2026-09-03T11:00:00+00:00",
      latest_material_update_timestamp="2026-09-03T08:00:00+00:00",
      model_valid_after_latest_update=True,
      rank_eligible=True, probability_publishable=True,
      calibration_status="PASS", final_refresh_status="PASS",
      terminal_label="FINAL_APPROVED", can_execute=False,
      home_team="HOME", away_team="AWAY",
      calibrated_home_probability=prob, official_winner=winner,
      settlement_timestamp="2026-09-03T23:00:00+00:00",
      settlement_source="OFFICIAL", void=False)

def test_one_game_one_outcome():
    r=audit_baseline([item(),item("p2",prob=0.65),
                      item("p3","e3",prob=0.25,winner="AWAY")],
                     audited_at="2026-10-26T18:00:00Z")
    assert r["unique_events"]==2
    assert r["admitted_prediction_rows"]==3
    assert r["repeated_valid_predictions"]==1
    assert r["accounted_rows"] is True
    assert r["automatic_promotion_allowed"] is False
    assert r["training_or_model_comparison_performed"] is False

def test_bad_calibration_excluded():
    bad=item()
    bad["calibration_status"]="HOLD"
    r=audit_baseline([bad],audited_at="2026-10-26T18:00:00Z")
    assert r["unique_events"]==0
    assert r["excluded_by_reason"]=={"CALIBRATION_NOT_CERTIFIED_PASS":1}
