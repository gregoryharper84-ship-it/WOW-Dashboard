"""Existing resident supervisor's authenticated log-artifact -> durable Reporter adapter.

- No new dispatcher, issue creator, lease authority, repair agent or approver.
- Unique engineering backlog ticket_id is the only deduplication key.
- The source is a separately attested successful protected-main GitHub run;
  source log strings are never used as commands.
- No probability, betting, trading, model-promotion, merge or deploy APIs.
"""
from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import re
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

from v17.log_reporter_handoff import (
    REPO, WORKFLOW, ARTIFACT, SHA, plan_reporter_handoff,
)

ARTIFACT_FILE = "wow-v17-log-intake.json"
MAX_ARCHIVE = 1_000_000
MAX_REPORT = 250_000


class ReporterHold(RuntimeError):
    """Only fixed error code, never upstream payload or exception text."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _download_archive(client: Any, artifact_id: int) -> bytes:
    """GitHub auth goes ONLY to api.github.com; signed download has no auth."""
    opener = build_opener(_NoRedirect)
    request = Request(
        f"https://api.github.com/repos/{REPO}/actions/artifacts/{artifact_id}/zip",
        headers={"Authorization": "Bearer " + client.token,
                 "Accept": "application/vnd.github+json"},
    )
    try:
        opener.open(request, timeout=10)
    except HTTPError as error:
        if error.code != 302:
            raise ReporterHold("REPORTER_GITHUB_ARTIFACT_UNAVAILABLE") from None
        signed_url = error.headers.get("Location", "")
    except (URLError, OSError):
        raise ReporterHold("REPORTER_GITHUB_ARTIFACT_UNAVAILABLE") from None
    else:
        raise ReporterHold("REPORTER_SIGNED_REDIRECT_MISSING")
    target = urlparse(signed_url)
    if (target.scheme != "https" or target.username or target.password or target.port
            or not (target.hostname or "").endswith(
                (".blob.core.windows.net", ".githubusercontent.com"))
            or target.fragment):
        raise ReporterHold("REPORTER_ARTIFACT_REDIRECT_UNTRUSTED")
    try:
        with opener.open(Request(signed_url), timeout=15) as response:
            raw = response.read(MAX_ARCHIVE + 1)
    except (HTTPError, URLError, OSError):
        raise ReporterHold("REPORTER_ARTIFACT_DOWNLOAD_FAILED") from None
    if len(raw) > MAX_ARCHIVE:
        raise ReporterHold("REPORTER_ARTIFACT_OVERSIZE")
    return raw


def _read_redacted_json(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_ARCHIVE:
        raise ReporterHold("REPORTER_ARTIFACT_OVERSIZE")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if (len(members) != 1 or members[0].filename != ARTIFACT_FILE
                    or members[0].file_size > MAX_REPORT
                    or members[0].is_dir()):
                raise ReporterHold("REPORTER_ARTIFACT_CONTENT_INVALID")
            document = json.loads(archive.read(members[0]))
        if not isinstance(document, dict):
            raise ReporterHold("REPORTER_ARTIFACT_CONTENT_INVALID")
        return document
    except ReporterHold:
        raise
    except (ValueError, zipfile.BadZipFile, UnicodeDecodeError, RuntimeError, OSError):
        raise ReporterHold("REPORTER_ARTIFACT_CONTENT_INVALID") from None


def read_latest_authenticated_report(
    client: Any, *, main_sha: str,
    downloader: Callable[[Any, int], bytes] = _download_archive,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Require exact-run identity AND exactly one nonexpired named artifact."""
    if not isinstance(main_sha, str) or not SHA.fullmatch(main_sha):
        raise ReporterHold("REPORTER_MAIN_SHA_INVALID")
    result = client.get(f"actions/workflows/{WORKFLOW}/runs?branch=main&status=completed&per_page=1")
    runs = result.get("workflow_runs") if isinstance(result, dict) else None
    if not isinstance(runs, list) or len(runs) != 1:
        raise ReporterHold("REPORTER_WORKFLOW_INVENTORY_UNVERIFIED")
    run = runs[0]
    if not isinstance(run, Mapping):
        raise ReporterHold("REPORTER_WORKFLOW_INVENTORY_UNVERIFIED")
    run_id, attempt = run.get("id"), run.get("run_attempt")
    if (run.get("path") != f".github/workflows/{WORKFLOW}"
            or run.get("head_branch") != "main" or run.get("head_sha") != main_sha
            or run.get("status") != "completed" or run.get("conclusion") != "success"
            or run.get("event") not in {"schedule", "workflow_dispatch"}
            or type(run_id) is not int or run_id <= 0
            or type(attempt) is not int or attempt < 1):
        raise ReporterHold("REPORTER_RUN_PROVENANCE_UNVERIFIED")
    artifacts = client.get(f"actions/runs/{run_id}/artifacts?per_page=100")
    rows = artifacts.get("artifacts") if isinstance(artifacts, dict) else None
    if (not isinstance(rows, list) or type(artifacts.get("total_count")) is not int
            or artifacts["total_count"] != len(rows)
            or len(rows) > 100):
        raise ReporterHold("REPORTER_ARTIFACT_INVENTORY_INCOMPLETE")
    matches = [row for row in rows if isinstance(row, dict) and
               row.get("name") == ARTIFACT and row.get("expired") is False]
    if len(matches) != 1 or type(matches[0].get("id")) is not int:
        raise ReporterHold("REPORTER_ARTIFACT_NOT_UNIQUE")
    report = _read_redacted_json(downloader(client, matches[0]["id"]))
    provenance = {"repository": REPO, "workflow": WORKFLOW,
                  "artifact_name": ARTIFACT, "branch": "main",
                  "head_sha": main_sha, "status": "completed",
                  "conclusion": "success", "run_id": run_id}
    return report, provenance


def _rows(response: Any) -> list[dict[str, Any]]:
    raw = getattr(response, "data", None)
    if not isinstance(raw, list):
        raise ReporterHold("REPORTER_DURABLE_READBACK_INVALID")
    return raw


def persist_candidates(db: Any, report: Mapping[str, Any], *,
                       provenance: Mapping[str, Any],
                       main_sha: str) -> dict[str, Any]:
    """Dedupe with existing unique backlog ticket_id; *never* grant dispatch.

    Caller must be the already-running resident supervisor with service-role
    DB identity, never a GitHub untrusted workflow or outside chat.
    """
    preflight = plan_reporter_handoff(
        report, provenance=provenance, protected_main_sha=main_sha,
        known_fingerprints={}, approved_issue_ids=frozenset(), issue_states={},
    )
    if preflight["status"] == "HELD":
        raise ReporterHold(str(preflight["reason"]))
    if preflight["status"] == "NO_ACTION":
        return {"status": "NO_ACTION", "created": 0, "known": 0,
                "dispatch_authorized": False, "can_execute": False}
    created = known = 0
    for candidate in report["findings"]:
        fingerprint = candidate["fingerprint"]
        ticket = "WOW-LOG-" + fingerprint
        if not re.fullmatch(r"WOW-LOG-[0-9a-f]{64}", ticket):
            raise ReporterHold("REPORTER_FINGERPRINT_INVALID")
        # Read-first is advisory; the UNIQUE constraint + ignore_duplicates
        # performs the actual dedupe under simultaneous supervisor replicas.
        existing = _rows(db.table("wow_engineering_backlog").select(
            "ticket_id,status,queue_status,terminal_state").eq(
                "ticket_id", ticket).limit(1).execute())
        if len(existing) > 1:
            raise ReporterHold("REPORTER_DURABLE_IDENTITY_DUPLICATE")
        if existing:
            if existing[0].get("status") == "CLOSED":
                raise ReporterHold("REPORTER_RECURRENCE_REENTRY_REQUIRED")
            known += 1
            continue
        item = {
            "ticket_id": ticket,
            "title": ("WOW log signal " + candidate["typed_failure"])[:160],
            "class": "B", "status": "OPEN", "source": "engineering",
            "priority": candidate["severity"], "queue_status": "PARKED",
            "parked_reason": "REPORTER_CANONICAL_ISSUE_ADMISSION_REQUIRED",
            "created_by": "wow_resident_reporter",
            "description": (
                f"Source=DATADOG; service={candidate['service']}; "
                f"boundary={candidate['failure_boundary']}; "
                f"code={candidate['typed_failure']}; count={candidate['count']}; "
                f"first_seen={candidate['first_seen_utc']}; "
                f"last_seen={candidate['last_seen_utc']}; "
                f"authenticated_run={provenance['run_id']}; "
                "candidate_only=true; can_execute=false"
            ),
            "parked_context": {"fingerprint": fingerprint,
                               "authenticated_run_id": provenance["run_id"],
                               "source_sha": main_sha,
                               "next_action": "CANONICAL_GITHUB_ISSUE_TRIAGE",
                               "can_execute": False},
        }
        db.table("wow_engineering_backlog").upsert(
            item, on_conflict="ticket_id", ignore_duplicates=True).execute()
        verified = _rows(db.table("wow_engineering_backlog").select(
            "ticket_id,status,queue_status,terminal_state").eq(
                "ticket_id", ticket).limit(1).execute())
        if len(verified) != 1 or verified[0].get("ticket_id") != ticket:
            raise ReporterHold("REPORTER_DURABLE_READBACK_INVALID")
        if verified[0].get("status") == "CLOSED":
            raise ReporterHold("REPORTER_RECURRENCE_REENTRY_REQUIRED")
        created += 1
    return {"status": "REPORTER_DURABLE_CANDIDATES", "created": created,
            "known": known, "dispatch_authorized": False, "can_execute": False}
