"""Pins the resident worker's Render source commit into the existing registry.

`wow-agent-worker` is a Render Python worker: it has no HTTP surface, so its
deployed commit could previously only be read from the Render API/dashboard.
This module removes that dependency. On Celery `worker_ready` the worker writes
its own ``RENDER_GIT_COMMIT`` into every enabled
``wow_agent_worker_registry`` row; the governed web service then compares that
last observed pin with its own via the authenticated ``/wow/deploy-identity``
route (``agent_runtime_api.py``).

Both sides are self-reported Render env values, so this proves *env* parity, not
independently loaded code. The pin is stamped at worker start and is not a
heartbeat: it says "the most recent worker start was at commit X", not "a
worker is live on X now". One atomic UPDATE covers every row, so the pinned set
is always uniform (last writer wins) — a mixed set cannot arise from this
writer.

Recording is fail-closed in the readback sense: a missing or malformed commit
is never invented and never overwrites a previously pinned value, so the
readback reports ``UNVERIFIED`` instead of a guess. A registry write failure
does not kill the worker (governed work must keep running), but it is logged
with a typed code and the readback stays ``UNVERIFIED``.

can_execute=false
DRY_RUN_ONLY_NO_LIVE_TRADING_NO_MARKET_ORDERS=true
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

PARITY_MATCH = "MATCH"
PARITY_MISMATCH = "MISMATCH"
PARITY_UNVERIFIED = "UNVERIFIED"

# Typed reasons surfaced by the readback; never free-form upstream text.
REASON_WEB_COMMIT_UNKNOWN = "WEB_RUNTIME_COMMIT_UNKNOWN"
REASON_WORKER_COMMIT_MISSING = "WORKER_RUNTIME_COMMIT_MISSING"
REASON_WORKER_RUNTIME_COMMIT_UNREADABLE = "WORKER_RUNTIME_COMMIT_UNREADABLE"


def _valid_sha(value: str | None) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 40 and all(character in "0123456789abcdef" for character in text)


def render_git_commit() -> str | None:
    """This process's Render source commit, or None when absent/malformed."""
    value = str(os.getenv("RENDER_GIT_COMMIT") or "").strip().lower()
    return value if _valid_sha(value) else None


def classify_deploy_parity(web_commit: str | None, worker_commits: list[str]) -> dict[str, Any]:
    """Compare the web commit with the commit pinned in the worker registry.

    Any gap is UNVERIFIED, never MATCH: no worker commit (or not exactly one,
    which this writer cannot produce) or an unknown web commit both mean
    parity was not established.
    """
    unique = sorted({str(commit).strip().lower() for commit in worker_commits or [] if _valid_sha(commit)})
    if len(unique) != 1:
        return {"parity": PARITY_UNVERIFIED, "reason": REASON_WORKER_COMMIT_MISSING}
    if not _valid_sha(web_commit):
        return {"parity": PARITY_UNVERIFIED, "reason": REASON_WEB_COMMIT_UNKNOWN}
    if unique[0] == str(web_commit).strip().lower():
        return {"parity": PARITY_MATCH, "reason": None}
    return {"parity": PARITY_MISMATCH, "reason": None}


def record_runtime_commit(client: Any, commit: str | None = None) -> None:
    """Pin this worker process's commit into every enabled registry row.

    Writes nothing when the commit is absent or malformed, so a missing Render
    value can never become a fabricated or blanked SHA.
    """
    from agent_runtime import repository

    pinned = str(commit if commit is not None else render_git_commit() or "").strip().lower()
    if not _valid_sha(pinned):
        print("WOW_WORKER_RUNTIME_COMMIT_UNVERIFIED reason=RENDER_GIT_COMMIT_MISSING_OR_MALFORMED")
        return
    repository.record_worker_runtime_commit(
        client, commit=pinned, recorded_at=datetime.now(timezone.utc).isoformat(),
    )
    print(f"WOW_WORKER_RUNTIME_COMMIT_RECORDED commit={pinned}")


def install_celery_worker_hooks() -> None:
    """Pin the commit once the resident Celery worker is ready."""
    from celery.signals import worker_ready

    @worker_ready.connect(weak=False)
    def _record_on_worker_ready(**_: object) -> None:
        try:
            import ledger

            client = ledger.get_client()
        except Exception as exc:  # missing config must not stop governed work
            print(f"WOW_WORKER_RUNTIME_COMMIT_RECORD_FAILED reason=CLIENT_UNAVAILABLE error={type(exc).__name__}")
            return
        try:
            record_runtime_commit(client)
        except Exception as exc:  # typed surface only; never log upstream text
            print(f"WOW_WORKER_RUNTIME_COMMIT_RECORD_FAILED reason=REGISTRY_WRITE_FAILED error={type(exc).__name__}")
