from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from v17.postmortem_engine import (
    PatchCandidateInput,
    PostmortemLegInput,
    PostmortemPositionInput,
    PostmortemRunInput,
    build_postmortem_payload,
    classify_margin,
    payout_diagnostics,
    signed_margin,
)


def leg(row_key, position, sport, participant, market, side, line, actual, result, official):
    selection = f"{participant} {side} {line} {market}" if line is not None else f"{participant} to win"
    return PostmortemLegInput(
        row_key=row_key,
        position_reference=position,
        sport=sport,
        participant=participant,
        market=market,
        side=side,
        selection=selection,
        exact_line=line,
        actual_stat=actual,
        official_result=official,
        settled_result=result,
        observed_path="SETTLED_SCREENSHOT",
        settlement_source="USER_SUPPLIED_SETTLED_SCREENSHOT",
    )


def build_0906():
    p1 = "PP-2026-09-06-CFB-3P-POWER"
    p2 = "PP-2026-09-06-MIXED-6P-POWER"
    p3 = "PP-2026-09-06-MLB-5P-FLEX"
    p4 = "PP-2026-09-06-MIXED-3P-FLEX"
    legs = [
        leg("washington-win",p1,"NCAAF","Washington Huskies","OUTRIGHT_WINNER","WIN",None,None,"WIN","24-10"),
        leg("ole-miss-win",p1,"NCAAF","Ole Miss Rebels","OUTRIGHT_WINNER","WIN",None,None,"WIN","settled win"),
        leg("notre-dame-win",p1,"NCAAF","Notre Dame Fighting Irish","OUTRIGHT_WINNER","WIN",None,None,"WIN","41-13"),
        leg("sonny-gray-k",p2,"MLB","Sonny Gray","PITCHER_STRIKEOUTS","MORE",3.5,7,"WIN","7"),
        leg("degrom-k",p2,"MLB","Jacob deGrom","PITCHER_STRIKEOUTS","MORE",4.5,7,"WIN","7"),
        leg("anthony-kay-k",p2,"MLB","Anthony Kay","PITCHER_STRIKEOUTS","MORE",2.5,3,"WIN","3"),
        leg("taj-bradley-k",p2,"MLB","Taj Bradley","PITCHER_STRIKEOUTS","MORE",5.5,6,"WIN","6"),
        leg("odegaard-passes",p2,"SOCCER","Martin Odegaard","PASSES_ATTEMPTED","MORE",45.5,54,"WIN","54"),
        leg("florida-win",p2,"NCAAF","Florida Gators","OUTRIGHT_WINNER","WIN",None,None,"WIN","66-21"),
        leg("lambert-1ip",p3,"MLB","Peter Lambert","FIRST_INNING_PITCHES_THROWN","MORE",12.5,14,"WIN","14"),
        leg("leahy-k",p3,"MLB","Kyle Leahy","PITCHER_STRIKEOUTS","MORE",1.5,6,"WIN","6"),
        leg("gordon-1ip",p3,"MLB","Tanner Gordon","FIRST_INNING_PITCHES_THROWN","MORE",13.5,14,"WIN","14"),
        PostmortemLegInput(
            row_key="cole-1ip",
            position_reference=p3,
            sport="MLB",
            participant="Gerrit Cole",
            market="FIRST_INNING_PITCHES_THROWN",
            side="LESS",
            selection="Gerrit Cole LESS 14.5 FIRST_INNING_PITCHES_THROWN",
            exact_line=14.5,
            actual_stat=23,
            official_result="23 first-inning pitches",
            settled_result="LOSS",
            observed_path="BF_EXTENSION_ZERO_OUTS_AFTER_TOP_THREE_POWER_BURST_TAIL",
            observed_bf=6,
            outs_after_top3=0,
            top_order_reach_events=3,
            settlement_source="USER_SUPPLIED_SETTLED_SCREENSHOT",
        ),
        leg("hudson-k",p3,"MLB","Bryan Hudson","PITCHER_STRIKEOUTS","MORE",0.5,2,"WIN","2"),
        leg("kalinskaya-fs",p4,"TENNIS","Anna Kalinskaya","FANTASY_SCORE","LESS",11.5,-5.5,"WIN","-5.5"),
        leg("alvarez-k",p4,"MLB","Andrew Alvarez","PITCHER_STRIKEOUTS","MORE",2.5,5,"WIN","5"),
        leg("wrobleski-k",p4,"MLB","Justin Wrobleski","PITCHER_STRIKEOUTS","MORE",2.5,6,"WIN","6"),
    ]
    positions = [
        PostmortemPositionInput(position_reference=p1,position_structure="3-Pick Power Play",underlying_market_count=3,entry_cost=58.50,gross_return=61.93,all_or_nothing=True),
        PostmortemPositionInput(position_reference=p2,position_structure="6-Pick Power Play",underlying_market_count=6,entry_cost=5.00,gross_return=28.75,all_or_nothing=True),
        PostmortemPositionInput(position_reference=p3,position_structure="5-Pick Flex Play",underlying_market_count=5,entry_cost=20.00,gross_return=20.00,all_or_nothing=False),
        PostmortemPositionInput(position_reference=p4,position_structure="3-Pick Flex Play",underlying_market_count=3,entry_cost=20.00,gross_return=40.00,all_or_nothing=False),
    ]
    patches = [
        PatchCandidateInput(
            patch_key="RETRO-0906-1IP-TAIL-ATTRIBUTION",
            scope="MLB 1IP",
            change_class="DIAGNOSTIC_ONLY",
            proposed_change="Persist observed BF>=5/BF>=6 and pregame tail state when immutable evidence exists.",
            preserve_targets=["1IP event-tree probability method"],
            regression_checks=["No global probability haircut", "No broader 1IP restriction"],
        ),
        PatchCandidateInput(
            patch_key="RETRO-0906-MARGIN-CALIBRATION",
            scope="Scalar props",
            change_class="DIAGNOSTIC_ONLY",
            proposed_change="Persist signed margin_to_line and margin class.",
            preserve_targets=["Current prop selection logic"],
            regression_checks=["Near-boundary wins remain wins"],
        ),
        PatchCandidateInput(
            patch_key="RETRO-0906-WINNER-PAYOUT-EFFICIENCY",
            scope="Winner card economics",
            change_class="TARGETED_ECONOMICS",
            proposed_change="Separate sporting success from payout break-even and capital efficiency.",
            preserve_targets=["Winner sporting-probability lane"],
            regression_checks=["Do not alter sporting probability from payout economics"],
        ),
    ]
    return PostmortemRunInput(
        research_run_id="wow-retro-2026-09-06-v17",
        slate_date="2026-09-06",
        source_type="USER_SUPPLIED_SETTLED_SLIP_SCREENSHOTS",
        preserve=["MLB K selections 8/8 observed", "Winner selections 4/4 observed", "Flex protected the Cole miss"],
        refine=["1IP tail attribution", "Margin-to-line diagnostics", "Winner payout efficiency"],
        regression_checks=["No global qualification-floor increase", "No universal probability haircut", "Do not suspend MLB K", "Do not make all 1IP stricter from one miss"],
        legs=legs,
        positions=positions,
        patch_candidates=patches,
    )


def test_signed_margin_directional():
    assert signed_margin("MORE", 2.5, 5) == 2.5
    assert signed_margin("LESS", 14.5, 23) == -8.5
    assert signed_margin("WIN", None, None) is None


def test_margin_class_preserves_close_wins():
    assert classify_margin(side="MORE", line=2.5, actual=3, settled_result="WIN") == "NEAR_BOUNDARY"
    assert classify_margin(side="LESS", line=14.5, actual=23, settled_result="LOSS") == "LARGE_MISS"
    assert classify_margin(side="WIN", line=None, actual=None, settled_result="WIN") == "BINARY_WINNER"


def test_power_break_even_is_diagnostic_not_sporting_probability():
    econ = payout_diagnostics(entry_cost=58.50, gross_return=61.93, all_or_nothing=True)
    assert econ["gross_multiplier"] == pytest.approx(61.93 / 58.50)
    assert econ["break_even_joint_probability"] == pytest.approx(58.50 / 61.93)
    flex = payout_diagnostics(entry_cost=20, gross_return=40, all_or_nothing=False)
    assert flex["break_even_joint_probability"] is None


def test_0906_retro_reconciles_and_excludes_probability_backfill():
    payload = build_postmortem_payload(build_0906())
    summary = payload["summary"]
    assert summary["legs"] == 17
    assert summary["wins"] == 16
    assert summary["losses"] == 1
    assert summary["leg_hit_rate"] == pytest.approx(16 / 17)
    assert summary["total_entry"] == pytest.approx(103.50)
    assert summary["total_return"] == pytest.approx(150.68)
    assert summary["net_profit"] == pytest.approx(47.18)
    assert summary["roi"] == pytest.approx(47.18 / 103.50)
    assert summary["profitable_positions"] == 3
    assert summary["non_losing_positions"] == 4
    assert summary["matched_pregame_count"] == 0
    assert summary["retrospective_count"] == 17
    assert summary["calibration_eligible_count"] == 0
    assert summary["probability_backfill_allowed"] is False
    assert summary["global_tightening_applied"] is False
    for row in payload["legs"]:
        assert row["capture_timing"] == "POST_EVENT_RETROACTIVE"
        assert row["calibration_eligible"] is False
        assert row["excluded_from_calibration"] is True
        assert row["raw_probability"] is None
        assert row["calibrated_probability"] is None
        assert row["lower_bound"] is None
    cole = next(row for row in payload["legs"] if row["row_key"] == "cole-1ip")
    assert cole["margin_to_line"] == pytest.approx(-8.5)
    assert cole["margin_class"] == "LARGE_MISS"
    assert cole["bf_ge_5"] is True
    assert cole["bf_ge_6"] is True
    assert cole["outs_after_top3"] == 0
    assert cole["pregame_bf_ge_6_probability"] is None
    assert cole["tail_diagnostics"]["pregame_bf_ge_6_status"] == "NOT_AVAILABLE_DO_NOT_BACKFILL"


def test_0906_lane_summary_preserves_strong_k_lane():
    payload = build_postmortem_payload(build_0906())
    ks = payload["summary"]["lane_summary"]["MLB:PITCHER_STRIKEOUTS"]
    assert ks == {"rows": 8, "wins": 8, "losses": 0, "pushes": 0, "voids": 0}
    ip1 = payload["summary"]["lane_summary"]["MLB:FIRST_INNING_PITCHES_THROWN"]
    assert ip1["wins"] == 2
    assert ip1["losses"] == 1


def test_position_diagnostics_keep_sporting_and_economics_separate():
    payload = build_postmortem_payload(build_0906())
    cfb = next(pos for pos in payload["positions"] if pos["position_reference"] == "PP-2026-09-06-CFB-3P-POWER")
    assert cfb["all_legs_hit"] is True
    assert cfb["leg_hit_rate"] == 1.0
    assert cfb["break_even_joint_probability"] == pytest.approx(58.50 / 61.93)
    assert cfb["economics_status"] == "DIAGNOSTIC_ONLY"
    assert cfb["capital_share"] == pytest.approx(58.50 / 103.50)
    assert cfb["profit_contribution_share"] == pytest.approx(3.43 / 47.18)


def test_broad_tightening_never_auto_implements():
    batch = build_0906()
    batch.patch_candidates.append(
        PatchCandidateInput(
            patch_key="broad-test",
            scope="all lanes",
            change_class="TARGETED_MODEL",
            proposed_change="broad change",
            broad_tightening=True,
            probability_change_allowed=True,
        )
    )
    payload = build_postmortem_payload(batch)
    patch = next(p for p in payload["patches"] if p["patch_key"] == "broad-test")
    assert patch["status"] == "REQUIRES_EXPLICIT_GOVERNANCE"


def test_linked_recommendation_requires_exact_event_id():
    with pytest.raises(ValidationError):
        PostmortemLegInput(
            row_key="x", position_reference="p", sport="MLB", participant="A",
            market="PITCHER_STRIKEOUTS", side="MORE", selection="A MORE 2.5 PITCHER_STRIKEOUTS",
            exact_line=2.5, actual_stat=3, settled_result="WIN", settlement_source="official",
            recommendation_record_id=str(uuid.uuid4()),
        )


def test_position_leg_reconciliation_is_hard_failure():
    batch = build_0906()
    batch.positions[0].underlying_market_count = 4
    with pytest.raises(ValidationError):
        PostmortemRunInput(**batch.model_dump())
