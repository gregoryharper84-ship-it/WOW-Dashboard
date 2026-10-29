"""Offline private-package intake for the October DAFT research exports.

Never imports Discord directly. Copies source originals into a *private*
append-only research index outside the Git checkout. Historical source-display
times are not guessed into UTC timestamps or prospective eligibility.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import zipfile

from v17.daft_external_research import normalize_research_record


def _zip_rows(filename: Path, member: str) -> list[dict]:
    with zipfile.ZipFile(filename) as archive:
        data = json.loads(archive.read(member).decode("utf-8"))
    if isinstance(data, dict):
        data = data.get("entries", data.get("records", []))
    if not isinstance(data, list):
        raise ValueError(f"Expected list in {member}")
    return [row for row in data if isinstance(row, dict)]


def import_historical_packages(
    *,
    sharp_zip: str,
    vip_zip: str,
    captured_at: str,
    destination: str,
) -> dict:
    """Write a strict 0600 offline archive without public repository modifications."""
    output = Path(destination).expanduser().resolve()
    repo_root = Path(__file__).resolve().parents[3]
    if output == repo_root or repo_root in output.parents:
        raise ValueError("Refuse to store paid research within public repository")
    if output.exists():
        raise FileExistsError("Immutable private archive exists; choose new output filename")
    if not output.parent.is_dir():
        raise ValueError("Create a private parent directory before import")
    if output.parent.stat().st_mode & 0o077:
        raise ValueError("Private archive directory must be mode 0700")
    if datetime.fromisoformat(captured_at.replace("Z", "+00:00")).utcoffset() is None:
        raise ValueError("Capture timestamp requires an offset")
    mapping = [
        (sharp_zip, "alerts_research_only.json", "SHARP_ALERT"),
        (sharp_zip, "results_source_reported.json", "SHARP_RESULT"),
        (vip_zip, "source_index.json", "VIP_SOURCE_INDEX"),
        (vip_zip, "nba_reported_ledger.json", "VIP_RECAP"),
    ]
    archive_rows = []
    counts = {}
    for archive_name, member, kind in mapping:
        raw_rows = _zip_rows(Path(archive_name), member)
        counts[kind] = len(raw_rows)
        for raw in raw_rows:
            record_kind = kind
            if kind == "VIP_SOURCE_INDEX":
                channel = str(raw.get("channel") or "")
                if channel == "VIP Picks":
                    record_kind = "VIP_PICK"
                elif channel == "VIP Recaps":
                    record_kind = "VIP_RECAP"
                else:
                    continue
            message_id = raw.get("message_id") or raw.get("recap_message_id")
            url = raw.get("source_url") or raw.get("recap_url") or raw.get("url")
            if not message_id:
                continue
            original_label = raw.get("play") or raw.get("selection_as_reported")
            normalized = normalize_research_record({
                "kind": record_kind, "channel": raw.get("channel") or "VIP Recaps",
                "message_id": str(message_id), "message_url": url,
                "source": "DAFT",
                # These display times are not authoritative timezone-aware stamps.
                "published_at": None,
                "capture_attested": False,
                "alert_id": raw.get("alert_id"),
                "source_reported_result": raw.get("result_reported") or raw.get("outcome_status"),
                "stake_as_reported": raw.get("stake_units_reported"),
            }, capture_at=captured_at)
            archive_rows.append({
                "normalized": normalized,
                "original_label": original_label,
                "original_source_fields": raw,  # PRIVATE ONLY; never emit in public API
                "source_member": member,
            })
    # Historical alert/result mismatches and source-index gaps are audit
    # evidence, not grade outcomes. Preserve them inside the private archive.
    with zipfile.ZipFile(sharp_zip) as archive:
        pairing = json.loads(archive.read("alert_result_pairs.json"))
        summary = json.loads(archive.read("audit_summary.json"))
    if not isinstance(pairing, list) or not isinstance(summary, dict):
        raise ValueError("Invalid private sharp reconciliation package")
    if len(pairing) != summary.get("linked_pairs"):
        raise ValueError("Private linked-pair count mismatch")
    if (counts["SHARP_ALERT"] != summary.get("classification", {}).get("alert") or
            counts["SHARP_RESULT"] != summary.get("classification", {}).get("result")):
        raise ValueError("Private source counts disagree with audit summary")
    if sum(r.get("same_displayed_primary_target") is False for r in pairing) != summary.get(
            "different_displayed_target_pairs"):
        raise ValueError("Private primary/graded target mismatch count")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(output), flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as writer:
            json.dump({
                "schema": "WOW_DAFT_PRIVATE_HISTORICAL_V1",
                "can_execute": False, "counts": counts, "rows": archive_rows,
                "sharp_alert_result_pairs": pairing, "sharp_audit_summary": summary,
            }, writer, ensure_ascii=False)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return {
        "counts": counts, "total_imported": len(archive_rows),
        "sharp_linked_pairs": len(pairing),
        "sharp_primary_vs_graded_mismatches": summary["different_displayed_target_pairs"],
        "prospective_eligible": 0,
        "mode": "RETROSPECTIVE_PRIVATE_RESEARCH_ONLY",
        "can_execute": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import private DAFT research packages offline")
    parser.add_argument("--sharp-zip", required=True)
    parser.add_argument("--vip-zip", required=True)
    parser.add_argument("--capture-time", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = import_historical_packages(
        sharp_zip=args.sharp_zip, vip_zip=args.vip_zip,
        captured_at=args.capture_time, destination=args.output,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
