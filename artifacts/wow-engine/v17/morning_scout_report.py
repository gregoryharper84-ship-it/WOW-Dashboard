"""Fail-closed governed morning Scout publication."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Any

_ALLOWED_RECEIPT_STATUSES = {"AUTO_ADVANCE_COMPLETE", "AUTO_ADVANCE_COMPLETE_WITH_BLOCKERS"}

def build_report(handoff: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    qualified: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
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
            if status == "COMPLETED" and publication_gate_open:
                qualified.append(item)
            else:
                if status == "COMPLETED" and not publication_gate_open:
                    item["publication_blocker"] = "SPECIALIST_RECEIPT_NOT_RECONCILED"
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
        "specialist_code": receipt.get("code") or "NOT_RUN",
        "specialist_reconciliation_pass": reconciliation_pass,
        "publication_gate_open": publication_gate_open,
        "governed_picks": qualified,
        "blocked_or_unresolved": blocked,
        "research_only_candidates": handoff.get("model_handoff", {}),
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
