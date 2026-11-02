from copy import deepcopy
from datetime import datetime, timedelta, timezone
import pytest
from v17.llp_team_intelligence_bundle import build_llp_team_intelligence_bundle, CAN_EXECUTE

START = datetime(2026, 11, 5, 23, 0, tzinfo=timezone.utc)
EVENT = dict(sport="NHL", official_event_id="nhl-123", home_team="BOS", away_team="NYR", event_start_time_utc=START.isoformat())


def receipt(family="OUTRIGHT_WINNER"):
    base = dict(EVENT, market_family=family, side="BOS", source_receipt_id="test-frozen-id",
                probability_publishable=True, rank_eligible=True, can_execute=False,
                controlling_engine_identity="LLP_TEAM_BETTING_ENGINE",
                terminal_authority="V17_TERMINAL_REDUCER", model_class="CERTIFIED_FITTED",
                model_version="test-artifact", calibration_version="test-calibration",
                model_timestamp=(START-timedelta(minutes=1)).isoformat(),
                source_snapshot_timestamp=(START-timedelta(minutes=2)).isoformat(),
                calibrated_probability_lower_bound=.52,
                calibrated_probability_upper_bound=.68)
    if family == "OUTRIGHT_WINNER":
        base.update(calibrated_probability=.6)
    elif family == "POINT_SPREAD":
        base.update(side="HOME",exact_line=1.5,settlement_basis="NHL_OT_SO_FINAL",
                    p_cover=.6,p_push=0.0,p_not_cover=.4)
    else:
        base.update(side="OVER",exact_line=5.5,settlement_basis="NHL_OT_SO_FINAL",
                    p_over=.6,p_under=.4,p_push=0.0)
    return base


def test_multi_market_receipts_stay_distinct_and_no_probabilities_blend():
    out = build_llp_team_intelligence_bundle(event=EVENT, receipts=[receipt(x) for x in ("OUTRIGHT_WINNER","POINT_SPREAD","GAME_TOTAL")])
    assert [x["status"] for x in out["markets"]] == ["GOVERNED_RECEIPT_VISIBLE"] * 3
    assert out["probabilities_blended"] is False and out["terminal_decision_generated"] is False
    assert out["can_execute"] is False and CAN_EXECUTE is False


@pytest.mark.parametrize("mutation,code",[
    ({"official_event_id":"other"},"CROSS_MARKET_EVENT_IDENTITY_MISMATCH"),
    ({"model_class":"RESEARCH_ONLY"},"MODEL_CLASS_NOT_CERTIFIED_FITTED"),
    ({"rank_eligible":False},"UPSTREAM_UNCERTIFIED_OR_HELD"),
    ({"terminal_authority":"LLP_TEAM_BETTING_ENGINE"},"TERMINAL_RECEIPT_NOT_VERIFIED"),
    ({"model_timestamp":(START+timedelta(minutes=1)).isoformat()},"PREGAME_MODEL_FRESHNESS_UNPROVEN"),
    ({"can_execute":True},"EXECUTION_INVARIANT_UNPROVEN"),
    ({"p_over":.9},"TOTAL_DISTRIBUTION_INVALID"),
    ({"exact_line":None},"TOTAL_EXACT_LINE_OR_SETTLEMENT_MISSING"),
])
def test_bad_totals_fail_closed_without_numeric_leak(mutation,code):
    raw=receipt("GAME_TOTAL")
    raw.update(mutation)
    out=build_llp_team_intelligence_bundle(event=EVENT,receipts=[raw])["markets"][0]
    assert code in out["reason_codes"]
    assert out["status"]=="HELD_EVIDENCE_ONLY" and "p_over" not in out


def test_unavailable_winner_cannot_promote_as_upset():
    raw=receipt()
    raw["model_class"]="PROVISIONAL"
    out=build_llp_team_intelligence_bundle(event=EVENT,receipts=[raw])
    assert out["governed_winner_receipt_id"] is None
    assert "calibrated_probability" not in out["markets"][0]


def test_same_market_different_lines_remain_separate():
    a,b=receipt("GAME_TOTAL"),receipt("GAME_TOTAL")
    b["exact_line"]=6.5
    out=build_llp_team_intelligence_bundle(event=EVENT, receipts=[a,b])
    assert len(out["markets"]) == 2


def test_duplicate_market_thesis_rejected():
    r=receipt("GAME_TOTAL")
    with pytest.raises(ValueError,match="DUPLICATE"):
        build_llp_team_intelligence_bundle(event=EVENT,receipts=[r,deepcopy(r)])
