"""No-write exact historical ATD packet adversarial tests."""
from datetime import datetime, timedelta, timezone
import copy
import pytest
from v17.nfl_atd_historical_grade_packet import (
    PINNED, SOURCES, HistoricalGradeHold, prepare_historical_packet,
)

START = datetime(2026,10,18,17,tzinfo=timezone.utc)
NOW = datetime(2026,10,21,23,tzinfo=timezone.utc)

def fixture():
    predictions=[]
    snapshots={}
    proposals={}
    for ident,(event,player,side) in PINNED.items():
        sid="frozen:"+event+":"+player
        predictions.append({
            "prediction_id":ident,"sport":"NFL","stat_type":"ANYTIME_TD",
            "event_id":event,"player":player,"direction":side,"line":"0.5",
            "model_provider_identity":"WOW_PROP_FITTED_MODEL_V1",
            "evidence_source_kind":None,"calibrated_probability":
              ("0.40" if side=="MORE" else "0.60"),
            "event_start_time":START.isoformat(),
            "created_at":(START-timedelta(minutes=50)).isoformat(),
            "model_timestamp":(START-timedelta(minutes=51)).isoformat(),
            "locked_at":(START-timedelta(minutes=49)).isoformat(),
            "source_snapshot_id":sid,
        })
        snapshots[sid]={
            "source_snapshot_id":sid,"event_id":event,"player":player,
            "captured_at":(START-timedelta(hours=2)).isoformat(),
            "role_status":{
                "identity_binding_status":"PASS",
                "verified_canonical_event_id":event,
                "player":player,
            }}
        proposals[ident]={
            "status":"PROPOSAL_REQUIRES_INDEPENDENT_SETTLEMENT_ACCEPTANCE",
            "prediction_id":ident,"event_id":event,"player":player,
            "direction":side,"line":"0.5","actual_stat":0,
            "hypothetical_official_result":"HIT" if side=="LESS" else "MISS",
            "source_url":SOURCES[event],
            "source_sha256":("a" if event.endswith("PHI") else "b")*64,
            "snapshot_id":sid,"participation_evidence":"POSITIVE_OFFICIAL_FINAL_GAME_STAT",
            "settlement_persisted":False,"can_execute":False,
        }
    return predictions,snapshots,proposals

def build(p=None,s=None,o=None):
    orig=fixture()
    return prepare_historical_packet(p if p is not None else orig[0],
                                     s if s is not None else orig[1],
                                     o if o is not None else orig[2],now=NOW)

def test_historical_ten_receipts_are_not_ten_sporting_trials():
    result=build()
    assert result["original_prediction_count"]==10
    assert result["underlying_player_markets"]==5
    assert len(result["candidate_outcome_rows"])==10
    assert sum(r["hit"] for r in result["candidate_outcome_rows"])==5
    assert sum(not r["hit"] for r in result["candidate_outcome_rows"])==5
    assert all(r["actual_stat"]==0 for r in result["candidate_outcome_rows"])
    assert len(result["immutable_packet_sha256"])==64
    assert result["historical_outcomes_persisted"] is False
    assert result["forward_calibration_eligible"] is False
    assert result["relabel_evidence_source_kind"] is False
    assert result["release_authorized"] is False
    assert result["can_execute"] is False

def test_packet_hash_is_order_independent_and_source_sensitive():
    p,s,o=fixture()
    assert build(p,s,o)["immutable_packet_sha256"]==build(p[::-1],s,o)["immutable_packet_sha256"]
    for prop in o.values():
        if prop["event_id"]=="2026_06_CAR_PHI":
            prop["source_sha256"]="c"*64
    assert build(p,s,o)["immutable_packet_sha256"]!=build()["immutable_packet_sha256"]

@pytest.mark.parametrize("change,code",[
    ("wrong_player","IMMUTABLE_PREDICTION_IDENTITY_INVALID"),
    ("post_kickoff","IMMUTABLE_PREGAME_TIME_INVALID"),
    ("forward_relabel","NOT_HISTORICAL_DO_NOT_RECLASSIFY_FORWARD"),
    ("changed_line","EXACT_HALF_TD_LINE_REQUIRED"),
    ("changed_prob","PAIRED_CALIBRATION_INCONSISTENT"),
    ("mismatched_snapshot","PREGAME_ROLE_IDENTITY_CONFLICT"),
    ("forged_candidate","OFFICIAL_PROPOSAL_RECEIPT_IDENTITY_CONFLICT"),
    ("wrong_official_stat","OFFICIAL_SCORER_RESULT_CONTRADICTORY"),
    ("missing_play","OFFICIAL_SOURCE_OR_PARTICIPATION_INVALID"),
    ("late_snapshot","FROZEN_SNAPSHOT_AFTER_KICKOFF"),
    ("wrong_gamebook_url","OFFICIAL_SOURCE_OR_PARTICIPATION_INVALID"),
])
def test_contract_fails_closed(change,code):
    p,s,o=fixture()
    target=p[0]
    sid=target["source_snapshot_id"]
    ident=target["prediction_id"]
    if change=="wrong_player":target["player"]="Another Player"
    if change=="post_kickoff":target["locked_at"]=(START+timedelta(minutes=5)).isoformat()
    if change=="forward_relabel":target["evidence_source_kind"]="IMMUTABLE_PREGAME_SETTLED"
    if change=="changed_line":target["line"]="1.5"
    if change=="changed_prob":target["calibrated_probability"]="0.87"
    if change=="mismatched_snapshot":s[sid]["role_status"]["player"]="Wrong Player"
    if change=="forged_candidate":o[ident]["prediction_id"]="another-id"
    if change=="wrong_official_stat":o[ident]["actual_stat"]=2
    if change=="missing_play":o[ident]["participation_evidence"]="ROSTER_ONLY"
    if change=="late_snapshot":s[sid]["captured_at"]=(START+timedelta(minutes=1)).isoformat()
    if change=="wrong_gamebook_url":o[ident]["source_url"]="https://bad.invalid/extra"
    with pytest.raises(HistoricalGradeHold,match=code):
        build(p,s,o)

def test_duplicate_or_missing_direction_is_never_backfilled():
    p,s,o=fixture()
    with pytest.raises(HistoricalGradeHold,match="EXACT_TEN_PREDICTIONS_REQUIRED"):
        build(p[:-1],s,o)
    p[0]=copy.deepcopy(p[1])
    with pytest.raises(HistoricalGradeHold,match="PINNED_PREDICTION_ID_INVALID"):
        build(p,s,o)

def test_conflicting_pdf_versions_hold_whole_batch():
    p,s,o=fixture()
    first=next(x for x in p if x["player"]=="Bryce Young")
    o[first["prediction_id"]]["source_sha256"]="e"*64
    with pytest.raises(HistoricalGradeHold,match="PAIR_GAMEBOOK_VERSION_CONFLICT"):
        build(p,s,o)

def test_candidate_missing_from_one_receipt_is_not_synthetic_loss():
    p,s,o=fixture()
    del o[next(iter(o))]
    with pytest.raises(HistoricalGradeHold,match="EXACT_TEN_OFFICIAL_PROPOSALS_REQUIRED"):
        build(p,s,o)
