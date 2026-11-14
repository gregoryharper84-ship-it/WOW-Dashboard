"""Historical review-packet collector: only reads, never writes or relabels."""
from types import SimpleNamespace
import pytest

from test_v17_nfl_atd_historical_grade_packet import fixture, NOW
from v17 import nfl_atd_historical_readonly_collector as collector
from v17.nfl_atd_historical_grade_packet import HistoricalGradeHold

class DB:
    def __init__(self,predictions,snapshots,outcomes=None):
        self.records={
            "wow_predictions":list(predictions),
            "wow_prop_evidence_snapshots":list(snapshots.values()),
            "wow_outcomes":list(outcomes or []),
        }
        self.reads=[]
    def table(self,name):
        assert name in self.records
        db=self
        class Q:
            def select(self,cols):self.cols=cols;return self
            def in_(self,col,values):
                self.col=col;self.ids=set(values);return self
            def execute(self):
                db.reads.append((name,self.col))
                return SimpleNamespace(data=[
                    {k:v for k,v in r.items() if k in self.cols.split(",")}
                    for r in db.records[name]
                    if r.get(self.col) in self.ids
                ])
        return Q()

def test_collects_all_ten_exact_official_proposals_without_writes(monkeypatch):
    predictions,snapshots,proposals=fixture()
    db=DB(predictions,snapshots)
    calls=[]
    def official(p,s,*,http_get,now):
        calls.append(p["prediction_id"])
        return {"status":"HOLD_PENDING_PROTECTED_SETTLEMENT_ACCEPTANCE",
                "candidate_official_outcome":proposals[p["prediction_id"]]}
    monkeypatch.setattr(collector,"prepare_exact_nfl_td_official_outcome",official)
    result=collector.collect_historical_nfl_td_packet(db=db,now=NOW)
    assert len(calls)==10
    assert len(result["candidate_outcome_rows"])==10
    assert result["underlying_player_markets"]==5
    assert result["collector"]=="READ_ONLY_PROTECTED_EXACT_TEN_RECONCILIATION"
    assert result["historical_outcomes_persisted"] is False
    assert result["forward_calibration_eligible"] is False
    assert result["can_execute"] is False
    assert db.reads==[
        ("wow_predictions","prediction_id"),
        ("wow_outcomes","prediction_id"),
        ("wow_prop_evidence_snapshots","source_snapshot_id"),
    ]

def test_existing_outcome_blocks_partial_regrades(monkeypatch):
    predictions,snapshots,_=fixture()
    db=DB(predictions,snapshots,[{"prediction_id":predictions[0]["prediction_id"]}])
    monkeypatch.setattr(collector,"prepare_exact_nfl_td_official_outcome",
                        lambda *a,**k:pytest.fail("Never fetch on existing receipt"))
    with pytest.raises(HistoricalGradeHold,match="EXISTING_OUTCOME_REQUIRES_IDEMPOTENT_RECONCILIATION"):
        collector.collect_historical_nfl_td_packet(db=db,now=NOW)

def test_missing_frozen_snapshot_blocks_and_cannot_synthesize_dnp():
    predictions,snapshots,_=fixture()
    snapshots.pop(next(iter(snapshots)))
    with pytest.raises(HistoricalGradeHold,match="FROZEN_SOURCE_SNAPSHOT_MISSING"):
        collector.collect_historical_nfl_td_packet(db=DB(predictions,snapshots),now=NOW)

def test_missing_exact_prediction_and_unsupported_source_do_not_partially_grade(monkeypatch):
    predictions,snapshots,_=fixture()
    with pytest.raises(HistoricalGradeHold,match="PINNED_HISTORICAL_PREDICTIONS_MISSING"):
        collector.collect_historical_nfl_td_packet(db=DB(predictions[1:],snapshots),now=NOW)
    monkeypatch.setattr(collector,"prepare_exact_nfl_td_official_outcome",
        lambda *a,**k: {"status":"HELD","blocker":"OFFICIAL_PLAYER_NOT_IDENTIFIED"})
    with pytest.raises(HistoricalGradeHold,match="OFFICIAL_EVIDENCE_NOT_RECONCILED"):
        collector.collect_historical_nfl_td_packet(db=DB(predictions,snapshots),now=NOW)
