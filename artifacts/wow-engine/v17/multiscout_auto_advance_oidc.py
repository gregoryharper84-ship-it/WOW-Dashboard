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
from contextlib import contextmanager
import json
import os
import sys
import time
from threading import Lock
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

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


# Production run 35996201182 sent two prop batches of 50/40 rows into the
# governed /score-pick-request route. Render completed those requests in roughly
# 155-209 seconds while the bridge transport timeout is 120 seconds, producing
# repeat-safe 499/502 retries and zero returned prop rows. Bound only the OIDC
# Nightly prop batches so the same governed scorer receives smaller requests;
# no sporting probability, hydration, routing, reconciliation, or terminal
# semantics change.
def _prop_batch_rows() -> int:
    raw = os.environ.get("WOW_AUTO_ADVANCE_PROP_BATCH_ROWS", "10")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 10
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
PROP_BATCH_ROWS = _prop_batch_rows()


def _async_handoff_enabled() -> bool:
    return str(os.environ.get("WOW_SCOUT_ASYNC_HANDOFF_ENABLED") or "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _get_json(origin: str, path: str, token: str, *, timeout: int = 30) -> dict[str, Any]:
    req = Request(
        f"{origin.rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
        except Exception:
            detail = {}
        return {"ok": False, "http_status": exc.code, "body": detail, "can_execute": False}
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"ok": False, "code": "SCOUT_ASYNC_STATUS_TRANSPORT_FAILED", "error_type": type(exc).__name__, "can_execute": False}
    return {"ok": True, "http_status": 200, "body": body, "can_execute": False}


def _execute_async_handoff(handoff: dict[str, Any], token: str, *, origin: str) -> dict[str, Any]:
    # The server accepts a compact raw-evidence projection and rebuilds its
    # deterministic plan; callers cannot self-assert RED_TEAM_PASSED.
    post = _refreshing_oidc_post(token)
    enqueue = post(origin, "/v17/scout-handoff-runs", token, handoff, timeout=120)
    body = enqueue.get("body") if isinstance(enqueue, dict) else None
    if not isinstance(body, dict) or body.get("can_execute") is not False:
        return {
            "schema_version": "wow.v17.scout-handoff-run.v1",
            "status": "BLOCKED_BACKEND_HANDOFF",
            "code": "SCOUT_ASYNC_ENQUEUE_FAILED",
            "source_run_id": handoff.get("run_id"),
            "research_run_id": handoff.get("research_run_id"),
            "enqueue_receipt": enqueue,
            "can_execute": False,
        }

    source_run_id = str(body.get("source_run_id") or handoff.get("run_id") or "")
    deadline = time.monotonic() + max(5, min(int(os.environ.get("WOW_SCOUT_ASYNC_RECONCILE_SECONDS", "45")), 120))
    latest: dict[str, Any] = {
        "schema_version": "wow.v17.scout-handoff-run.v1",
        "status": "IN_PROGRESS",
        "source_run_id": source_run_id,
        "research_run_id": body.get("research_run_id") or handoff.get("research_run_id"),
        "can_execute": False,
    }
    while time.monotonic() < deadline and source_run_id:
        status = _get_json(
            origin,
            f"/v17/scout-handoff-runs/{quote(source_run_id, safe='')}",
            token,
            timeout=30,
        )
        status_body = status.get("body") if isinstance(status, dict) else None
        if isinstance(status_body, dict) and status_body.get("can_execute") is False:
            latest = dict(status_body)
            latest["enqueue_receipt"] = body
            if latest.get("status") == "COMPLETE":
                detailed = _get_json(
                    origin,
                    f"/v17/scout-handoff-runs/{quote(source_run_id, safe='')}?include_receipts=true",
                    token,
                    timeout=30,
                )
                detailed_body = detailed.get("body") if isinstance(detailed, dict) else None
                if isinstance(detailed_body, dict) and detailed_body.get("can_execute") is False:
                    latest = dict(detailed_body)
                    latest["enqueue_receipt"] = body
                break
        time.sleep(2)

    latest.setdefault("schema_version", "wow.v17.scout-handoff-run.v1")
    latest.setdefault("source_run_id", source_run_id)
    latest.setdefault("research_run_id", handoff.get("research_run_id"))
    latest["source_acquisition_status"] = handoff.get("status")
    latest["can_execute"] = False
    return latest
OIDC_MAX_IN_FLIGHT = _oidc_max_in_flight()
_TRANSIENT_HTTP_STATUSES = frozenset({502, 503, 504})
_TRANSIENT_RETRY_BACKOFF_SECONDS = (1.0, 3.0)


@contextmanager
def _oidc_batch_bounds() -> Iterator[None]:
    """Scope Nightly-only batch overrides to one OIDC CLI execution.

    Importing this wrapper must not mutate the ordinary core bridge. The OIDC
    workflow runs in its own process, so temporarily overriding the existing
    chunk constants is sufficient and keeps the static Action-key path unchanged.
    """
    original_team_event_rows = auto_advance.MAX_TEAM_EVENT_ROWS
    original_prop_rows = auto_advance.MAX_PROP_ROWS
    auto_advance.MAX_TEAM_EVENT_ROWS = TEAM_EVENT_BATCH_ROWS
    auto_advance.MAX_PROP_ROWS = PROP_BATCH_ROWS
    try:
        yield
    finally:
        auto_advance.MAX_TEAM_EVENT_ROWS = original_team_event_rows
        auto_advance.MAX_PROP_ROWS = original_prop_rows


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




_ASYNC_CANDIDATE_KEYS = frozenset({
    "official_event_id",
    "sport_key",
    "commence_time",
    "home_team",
    "away_team",
    "market_evidence_source_blockers",
    "contradictory_evidence",
    "red_team_flags",
    "research_priority",
    "priority",
    "research_interest",
    "can_execute",
})
_ASYNC_EVIDENCE_KEYS = frozenset({
    "source_class",
    "market_last_update",
    "bookmaker_last_update",
    "captured_at",
    "bookmaker",
    "source_provider",
    "bookmaker_title",
})
_ASYNC_PROP_EVIDENCE_KEYS = _ASYNC_EVIDENCE_KEYS | frozenset({
    "description",
    "outcome_name",
    "point",
    "market_key",
})


def _compact_async_evidence(value: Any, *, prop: bool) -> Any:
    """Project active evidence to fields consumed by mapping/research gates.

    Preserve the original dict/list/scalar shape so malformed evidence follows
    the same typed mapping/research path on the governed server.
    """
    keys = _ASYNC_PROP_EVIDENCE_KEYS if prop else _ASYNC_EVIDENCE_KEYS
    if isinstance(value, dict):
        return {key: value[key] for key in keys if key in value}
    if isinstance(value, list):
        return [
            {key: row[key] for key in keys if key in row}
            if isinstance(row, dict)
            else row
            for row in value
        ]
    return value


def _compact_async_candidate(candidate: Any, *, prop: bool) -> Any:
    """Keep only source fields the governed server actually consumes."""
    if not isinstance(candidate, dict):
        return candidate
    compact = {
        key: candidate[key]
        for key in _ASYNC_CANDIDATE_KEYS
        if key in candidate
    }
    if "market_evidence" in candidate:
        compact["market_evidence"] = _compact_async_evidence(
            candidate.get("market_evidence"),
            prop=prop,
        )
    return compact


def _compact_async_handoff(handoff: dict[str, Any]) -> dict[str, Any]:
    """Build a server-rebuildable evidence projection for durable enqueue.

    The server still calls build_handoff_plan(), build_dispatch(), and the
    deterministic research/red-team gates. This removes transport-only bulk
    (historical/stale evidence corpora, briefs, game scripts, etc.) that those
    gates never read; it does not create or assert RED_TEAM_PASSED locally.
    """
    model_handoff = (
        handoff.get("model_handoff")
        if isinstance(handoff.get("model_handoff"), dict)
        else {}
    )
    raw_props = (
        model_handoff.get("prop_candidates")
        if isinstance(model_handoff.get("prop_candidates"), list)
        else []
    )
    raw_teams = (
        model_handoff.get("team_event_candidates")
        if isinstance(model_handoff.get("team_event_candidates"), list)
        else []
    )
    governance = (
        dict(handoff.get("governance"))
        if isinstance(handoff.get("governance"), dict)
        else {}
    )
    return {
        "run_id": handoff.get("run_id"),
        "research_run_id": handoff.get("research_run_id"),
        "generated_at": handoff.get("generated_at"),
        "status": handoff.get("status"),
        "model_handoff_ready": handoff.get("model_handoff_ready"),
        "source_acquisition_status": handoff.get("source_acquisition_status"),
        "governance": governance,
        "model_handoff": {
            "prop_candidates": [
                _compact_async_candidate(candidate, prop=True)
                for candidate in raw_props
            ],
            "team_event_candidates": [
                _compact_async_candidate(candidate, prop=False)
                for candidate in raw_teams
            ],
        },
        "can_execute": False,
    }


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

    if _async_handoff_enabled():
        compact_handoff = _compact_async_handoff(dispatch_handoff)
        receipt = _execute_async_handoff(compact_handoff, token, origin=ACTION_ORIGIN)
    elif static_token:
        receipt = execute_auto_advance(dispatch_handoff, token=token, origin=ACTION_ORIGIN, progress_fn=progress_fn)
    else:
        with _oidc_batch_bounds():
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
    status = str(receipt.get("status") or "")
    if _async_handoff_enabled():
        # Durable enqueue is success even if the bounded reconciliation read is
        # still IN_PROGRESS; publication remains fail-closed until the ledger is COMPLETE.
        return 0 if status in {"COMPLETE", "IN_PROGRESS"} else 3
    return 0 if status.startswith("AUTO_ADVANCE_COMPLETE") else 3


if __name__ == "__main__":
    raise SystemExit(main())
