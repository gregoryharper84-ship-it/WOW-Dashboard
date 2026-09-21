from v17.morning_scout_report import build_report

def _handoff():
    return {
        "run_id":"r1","research_run_id":"rr1","status":"DISCOVERY_COMPLETE",
        "model_handoff":{
            "prop_candidates":[{"research_ceiling":"RESEARCH_INTEREST","probability_authority":False,"can_execute":False}],
            "team_event_candidates":[]
        },
        "can_execute":False,
    }

def _receipt(status="AUTO_ADVANCE_COMPLETE_WITH_BLOCKERS", reconciled=True):
    return {
        "status":status,"code":"TEST","can_execute":False,
        "reconciliation":{"response_reconciliation_pass":reconciled},
        "prop_receipts":[{"body":{"outcomes":[
            {"row_key":"c","terminal_status":"COMPLETED","calibrated_lower_bound":0.72,"probability_publishable":True,"rank_eligible":True,"card_admission_eligible":True},
            {"row_key":"h","terminal_status":"HELD"},
            {"row_key":"b","terminal_status":"BLOCKED"},
            {"row_key":"u","status":"UNRESOLVED"},
        ]}}],
        "team_event_receipts":[],
    }

def test_only_terminal_completed_rows_publish_when_receipt_reconciles():
    report=build_report(_handoff(), _receipt())
    assert [r["row_key"] for r in report["governed_picks"]] == ["c"]
    assert {r["row_key"] for r in report["blocked_or_unresolved"]} == {"h","b","u"}
    assert report["publication_gate_open"] is True
    assert report["can_execute"] is False
    assert all(r["can_execute"] is False for r in report["governed_picks"])

def test_completed_rows_do_not_publish_when_receipt_is_unreconciled():
    report=build_report(_handoff(), _receipt(status="BLOCKED_RECONCILIATION", reconciled=False))
    assert report["governed_picks"] == []
    assert {r["row_key"] for r in report["blocked_or_unresolved"]} == {"c","h","b","u"}
    completed=next(r for r in report["blocked_or_unresolved"] if r["row_key"]=="c")
    assert completed["publication_blocker"] == "SPECIALIST_RECEIPT_NOT_RECONCILED"
    assert report["publication_gate_open"] is False
    assert report["can_execute"] is False

def test_raw_scout_candidates_remain_research_only():
    report=build_report(_handoff(), _receipt())
    raw=report["research_only_candidates"]["prop_candidates"][0]
    assert raw["research_ceiling"] == "RESEARCH_INTEREST"
    assert raw["probability_authority"] is False
    assert raw["can_execute"] is False


def test_completed_but_rank_ineligible_is_not_published_as_pick():
    receipt=_receipt()
    row=receipt["prop_receipts"][0]["body"]["outcomes"][0]
    row["rank_eligible"]=False
    row["card_admission_eligible"]=False
    report=build_report(_handoff(), receipt)
    assert report["governed_picks"] == []
    blocked=next(r for r in report["blocked_or_unresolved"] if r["row_key"]=="c")
    assert blocked["publication_blocker"] == "GOVERNED_PICK_ADMISSION_NOT_PROVEN"
    assert blocked["can_execute"] is False
