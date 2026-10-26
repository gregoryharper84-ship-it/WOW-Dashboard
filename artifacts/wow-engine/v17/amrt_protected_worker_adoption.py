"""Protected existing-worker invocation for AMRT's five specialist adapters.

No new supervisor, lease, approval, model or wager execution. This helper must
be called only from a protected-main checkout after the existing mutation lease
fence. It reads current GitHub issue and heartbeat evidence via GitHub Actions
token and persists attributed held/routed decisions using the canonical ledger.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable
from urllib.request import Request, urlopen

from v17.amrt_authenticated_worker_handoff import persist_authenticated_specialist_handoff
from v17.amrt_specialist_agents import AGENT_TO_EXISTING_ROLE
from v17.persistent_worker_safety import Lease

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
CAN_EXECUTE = False
TERMINAL_AUTHORITY = "V17_TERMINAL_REDUCER"
MAX_COMMENT_PAGES = 10
ALLOWED_GITHUB_PATH = re.compile(r"^/repos/gregoryharper84-ship-it/WOW-Dashboard/(?:issues/[0-9]+|issues/1138/comments\?per_page=100&page=[0-9]+)$")
HEARTBEAT_FIELD = re.compile(r"^- ([a-z_]+): ([^\r\n]+)$", re.MULTILINE)


def _must(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError(code)


def github_read(path: str, token: str) -> Any:
    """Read only explicitly allowlisted authenticated repository records."""
    _must(bool(ALLOWED_GITHUB_PATH.fullmatch(path)) and bool(token), "AMRT_GITHUB_READ_NOT_AUTHORIZED")
    request = Request(
        "https://api.github.com" + path,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "WOW-V17-AMRT-protected-worker-readonly",
        },
    )
    with urlopen(request, timeout=10) as response:
        return json.load(response)


def _canonical_heartbeat(
    read: Callable[[str], Any], *, lease_group: str,
) -> dict[str, str]:
    marker = "<!-- WOW_DUAL_STREAM_HEARTBEAT:" + lease_group + " -->"
    matches: list[str] = []
    for page in range(1, MAX_COMMENT_PAGES + 1):
        rows = read(f"/repos/{REPO}/issues/1138/comments?per_page=100&page={page}")
        _must(isinstance(rows, list), "AMRT_HEARTBEAT_SOURCE_INVALID")
        for item in rows:
            if not isinstance(item, dict):
                raise ValueError("AMRT_HEARTBEAT_SOURCE_INVALID")
            body = item.get("body")
            if isinstance(body, str) and marker in body:
                matches.append(body)
        if len(rows) < 100:
            break
    else:
        raise ValueError("AMRT_HEARTBEAT_INVENTORY_NOT_EXHAUSTIVE")
    _must(len(matches) == 1, "AMRT_HEARTBEAT_NOT_UNIQUE_OR_MISSING")
    fields = dict(HEARTBEAT_FIELD.findall(matches[0]))
    _must(all(key in fields for key in (
        "lease_epoch", "worker_id", "lease_expires_at", "lease_group",
        "worker_mode", "current_restoration_target", "can_execute",
        "terminal_authority",
    )), "AMRT_HEARTBEAT_INCOMPLETE")
    return fields


def admitted_context(
    *,
    manifest: dict[str, Any],
    issue_number: str,
    issue: dict[str, Any],
    heartbeat: dict[str, str],
    lease_data: dict[str, Any],
    worker_id: str,
    lease_group: str,
) -> tuple[dict[str, Any], Lease]:
    """Bind issue accountability and canonical single-writer evidence."""
    _must(issue_number.isdigit() and int(issue_number) > 0,
          "AMRT_CANONICAL_INCIDENT_INVALID")
    entries = [r for r in manifest.get("restoration", [])
               if str(r.get("issue_number")) == issue_number]
    _must(len(entries) == 1, "AMRT_MANIFEST_ENTRY_MISSING_OR_DUPLICATE")
    config = entries[0]
    _must(config.get("amrt_owned") is True
          and config.get("can_execute") is False
          and config.get("change_class") == "B"
          and config.get("severity") == "P0"
          and config.get("execution_lane") == "RAPID"
          and config.get("lease_group") == lease_group,
          "AMRT_INCIDENT_NOT_ADMITTED")
    canonical_url = f"https://github.com/{REPO}/issues/{issue_number}"
    _must(issue.get("html_url") == canonical_url
          and issue.get("number") == int(issue_number)
          and issue.get("state") == "open"
          and not issue.get("pull_request"),
          "AMRT_CURRENT_ISSUE_INVALID")
    owner_id = config.get("amrt_accountable_owner")
    assignees = issue.get("assignees")
    _must(isinstance(owner_id, str) and bool(owner_id.strip())
          and isinstance(assignees, list)
          and any(isinstance(a, dict) and a.get("login") == owner_id for a in assignees),
          "AMRT_ACCOUNTABLE_ISSUE_OWNER_UNVERIFIED")
    _must(lease_data.get("can_execute") is False
          and lease_data.get("terminal_authority") == TERMINAL_AUTHORITY
          and lease_data.get("worker_mode") == "RUNNING",
          "AMRT_LEASE_SAFETY_INVARIANT_INVALID")
    _must(lease_data.get("work_item_id") == issue_number
          and lease_data.get("worker_id") == worker_id
          and lease_data.get("lease_group") == lease_group
          and heartbeat["worker_id"] == worker_id
          and heartbeat["current_restoration_target"] == issue_number
          and heartbeat["lease_group"] == lease_group
          and heartbeat["worker_mode"] == "RUNNING"
          and heartbeat["can_execute"].lower() == "false"
          and heartbeat["terminal_authority"] == TERMINAL_AUTHORITY
          and heartbeat["lease_expires_at"] == lease_data.get("expires_at")
          and str(heartbeat["lease_epoch"]) == str(lease_data.get("lease_epoch")),
          "AMRT_ACTIVE_LEASE_HEARTBEAT_MISMATCH")
    try:
        epoch = int(heartbeat["lease_epoch"])
    except (TypeError, ValueError) as exc:
        raise ValueError("AMRT_LEASE_EPOCH_INVALID") from exc
    _must(epoch >= 0 and type(lease_data.get("lease_epoch")) is int,
          "AMRT_LEASE_EPOCH_INVALID")
    lease = Lease(issue_number, worker_id, epoch, heartbeat["lease_expires_at"])
    incident = {
        "incident_id": issue_number,
        "issue_ref": canonical_url,
        "canonical_owner_source_ref": canonical_url,
        "owner_id": owner_id,
        "lease_owner": worker_id,
        "lease_authority": "EXISTING_AUTHENTICATED_ENGINEERING_WORKER",
        "amrt_owned": True,
        "can_execute": False,
        "severity": "P0",
        "change_class": "B",
        "stage": "INVESTIGATION",
        # No invented reproduction, model, participant or proof packet.
        # Missing fields correctly produce role-specific HELD receipts.
    }
    return incident, lease


def run_worker_handoffs(
    *, read: Callable[[str], Any], manifest: dict[str, Any],
    lease_data: dict[str, Any], issue_number: str, lease_group: str,
    worker_id: str, head_sha: str, supabase_url: str,
    service_credential: str, now: datetime,
) -> list[dict[str, Any]]:
    """Invoke only admitted source specialists, never simulate model evidence."""
    issue = read(f"/repos/{REPO}/issues/{issue_number}")
    heartbeat = _canonical_heartbeat(read, lease_group=lease_group)
    incident, lease = admitted_context(
        manifest=manifest, issue_number=issue_number, issue=issue,
        heartbeat=heartbeat, lease_data=lease_data,
        worker_id=worker_id, lease_group=lease_group,
    )
    _must(bool(supabase_url) and bool(service_credential),
          "AMRT_CANONICAL_LEDGER_CREDENTIAL_UNAVAILABLE")
    results: list[dict[str, Any]] = []
    for specialist in AGENT_TO_EXISTING_ROLE:
        # Each write must validate the live heartbeat, so a stolen/released
        # writer cannot continue through the remaining roles.
        live = _canonical_heartbeat(read, lease_group=lease_group)
        _must(live == heartbeat, "AMRT_LEASE_CHANGED_DURING_HANDOFF")
        receipt = persist_authenticated_specialist_handoff(
            incident=incident, specialist=specialist,
            lease=lease, verified_latest_epoch=int(heartbeat["lease_epoch"]),
            verified_worker_id=worker_id,
            verified_accountable_owner_id=incident["owner_id"],
            verified_owner_evidence_ref=incident["issue_ref"],
            observed_at=now, head_sha=head_sha,
            supabase_url=supabase_url, service_credential=service_credential,
            # Empty map is truthful: this specialist reports the missing
            # discovery stage rather than a fabricated producer PASS.
            producer_stages={},
        )
        results.append({
            "specialist": specialist,
            "receipt_id": receipt["receipt_id"],
            "disposition": receipt["disposition"],
        })
    return results


def main() -> int:
    try:
        _must(os.getenv("GITHUB_REPOSITORY") == REPO, "AMRT_REPOSITORY_IDENTITY_INVALID")
        _must(os.getenv("WOW_CAN_EXECUTE", "false").lower() == "false"
              and os.getenv("WOW_DRY_RUN_ONLY", "true").lower() == "true",
              "AMRT_EXECUTION_GOVERNANCE_INVALID")
        issue_number = os.getenv("AMRT_SELECTED_INCIDENT", "").strip()
        # A non-AMRT incident follows the existing engineering workflow,
        # not a second specialist dispatch path.
        if not issue_number or issue_number == "NONE":
            print("AMRT_NOT_ADMITTED_NO_OP")
            return 0
        lease_group = os.environ["AMRT_LEASE_GROUP"]
        worker_id = os.environ["AMRT_WORKER_ID"]
        sha = os.environ["GITHUB_SHA"]
        base = Path(__file__).resolve().parent
        manifest = json.loads((base / "engineering_dispatch_manifest.json").read_text())
        entries = [item for item in manifest.get("restoration", [])
                   if str(item.get("issue_number")) == issue_number]
        if len(entries) == 1 and entries[0].get("amrt_owned") is not True:
            print("AMRT_NOT_ADMITTED_NO_OP")
            return 0
        lease_data = json.loads(
            Path("artifacts/wow-engine/v17/.agent-handoff/lease.json").read_text()
        )
        token = os.environ["GH_TOKEN"]
        results = run_worker_handoffs(
            read=lambda path: github_read(path, token),
            manifest=manifest, lease_data=lease_data,
            issue_number=issue_number, lease_group=lease_group,
            worker_id=worker_id, head_sha=sha,
            supabase_url=os.getenv("SUPABASE_URL", ""),
            service_credential=os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
            now=datetime.now(timezone.utc),
        )
        for result in results:
            print("AMRT_SPECIALIST_PERSISTED:" + result["specialist"]
                  + ":" + result["disposition"])
        return 0
    except Exception as exc:
        # Never include raw remote exception messages, credentials or URLs.
        print("AMRT_HANDOFF_HELD:" + type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
