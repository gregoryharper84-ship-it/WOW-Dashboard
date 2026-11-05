"""NFL admission caller must never self-certify model or terminal claims."""
from __future__ import annotations

from types import SimpleNamespace
import pytest

from v17.multisport_event_receipt_admission import (
    admit_verified_nfl_score as admit, ADMIT_RPC, VERIFY_RPC,
)

SCORE="216f0d3a-1c2b-4e3f-9a4b-1f2c3d4e5f60"
RECEIPT="116f0d3a-1c2b-4e3f-9a4b-1f2c3d4e5f60"
REDUCER="316f0d3a-1c2b-4e3f-9a4b-1f2c3d4e5f60"
HASH="a"*64


class RPC:
    def __init__(self, db, name):
        self.db, self.name = db, name
    def execute(self):
        if self.name in self.db.fail:
            raise RuntimeError("backend unavailable")
        return SimpleNamespace(data=(self.db.admission if self.name==ADMIT_RPC
                                     else self.db.verified))


class DB:
    def __init__(self):
        self.calls=[]
        self.fail=set()
        self.admission=dict(status="PERSISTED",event_prediction_id=RECEIPT,
                            score_snapshot_id=SCORE,reducer_receipt_id=REDUCER,
                            reducer_receipt_sha256=HASH,can_execute=False)
        self.verified=dict(status="PASS",terminal_label="FINAL_APPROVED",
                           terminal_authority="V17_TERMINAL_REDUCER",
                           rank_eligible=True,probability_publishable=True,
                           can_execute=False,event_prediction_id=RECEIPT,
                           score_snapshot_id=SCORE,reducer_receipt_id=REDUCER,
                           reducer_receipt_sha256=HASH)
    def rpc(self,name,args):
        self.calls.append((name,args))
        assert name in {ADMIT_RPC,VERIFY_RPC}
        return RPC(self,name)


@pytest.mark.parametrize("status",["PERSISTED","ALREADY_PERSISTED"])
def test_only_db_generated_ids_are_returned_after_independent_reread(status):
    db=DB(); db.admission["status"]=status
    result=admit(db,score_snapshot_id=SCORE)
    assert result["status"]=="RECEIPT_VERIFIED"
    assert result["source_receipt_id"]==RECEIPT
    assert result["can_execute"] is False
    assert "calibrated_probability" not in result
    assert db.calls==[
        (ADMIT_RPC,{"p_score_snapshot_id":SCORE}),
        (VERIFY_RPC,{"p_event_prediction_id":RECEIPT}),
    ]


def test_invalid_score_uuid_stops_before_any_database_call():
    db=DB()
    assert admit(db,score_snapshot_id="spoof")["code"]=="LLP_EVENT_PREDICTION_ID_UNVERIFIED"
    assert db.calls==[]


def test_no_database_is_not_model_unavailable():
    assert admit(None,score_snapshot_id=SCORE)["code"]=="LLP_EVENT_PREDICTION_LOOKUP_NOT_PERFORMED"


@pytest.mark.parametrize("missing",["admit","verify"])
def test_missing_backend_rpc_is_a_typed_infrastructure_hold(missing):
    db=DB()
    db.fail.add(ADMIT_RPC if missing=="admit" else VERIFY_RPC)
    held=admit(db,score_snapshot_id=SCORE)
    assert held["status"]=="HELD"
    assert held["code"]=="LLP_EVENT_PREDICTION_LOOKUP_UNAVAILABLE"
    assert held["can_execute"] is False


@pytest.mark.parametrize("field,value",[
    ("event_prediction_id","spoof"),("score_snapshot_id",REDUCER),
    ("reducer_receipt_id","spoof"),("reducer_receipt_sha256","not-sha"),
    ("can_execute",True),("status","MAYBE"),
])
def test_incomplete_or_spoofed_insert_ack_never_gets_trusted(field,value):
    db=DB(); db.admission[field]=value
    held=admit(db,score_snapshot_id=SCORE)
    assert held["status"]=="HELD"
    assert held["can_execute"] is False
    assert len(db.calls)==1


@pytest.mark.parametrize("field,value",[
    ("status","HOLD"),("terminal_label","MODEL_QUALIFIED_HOLD"),
    ("terminal_authority","OTHER"),("probability_publishable",False),
    ("rank_eligible",False),("can_execute",True),
    ("event_prediction_id",REDUCER),("score_snapshot_id",RECEIPT),
    ("reducer_receipt_id",RECEIPT),("reducer_receipt_sha256","b"*64),
])
def test_issuer_ack_is_insufficient_without_independent_reducer_exact_match(field,value):
    db=DB(); db.verified[field]=value
    held=admit(db,score_snapshot_id=SCORE)
    assert held["status"]=="HELD"
    assert held["code"]=="LLP_EVENT_PREDICTION_RECEIPT_MISMATCH"
    assert held["can_execute"] is False


def test_backend_governed_hold_preserves_native_reason_without_fallback():
    db=DB(); db.admission=dict(status="HOLD",
                              code="LLP_EVENT_PREDICTION_RECEIPT_MISMATCH",
                              can_execute=False)
    held=admit(db,score_snapshot_id=SCORE)
    assert held["code"]=="LLP_EVENT_PREDICTION_RECEIPT_MISMATCH"
    assert len(db.calls)==1
    assert held["probability_publishable"] is False


def test_unrecognized_backend_failure_code_is_never_exposed_or_promoted():
    db=DB(); db.admission=dict(status="HOLD",code="SOME_UNTRUSTED_FAILURE")
    held=admit(db,score_snapshot_id=SCORE)
    assert held["code"]=="LLP_EVENT_PREDICTION_RECEIPT_MISMATCH"
    assert held["can_execute"] is False


def test_public_api_never_accepts_caller_price_side_or_probability():
    import inspect
    assert tuple(inspect.signature(admit).parameters)==("db","score_snapshot_id")
