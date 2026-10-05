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


def _durable_receipt(*, complete=True, reconciled=True):
    return {
        "schema_version": "wow.v17.scout-handoff-run.v1",
        "status": "COMPLETE" if complete else "IN_PROGRESS",
        "source_run_id": "r1",
        "research_run_id": "rr1",
        "candidate_jobs": 2,
        "model_evaluated": 1,
        "handoff_blocked": 1 if complete else 0,
        "state_counts": {"V17_QUALIFIED": 1, "HANDOFF_BLOCKED": 1} if complete else {"SPECIALIST_PROCESSING": 1},
        "reconciliation_pass": reconciled,
        "untracked_rows": 0 if reconciled else 1,
        "jobs": [
            {
                "candidate_id": "cand-good",
                "target_lane": "WOW_PROP_LANE",
                "current_state": "V17_QUALIFIED",
                "terminal": True,
                "specialist_receipt": {
                    "result": {
                        "can_execute": False,
                        "rows": [{
                            "row_key": "durable-good",
                            "terminal_status": "COMPLETED",
                            "calibrated_lower_bound": 0.71,
                            "probability_publishable": True,
                            "rank_eligible": True,
                            "card_admission_eligible": True,
                            "can_execute": False,
                        }],
                    },
                    "can_execute": False,
                },
                "can_execute": False,
            },
            {
                "candidate_id": "cand-blocked",
                "target_lane": "LLP_TEAM_BETTING_ENGINE",
                "current_state": "HANDOFF_BLOCKED" if complete else "SPECIALIST_PROCESSING",
                "terminal": complete,
                "last_error_code": "TEAM_EVENT_IDENTITY_INCOMPLETE" if complete else None,
                "can_execute": False,
            },
        ],
        "can_execute": False,
    }


def test_durable_ledger_is_the_morning_report_reconciliation_authority():
    report = build_report(_handoff(), _durable_receipt())
    assert report["durable_handoff"]["enabled"] is True
    assert report["specialist_reconciliation_pass"] is True
    assert report["publication_gate_open"] is True
    assert [row["row_key"] for row in report["governed_picks"]] == ["durable-good"]
    blocked = next(row for row in report["blocked_or_unresolved"] if row.get("candidate_id") == "cand-blocked")
    assert blocked["terminal_status"] == "HANDOFF_BLOCKED"
    assert blocked["code"] == "TEAM_EVENT_IDENTITY_INCOMPLETE"
    assert blocked["can_execute"] is False


def test_durable_ledger_pending_rows_fail_closed_for_publication():
    report = build_report(_handoff(), _durable_receipt(complete=False, reconciled=False))
    assert report["durable_handoff"]["enabled"] is True
    assert report["publication_gate_open"] is False
    assert report["governed_picks"] == []
    assert any(row.get("publication_blocker") == "SPECIALIST_HANDOFF_PENDING" for row in report["blocked_or_unresolved"])


def test_durable_completed_row_missing_card_admission_fails_closed():
    receipt = _durable_receipt()
    row = receipt["jobs"][0]["specialist_receipt"]["result"]["rows"][0]
    row.pop("card_admission_eligible")
    receipt["jobs"][0]["current_state"] = "MODEL_EVALUATED"
    report = build_report(_handoff(), receipt)
    assert report["governed_picks"] == []
    blocked = next(r for r in report["blocked_or_unresolved"] if r.get("row_key") == "durable-good")
    assert blocked["publication_blocker"] == "GOVERNED_PICK_ADMISSION_NOT_PROVEN"
    assert blocked["can_execute"] is False



def test_terminal_acceptance_wrapper_uses_resolved_durable_ledger():
    wrapped = {
        "schema_version": "wow.v17.scout-handoff-terminal-acceptance.v1",
        "status": "PASS",
        "resolved_run_summary": _durable_receipt(),
        "can_execute": False,
    }
    report = build_report(_handoff(), wrapped)
    assert report["durable_handoff"]["enabled"] is True
    assert report["publication_gate_open"] is True
    assert [row["row_key"] for row in report["governed_picks"]] == ["durable-good"]
