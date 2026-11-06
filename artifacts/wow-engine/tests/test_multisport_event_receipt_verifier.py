"""Contract tests only: fake DB and RPC are NOT official model/reducer proof."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from v17.multisport_event_receipt_verifier import (
    verify_multisport_event_receipt as verify,
    TABLE, RPC, ID_HOLD, LOOKUP_HOLD, MATCH_HOLD,
    NUM_HOLD, MISSING_HOLD, ROW_HOLD,
)

RID = "116f0d3a-1c2b-4e3f-9a4b-1f2c3d4e5f60"
SID = "216f0d3a-1c2b-4e3f-9a4b-1f2c3d4e5f60"
TID = "316f0d3a-1c2b-4e3f-9a4b-1f2c3d4e5f60"
T = "2026-11-06T20:00:00+00:00"
TIMES = dict(source_snapshot_timestamp="2026-11-06T15:00:00+00:00",
             model_timestamp="2026-11-06T16:00:00+00:00",
             locked_at="2026-11-06T16:05:00+00:00",
             terminal_reduced_at="2026-11-06T16:10:00+00:00",
             created_at="2026-11-06T16:15:00+00:00",
             event_start_time=T)


def fixture(sport="NFL"):
    home, away = "H", "A"
    basis = {"NFL": "FULL_GAME_INCLUDING_OVERTIME", "NBA": "FULL_GAME_INCLUDING_OVERTIME",
             "NHL": "FULL_GAME_MONEYLINE_INCLUDING_OVERTIME_SHOOTOUT"}[sport]
    row = dict(
        event_prediction_id=RID, score_snapshot_id=SID, reducer_receipt_id=TID,
        schema_version="WOW_TEAM_EVENT_RECEIPT_V1", sport=sport,
        official_event_id="official-789", market_family="OUTRIGHT_WINNER",
        settlement_basis=basis, home_team=home, away_team=away,
        selected_participant=home, source_snapshot_id="snapshot-A",
        model_artifact_id="model-m", model_version="v17m",
        model_artifact_sha256="a"*64, calibration_artifact_id="cal-1",
        calibration_version="v17c", calibration_artifact_sha256="b"*64,
        reducer_receipt_sha256="c"*64, terminal_authority="V17_TERMINAL_REDUCER",
        terminal_status="PASS", terminal_label="FINAL_APPROVED",
        calibrated_home_probability=Decimal("0.61"),
        calibrated_home_lower_bound=Decimal("0.55"),
        calibrated_home_upper_bound=Decimal("0.65"),
        calibrated_away_probability=Decimal("0.39"),
        calibrated_away_lower_bound=Decimal("0.35"),
        calibrated_away_upper_bound=Decimal("0.45"),
        probability_publishable=True, rank_eligible=True, can_execute=False,
        **TIMES)
    event = dict(sport=sport, official_event_id="official-789", home_team=home,
                 away_team=away, event_start_time_utc=T)
    winner = dict(source_receipt_id=RID, score_snapshot_id=SID, reducer_receipt_id=TID,
                  official_event_id="official-789", source_snapshot_id="snapshot-A",
                  source_snapshot_timestamp=TIMES["source_snapshot_timestamp"],
                  model_timestamp=TIMES["model_timestamp"],
                  model_artifact_id="model-m", model_version="v17m",
                  model_artifact_sha256="a"*64, calibration_artifact_id="cal-1",
                  calibration_version="v17c", calibration_artifact_sha256="b"*64,
                  reducer_receipt_sha256="c"*64, terminal_authority="V17_TERMINAL_REDUCER",
                  side=home, calibrated_probability=Decimal("0.61"),
                  calibrated_probability_lower_bound=Decimal("0.55"),
                  calibrated_probability_upper_bound=Decimal("0.65"),
                  probability_publishable=True, rank_eligible=True, can_execute=False)
    reducer = dict(status="PASS", terminal_label="FINAL_APPROVED",
                   terminal_authority="V17_TERMINAL_REDUCER", can_execute=False,
                   probability_publishable=True, rank_eligible=True,
                   event_prediction_id=RID, score_snapshot_id=SID, reducer_receipt_id=TID,
                   reducer_receipt_sha256="c"*64)
    return row,event,winner,reducer


class Query:
    def __init__(self, database, operation, target):
        self.database, self.operation, self.target = database, operation, target
        self.calls = []
    def select(self, cols):
        self.calls.append(("select", cols)); return self
    def eq(self, key, val):
        self.calls.append(("eq", key, val)); return self
    def limit(self, n):
        self.calls.append(("limit", n)); return self
    def execute(self):
        if self.operation == "table" and self.database.table_error:
            raise RuntimeError("DB_TABLE_MISSING")
        if self.operation == "rpc" and self.database.rpc_error:
            raise RuntimeError("DB_RPC_MISSING")
        return SimpleNamespace(data=self.database.rows if self.operation == "table"
                               else self.database.reducer)


class DB:
    def __init__(self, row, reducer):
        self.rows = [row]
        self.reducer = reducer
        self.table_error = False
        self.rpc_error = False
        self.table_calls = []
        self.rpc_calls = []
    def table(self, name):
        assert name == TABLE
        self.table_calls.append(name)
        return Query(self, "table", name)
    def rpc(self, name, params):
        assert name == RPC
        assert params == {"p_event_prediction_id": RID}
        self.rpc_calls.append((name,params))
        return Query(self,"rpc",name)


@pytest.mark.parametrize("sport", ["NFL","NBA","NHL"])
def test_proposed_contract_only_fixture_passes_when_both_fake_receipts_match(sport):
    row,event,winner,reducer=fixture(sport)
    db=DB(row,reducer)
    assert verify(db,event=event,winner=winner) is None
    assert len(db.rpc_calls)==1


def test_no_receipt_id_does_not_call_database():
    row,event,winner,reducer=fixture(); winner["source_receipt_id"]="fake-id"
    db=DB(row,reducer)
    assert verify(db,event=event,winner=winner)==ID_HOLD
    assert not db.table_calls


def test_no_db_is_not_model_unavailable():
    row,event,winner,_=fixture()
    assert verify(None,event=event,winner=winner)==MISSING_HOLD


def test_missing_multisport_table_preserves_infrastructure_hold():
    row,event,winner,reducer=fixture(); db=DB(row,reducer); db.table_error=True
    assert verify(db,event=event,winner=winner)==LOOKUP_HOLD


def test_nonexistent_or_ambiguous_ledger_receipt_is_not_exactly_one():
    row,event,winner,reducer=fixture(); db=DB(row,reducer)
    for rows in ([],[row,row]):
        db.rows=rows
        assert verify(db,event=event,winner=winner)==ROW_HOLD
        assert not db.rpc_calls


def test_reducer_rpc_absence_is_infrastructure_hold_not_success():
    row,event,winner,reducer=fixture(); db=DB(row,reducer); db.rpc_error=True
    assert verify(db,event=event,winner=winner)==LOOKUP_HOLD


@pytest.mark.parametrize("field,value", [
    ("status","HOLD"),("terminal_label","MODEL_QUALIFIED_HOLD"),
    ("terminal_authority","OTHER_REDUCER"),("can_execute",True),
    ("score_snapshot_id","416f0d3a-1c2b-4e3f-9a4b-1f2c3d4e5f60"),
    ("reducer_receipt_sha256","0"*64),
])
def test_fake_reducer_must_be_exact_on_every_admission_field(field,value):
    row,event,winner,reducer=fixture(); reducer[field]=value
    assert verify(DB(row,reducer),event=event,winner=winner)==MATCH_HOLD


@pytest.mark.parametrize("field,value", [
    ("sport","MLB"), ("settlement_basis","REGULATION_ONLY"),
    ("official_event_id","other"),("selected_participant","THIRD_TEAM"),
    ("model_artifact_sha256","not-a-sha"),("probability_publishable",False),
    ("can_execute",True), ("terminal_status","HOLD"),
    ("score_snapshot_id","416f0d3a-1c2b-4e3f-9a4b-1f2c3d4e5f60"),
])
def test_ledger_mismatch_is_held_before_reducer_query(field,value):
    row,event,winner,reducer=fixture(); row[field]=value
    db=DB(row,reducer)
    assert verify(db,event=event,winner=winner)==MATCH_HOLD
    assert not db.rpc_calls


@pytest.mark.parametrize("field,value", [
    ("created_at","2026-11-06T21:00:00+00:00"),
    ("model_timestamp","2026-11-06T16:20:00+00:00"),
    ("terminal_reduced_at","2026-11-06T15:00:00+00:00"),
    ("created_at","2026-11-06T16:00:00"),
    ("event_start_time","0001-01-01T00:00:00+14:00"),
])
def test_strict_receipt_chronology_and_aware_time(field,value):
    row,event,winner,reducer=fixture(); row[field]=value
    assert verify(DB(row,reducer),event=event,winner=winner)==MATCH_HOLD


@pytest.mark.parametrize("field,value", [
    ("calibrated_home_lower_bound",Decimal("0.6100000000000000000001")),
    ("calibrated_home_upper_bound",Decimal("0.60")),
    ("calibrated_home_probability",Decimal("NaN")),
    ("calibrated_home_probability",True),
    ("calibrated_home_probability",Decimal("1")),
    ("calibrated_away_probability",Decimal("0.50")),
])
def test_exact_decimal_calibrated_domains_and_pair(field,value):
    row,event,winner,reducer=fixture(); row[field]=value
    assert verify(DB(row,reducer),event=event,winner=winner)==NUM_HOLD


def test_saved_selected_side_cannot_be_inferred_from_favorite():
    row,event,winner,reducer=fixture(); winner["side"]="third team"
    assert verify(DB(row,reducer),event=event,winner=winner)==MATCH_HOLD


def test_winner_point_mismatch_is_numerical_not_model_absence():
    row,event,winner,reducer=fixture(); winner["calibrated_probability"]=Decimal("0.62")
    assert verify(DB(row,reducer),event=event,winner=winner)==NUM_HOLD


def test_even_claimed_approved_row_cannot_pass_without_real_rpc():
    row,event,winner,reducer=fixture()
    db=DB(row,reducer); db.reducer=None
    assert verify(db,event=event,winner=winner)==LOOKUP_HOLD


def test_original_mlb_ledger_is_never_queried():
    row,event,winner,reducer=fixture()
    db=DB(row,reducer)
    assert verify(db,event=event,winner=winner) is None
    assert db.table_calls==[TABLE]


@pytest.mark.parametrize("requested_id", ["other-official-game", "", None])
def test_foreign_or_missing_requested_official_event_holds_before_rpc(requested_id):
    """Ledger and winner may collude on one ID; requested event must also match."""
    row, event, winner, reducer = fixture()
    event["official_event_id"] = requested_id
    db = DB(row, reducer)
    assert verify(db, event=event, winner=winner) == MATCH_HOLD
    assert not db.rpc_calls


def test_exact_requested_official_event_still_passes_fake_contract():
    row, event, winner, reducer = fixture()
    db = DB(row, reducer)
    assert verify(db, event=event, winner=winner) is None
    assert len(db.rpc_calls) == 1
