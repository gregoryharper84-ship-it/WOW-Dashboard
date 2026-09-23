"""OIDC-authenticated CLI wrapper for Multi-Scout governed auto-advance.

The ordinary `multiscout_auto_advance.py` Action-key contract remains intact.
This wrapper is only for the exact GitHub Actions Multi-Scout workflow: it mints
short-lived OIDC tokens and passes them to the existing bridge. No model,
probability, calibration, routing, reconciliation, or execution semantics are
duplicated here.

GitHub OIDC credentials are intentionally short-lived. A large Scout slate can
outlive one token, so the wrapper refreshes OIDC only after the backend returns
401 and retries that exact request once. Static WOW_ACTION_API_KEY callers keep
the existing non-refreshing path. There is no static-secret fallback for the
OIDC workflow.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from threading import Lock
from pathlib import Path
from typing import Any, Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v17 import multiscout_auto_advance as auto_advance
from v17.github_actions_oidc_client import GitHubOIDCMintError, mint_github_actions_oidc
from v17.multiscout_auto_advance import ACTION_ORIGIN, _post_json, execute_auto_advance


# Production run 35137881356 sent 64 team/event objective rows in one request
# and hit the bridge's exact 120-second transport timeout. Bound only the OIDC
# Nightly path here so large Scout slates are split into smaller canonical
# /score-team-event-request calls without changing scoring, routing, calibration,
# reconciliation, terminal authority, or execution semantics.
def _team_event_batch_rows() -> int:
    raw = os.environ.get("WOW_AUTO_ADVANCE_TEAM_EVENT_BATCH_ROWS", "8")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 8
    return max(1, min(value, 50))


# Live run 35874396361 proved that eight concurrent long-running governed scorer
# requests can overload the single production web-service path: seven requests
# returned HTTP 200 while five sibling batches timed out. Keep the ordinary
# Action-key bridge unchanged and bound only the OIDC Nightly caller so Scout
# cannot create its own avoidable thundering herd against the governed backend.
def _oidc_max_in_flight() -> int:
    raw = os.environ.get("WOW_AUTO_ADVANCE_OIDC_IN_FLIGHT", "2")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 2
    return max(1, min(value, 4))


TEAM_EVENT_BATCH_ROWS = _team_event_batch_rows()
OIDC_MAX_IN_FLIGHT = _oidc_max_in_flight()
auto_advance.MAX_TEAM_EVENT_ROWS = TEAM_EVENT_BATCH_ROWS
_TRANSIENT_HTTP_STATUSES = frozenset({502, 503, 504})
_TRANSIENT_RETRY_BACKOFF_SECONDS = (1.0, 3.0)


def _repeat_safe_transient_retry(path: str, payload: dict[str, Any]) -> bool:
    """Return whether the exact request can be safely repeated after transport loss.

    Prop scoring has a durable resume contract keyed by the exact request_id and
    stable row_key values, so a lost response can be retried without duplicating
    completed rows. MLB team/event governance upserts by frozen
    research/event/settlement identity, so that lane is also repeat-safe.

    Other team/event sports can write immutable prediction rows and therefore
    remain fail-closed until they expose an equivalent idempotency contract.
    """
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        return False

    if path == "/score-pick-request":
        request_id = str(payload.get("request_id") or "").strip()
        return bool(request_id) and all(
            isinstance(row, dict) and bool(str(row.get("row_key") or "").strip())
            for row in rows
        )

    if path != "/score-team-event-request":
        return False
    return all(
        isinstance(row, dict)
        and str(row.get("sport") or "").strip().upper() == "MLB"
        and str(row.get("league") or "").strip().upper() == "MLB"
        and bool(str(row.get("research_run_id") or "").strip())
        and bool(str(row.get("event_key") or "").strip())
        for row in rows
    )


def _transient_receipt(receipt: dict[str, Any]) -> bool:
    if receipt.get("http_status") in _TRANSIENT_HTTP_STATUSES:
        return True
    body = receipt.get("body") if isinstance(receipt.get("body"), dict) else {}
    return (
        receipt.get("http_status") is None
        and body.get("code") == "AUTO_ADVANCE_TRANSPORT_FAILURE"
    )


def _refreshing_oidc_post(initial_token: str) -> Callable[..., dict[str, Any]]:
    """Refresh OIDC on 401 and retry only repeat-safe transient requests."""
    state = {"token": initial_token}
    refresh_lock = Lock()

    def authorized_post(origin: str, path: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
        with refresh_lock:
            request_token = state["token"]
        receipt = _post_json(origin, path, request_token, payload, timeout=timeout)
        if receipt.get("http_status") != 401:
            return receipt
        try:
            with refresh_lock:
                if state["token"] == request_token:
                    state["token"] = mint_github_actions_oidc(force=True)
                refreshed_token = state["token"]
        except GitHubOIDCMintError as exc:
            return {
                "ok": False,
                "http_status": 401,
                "body": {"code": str(exc)},
                "can_execute": False,
            }
        return _post_json(origin, path, refreshed_token, payload, timeout=timeout)

    def post(origin: str, path: str, _token: str, payload: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
        receipt = authorized_post(origin, path, payload, timeout)
        first_status = receipt.get("http_status")
        first_body = receipt.get("body") if isinstance(receipt.get("body"), dict) else {}
        retry_count = 0
        if _repeat_safe_transient_retry(path, payload):
            for delay in _TRANSIENT_RETRY_BACKOFF_SECONDS:
                if not _transient_receipt(receipt):
                    break
                time.sleep(delay)
                retry_count += 1
                receipt = authorized_post(origin, path, payload, timeout)
        if retry_count:
            receipt = dict(receipt)
            receipt["transient_retry_count"] = retry_count
            receipt["initial_http_status"] = first_status
            receipt["initial_transport_code"] = first_body.get("code")
            receipt["repeat_safe_retry"] = True
            receipt["can_execute"] = False
        return receipt

    return post


def _dispatchable_handoff(handoff: dict[str, Any]) -> dict[str, Any]:
    """Preserve degraded source telemetry while allowing valid rows to route.

    `multiscout_auto_advance` intentionally accepts only DISCOVERY_COMPLETE.
    A row-preserved Scout run can be discovery-complete even when one market
    evidence source is degraded. Normalize only that exact state for dispatch;
    global acquisition/auth failures and handoffs with no valid rows remain
    fail-closed.
    """
    if (
        handoff.get("status") == "DISCOVERY_COMPLETE_WITH_SOURCE_BLOCKERS"
        and handoff.get("model_handoff_ready") is True
    ):
        normalized = dict(handoff)
        normalized["source_acquisition_status"] = handoff.get("status")
        normalized["status"] = "DISCOVERY_COMPLETE"
        return normalized
    return handoff



def _progress_writer(output: Path, handoff: dict[str, Any]) -> Callable[..., None]:
    """Persist cancellation-visible batch progress without granting scoring authority."""
    state: dict[str, Any] = {
        "schema_version": "wow.v17.multiscout.auto-advance-progress.v1",
        "status": "AUTO_ADVANCE_IN_PROGRESS",
        "source_run_id": handoff.get("run_id"),
        "research_run_id": handoff.get("research_run_id"),
        "completed_batches": [],
        "can_execute": False,
    }
    write_lock = Lock()

    def write(**event: Any) -> None:
        compact = {
            "lane": event.get("lane"),
            "batch_key": event.get("batch_key"),
            "batch_index": event.get("batch_index"),
            "completed_batches": event.get("completed_batches"),
            "total_batches": event.get("total_batches"),
            "ok": bool((event.get("receipt") or {}).get("ok")),
            "http_status": (event.get("receipt") or {}).get("http_status"),
            "can_execute": False,
        }
        with write_lock:
            state["completed_batches"].append(compact)
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_suffix(output.suffix + ".tmp")
            temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            temporary.replace(output)

    return write


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    handoff = json.loads(Path(args.input).read_text(encoding="utf-8"))
    output = Path(args.output)
    progress_fn = _progress_writer(output, handoff)
    dispatch_handoff = _dispatchable_handoff(handoff)
    static_token = os.environ.get("WOW_ACTION_API_KEY")
    token = static_token
    if not token:
        try:
            token = mint_github_actions_oidc(force=True)
        except GitHubOIDCMintError as exc:
            receipt = {
                "schema_version": "wow.v17.multiscout.auto-advance.v1",
                "status": "BLOCKED_BACKEND_HANDOFF",
                "code": str(exc),
                "source_run_id": handoff.get("run_id"),
                "research_run_id": handoff.get("research_run_id"),
                "source_acquisition_status": handoff.get("status"),
                "can_execute": False,
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps({"status": receipt["status"], "code": receipt["code"], "can_execute": False}))
            return 3

    if static_token:
        receipt = execute_auto_advance(dispatch_handoff, token=token, origin=ACTION_ORIGIN, progress_fn=progress_fn)
    else:
        receipt = execute_auto_advance(
            dispatch_handoff,
            token=token,
            origin=ACTION_ORIGIN,
            post_fn=_refreshing_oidc_post(token),
            max_in_flight=OIDC_MAX_IN_FLIGHT,
            progress_fn=progress_fn,
        )
    if dispatch_handoff is not handoff:
        receipt["source_acquisition_status"] = handoff.get("status")
        receipt["source_blocker_count"] = len(handoff.get("source_blockers") or [])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": receipt.get("status"),
        "code": receipt.get("code"),
        "source_run_id": receipt.get("source_run_id"),
        "research_run_id": receipt.get("research_run_id"),
        "source_acquisition_status": receipt.get("source_acquisition_status"),
        "can_execute": False,
    }))
    return 0 if str(receipt.get("status") or "").startswith("AUTO_ADVANCE_COMPLETE") else 3


if __name__ == "__main__":
    raise SystemExit(main())
