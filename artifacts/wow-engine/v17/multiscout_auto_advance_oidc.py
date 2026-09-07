"""OIDC-authenticated CLI wrapper for Multi-Scout governed auto-advance.

The ordinary `multiscout_auto_advance.py` Action-key contract remains intact.
This wrapper is only for the exact GitHub Actions Multi-Scout workflow: it mints
a short-lived OIDC token and passes that token to the existing bridge. No model,
probability, calibration, routing, reconciliation, or execution semantics are
duplicated here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# The governed post-merge workflow executes this file directly from
# artifacts/wow-engine. Preserve that invocation contract by adding only the
# package parent when Python has not established a package context. Without
# this bootstrap, direct execution fails before the canonical V17 handoff with
# ``ModuleNotFoundError: No module named 'v17'``.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v17.github_actions_oidc_client import GitHubOIDCMintError, mint_github_actions_oidc
from v17.multiscout_auto_advance import ACTION_ORIGIN, execute_auto_advance


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    handoff = json.loads(Path(args.input).read_text(encoding="utf-8"))
    token = os.environ.get("WOW_ACTION_API_KEY")
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
                "can_execute": False,
            }
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps({"status": receipt["status"], "code": receipt["code"], "can_execute": False}))
            return 3

    receipt = execute_auto_advance(handoff, token=token, origin=ACTION_ORIGIN)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": receipt.get("status"),
        "code": receipt.get("code"),
        "source_run_id": receipt.get("source_run_id"),
        "research_run_id": receipt.get("research_run_id"),
        "can_execute": False,
    }))
    return 0 if str(receipt.get("status") or "").startswith("AUTO_ADVANCE_COMPLETE") else 3


if __name__ == "__main__":
    raise SystemExit(main())
