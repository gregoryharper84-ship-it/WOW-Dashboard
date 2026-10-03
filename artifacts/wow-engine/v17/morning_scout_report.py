"""Fail-closed governed morning Scout publication."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Any

_ALLOWED_RECEIPT_STATUSES = {"AUTO_ADVANCE_COMPLETE", "AUTO_ADVANCE_COMPLETE_WITH_BLOCKERS"}

def build_report(handoff: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    qualified: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    durable = str(receipt.get("schema_version") or "") == "wow.v17.scout-handoff-run.v1"

    if durable:
        specialist_status = str(receipt.get("status") or "NOT_RUN")
        reconciliation_pass = receipt.get("reconciliation_pass") is True
        publication_gate_open = specialist_status == "COMPLETE" and reconciliation_pass and receipt.get("can_execute") is False

        for job in receipt.get("jobs") or []:
            if not isinstance(job, dict):
                continue
            lane = str(job.get("target_lane") or "UNKNOWN")
            state = str(job.get("current_state") or "UNKNOWN")
            specialist = job.get("specialist_receipt") if isinstance(job.get("specialist_receipt"), dict) else {}
            result = specialist.get("result") if isinstance(specialist.get("result"), dict) else {}
            rows = result.get("outcomes") or result.get("rows") or []
            if not isinstance(rows, list):
                rows = []
            if rows:
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    status = str(row.get("terminal_status") or row.get("status") or "").upper()
                    item = {**row, "controlling_specialist_route": lane, "can_execute": False}
                    governed_admission = (
                        status == "COMPLETED"
                        and row.get("probability_publishable") is True
                        and row.get("rank_eligible") is True
                        and row.get("card_admission_eligible", True) is True
                    )
                    if governed_admission and publication_gate_open:
                        qualified.append(item)
                    else:
                        item["publication_blocker"] = (
                            "SPECIALIST_RECEIPT_NOT_RECONCILED"
                            if status == "COMPLETED" and not publication_gate_open
                            else "GOVERNED_PICK_ADMISSION_NOT_PROVEN"
                        )
                        blocked.append(item)
            elif state == "HANDOFF_BLOCKED":
                blocked.append({
                    "candidate_id": job.get("candidate_id"),
                    "controlling_specialist_route": lane,
                    "terminal_status": "HANDOFF_BLOCKED",
                    "code": job.get("last_error_code"),
                    "detail": job.get("last_error_detail"),
                    "can_execute": False,
                })
            elif state not in {"MODEL_EVALUATED", "V17_QUALIFIED"}:
                blocked.append({
                    "candidate_id": job.get("candidate_id"),
                    "controlling_specialist_route": lane,
                    "terminal_status": state,
                    "publication_blocker": "SPECIALIST_HANDOFF_PENDING",
                    "can_execute": False,
                })
    else:
        specialist_status = str(receipt.get("status") or "NOT_RUN")
        reconciliation_pass = receipt.get("reconciliation", {}).get("response_reconciliation_pass") is True
        publication_gate_open = (
            specialist_status in _ALLOWED_RECEIPT_STATUSES
            and reconciliation_pass
            and receipt.get("can_execute") is False
        )

        def rows_from(receipts):
            for call in receipts or []:
                body = (call or {}).get("body") or {}
                rows = body.get("outcomes") or body.get("rows") or []
                if isinstance(rows, list):
                    yield from (r for r in rows if isinstance(r, dict))

        for route, receipts in (
            ("WOW_PROP_LANE", receipt.get("prop_receipts")),
            ("LLP_TEAM_BETTING_ENGINE", receipt.get("team_event_receipts")),
        ):
            for row in rows_from(receipts):
                status = str(row.get("terminal_status") or row.get("status") or "").upper()
                item = {**row, "controlling_specialist_route": route, "can_execute": False}
                governed_admission = (
                    status == "COMPLETED"
                    and row.get("probability_publishable") is True
                    and row.get("rank_eligible") is True
                    and row.get("card_admission_eligible", True) is True
                )
                if governed_admission and publication_gate_open:
                    qualified.append(item)
                else:
                    if status == "COMPLETED" and not publication_gate_open:
                        item["publication_blocker"] = "SPECIALIST_RECEIPT_NOT_RECONCILED"
                    elif status == "COMPLETED" and not governed_admission:
                        item["publication_blocker"] = "GOVERNED_PICK_ADMISSION_NOT_PROVEN"
                    blocked.append(item)

    def lower_bound(row):
        for key in ("calibrated_lower_bound", "probability_lower_bound", "lower_bound"):
            value = row.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        return -1.0

    qualified.sort(key=lower_bound, reverse=True)
    report = {
        "schema_version": "wow.v17.morning-scout-picks.v1",
        "run_id": handoff.get("run_id"),
        "research_run_id": handoff.get("research_run_id"),
        "scout_status": handoff.get("status"),
        "specialist_status": specialist_status,
        "specialist_code": receipt.get("code") or ("DURABLE_HANDOFF_LEDGER" if durable else "NOT_RUN"),
        "specialist_reconciliation_pass": reconciliation_pass,
        "publication_gate_open": publication_gate_open,
        "governed_picks": qualified,
        "blocked_or_unresolved": blocked,
        "research_only_candidates": handoff.get("model_handoff", {}),
        "durable_handoff": {
            "enabled": durable,
            "candidate_jobs": receipt.get("candidate_jobs") if durable else None,
            "state_counts": receipt.get("state_counts") if durable else None,
            "untracked_rows": receipt.get("untracked_rows") if durable else None,
        },
        "governance": {
            "scout_discovery_only": True,
            "sportsbook_external_probability_is_evidence_only": True,
            "final_probability_requires_controlling_specialist": True,
            "ranking_metric": "calibrated_lower_bound",
            "can_execute": False,
        },
        "can_execute": False,
    }
    assert report["can_execute"] is False
    assert report["governance"]["can_execute"] is False
    assert all(row.get("can_execute") is False for row in qualified)
    assert all(str(row.get("terminal_status") or row.get("status") or "").upper() == "COMPLETED" for row in qualified)
    assert all(row.get("probability_publishable") is True for row in qualified)
    assert all(row.get("rank_eligible") is True for row in qualified)
    assert all(row.get("card_admission_eligible", True) is True for row in qualified)
    if not publication_gate_open:
        assert qualified == []
    return report

def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--handoff", required=True)
    p.add_argument("--receipt", required=False)
    p.add_argument("--output", required=True)
    a=p.parse_args()
    handoff=json.loads(Path(a.handoff).read_text())
    receipt=json.loads(Path(a.receipt).read_text()) if a.receipt and Path(a.receipt).exists() else {}
    report=build_report(handoff, receipt)
    Path(a.output).write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
    print(f"governed picks={len(report['governed_picks'])} blocked/unresolved={len(report['blocked_or_unresolved'])} publication_gate_open={report['publication_gate_open']} can_execute=false")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
