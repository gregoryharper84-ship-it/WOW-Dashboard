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
from pathlib import Path
from typing import Any, Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v17.github_actions_oidc_client import GitHubOIDCMintError, mint_github_actions_oidc
from v17.multiscout_auto_advance import ACTION_ORIGIN, _post_json, execute_auto_advance


def _refreshing_oidc_post(initial_token: str) -> Callable[..., dict[str, Any]]:
    """Return a backend POST function that refreshes OIDC once on HTTP 401."""
    state = {"token": initial_token}

    def post(origin: str, path: str, _token: str, payload: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
        receipt = _post_json(origin, path, state["token"], payload, timeout=timeout)
        if receipt.get("http_status") != 401:
            return receipt
        try:
            state["token"] = mint_github_actions_oidc(force=True)
        except GitHubOIDCMintError as exc:
            return {
                "ok": False,
                "http_status": 401,
                "body": {"code": str(exc)},
                "can_execute": False,
            }
        return _post_json(origin, path, state["token"], payload, timeout=timeout)

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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    handoff = json.loads(Path(args.input).read_text(encoding="utf-8"))
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
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps({"status": receipt["status"], "code": receipt["code"], "can_execute": False}))
            return 3

    if static_token:
        receipt = execute_auto_advance(dispatch_handoff, token=token, origin=ACTION_ORIGIN)
    else:
        receipt = execute_auto_advance(
            dispatch_handoff,
            token=token,
            origin=ACTION_ORIGIN,
            post_fn=_refreshing_oidc_post(token),
        )
    if dispatch_handoff is not handoff:
        receipt["source_acquisition_status"] = handoff.get("status")
        receipt["source_blocker_count"] = len(handoff.get("source_blockers") or [])
    output = Path(args.output)
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
