"""Off-GitHub SIRT sentinel (Render cron). Class B, evidence-only, no execution authority.

GitHub's scheduler fires the 15-minute SIRT sentinel only about every 4.5 hours,
so it cannot satisfy a 90-minute dead-man window and shares GitHub's failure
domain with the PRs it watches. This job runs the same read-only assessment on
a Render cron, outside GitHub, and persists a liveness heartbeat the resident
auditor may accept as evidence that the SIRT watchdog function is alive.

Liveness is separate from verdict: the heartbeat advances only after evidence
was actually read and assessed. A BLOCKED verdict is recorded (not hidden) and
still advances liveness; unreadable evidence never advances it.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from v17.sirt_assurance import (
    TERMINAL_AUTHORITY,
    assess_sentinel,
    read_audit_rows,
    read_auditor_runtime,
)

EXTERNAL_SENTINEL_ID = "WOW_SIRT_EXTERNAL_SENTINEL"
RUNTIME_TABLE = "wow_engineering_auditor_runtime"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def write_heartbeat(
    url: str, key: str, payload: dict[str, Any], *, timeout_seconds: int = 8,
) -> None:
    """Upsert exactly one runtime row owned by this sentinel; credentials never logged."""
    base = url.rstrip("/")
    if not base.startswith("https://") or not key:
        raise ValueError("SIRT_MONITOR_CONFIGURATION_MISSING")
    if payload.get("auditor_id") != EXTERNAL_SENTINEL_ID:
        raise ValueError("SIRT_EXTERNAL_SENTINEL_IDENTITY_INVALID")
    request = Request(
        f"{base}/rest/v1/{RUNTIME_TABLE}?on_conflict={quote('auditor_id', safe='')}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "apikey": key,
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
            "User-Agent": "wow-sirt-external-sentinel",
        },
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        if response.status not in (200, 201, 204):
            raise RuntimeError("SIRT_EXTERNAL_HEARTBEAT_WRITE_REJECTED")


def run_once(
    url: str,
    key: str,
    *,
    now: datetime | None = None,
    reader_runtime: Callable[..., dict[str, Any]] = read_auditor_runtime,
    reader_rows: Callable[..., list[dict[str, Any]]] = read_audit_rows,
    writer: Callable[..., None] = write_heartbeat,
    instance_id: str = "render-cron",
) -> dict[str, Any]:
    observed = now or _utcnow()
    # Any read failure raises BEFORE the heartbeat is written: an unverifiable
    # evidence source can never be reported as a live sentinel.
    runtime = reader_runtime(url, key)
    work_items = reader_rows(url, key, kind="work_items")
    findings = reader_rows(url, key, kind="findings")
    # No GitHub call: worker_runs=None omits the GitHub-hosted worker check by
    # design; this observer must not depend on the failure domain it backs up.
    assessment = assess_sentinel(
        runtime=runtime, work_items=work_items, findings=findings,
        worker_runs=None, now=observed,
    )
    writer(url, key, {
        "auditor_id": EXTERNAL_SENTINEL_ID,
        "instance_id": instance_id,
        "status": "RUNNING",
        "last_heartbeat_at": observed.isoformat(),
        "last_error_code": str(assessment["status"]),
        "can_execute": False,
        "terminal_authority": TERMINAL_AUTHORITY,
        "updated_at": observed.isoformat(),
    })
    return assessment


def main() -> int:
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY", "")
    try:
        assessment = run_once(url, key)
    except (ValueError, RuntimeError, HTTPError, URLError, TimeoutError, OSError) as exc:
        # No URLs, credentials, or bodies in diagnostic output.
        print(json.dumps({
            "status": "SIRT_EXTERNAL_SENTINEL_NOT_RECORDED",
            "reason": "SIRT_MONITOR_CONFIGURATION_MISSING" if isinstance(exc, ValueError)
            else "SIRT_MONITOR_PROBE_UNAVAILABLE",
            "can_execute": False,
            "terminal_authority": TERMINAL_AUTHORITY,
        }, sort_keys=True))
        return 2
    print(json.dumps(assessment, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
