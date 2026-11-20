"""Deployment-identity dedupe for the post-deploy acceptance orchestrator.

Repeat acceptance may be skipped only when a previous orchestrator run both:

1. observed the identical deployment (same target SHA, same live web deploy ID
   and same live worker deploy ID), and
2. recorded an authentic ``ACCEPTANCE_LANES_PASSED`` outcome receipt.

A soft-green checksPass isolation on optional canary steps must not count as
acceptance: without the authentic outcome artifact, dedupe fails closed to
``already_accepted=false`` so every lane repeats. A redeploy of the same SHA
produces new deploy IDs and therefore always runs every lane. Any missing,
expired, malformed or unreadable evidence fails closed.

This only decides whether expensive lanes repeat. It never approves anything:
``production_verified`` stays false and SIRT acceptance remains separate.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable

OBSERVED = "WEB_WORKER_LIVE_SHA_AND_HEALTH_OBSERVED"
ACCEPTANCE_PASSED = "ACCEPTANCE_LANES_PASSED"
WORKFLOW_FILE = "wow-v17-post-deploy-verification-orchestrator.yml"
MAX_PRIOR_RUNS = 20


def deployment_key(receipt: Any) -> tuple[str, str, str] | None:
    """Return (sha, web_deploy_id, worker_deploy_id) or None if not a full observation."""
    if not isinstance(receipt, dict) or receipt.get("status") != OBSERVED:
        return None
    parts = tuple(str(receipt.get(k) or "") for k in ("target_sha", "web_deploy_id", "worker_deploy_id"))
    return parts if all(parts) else None


def acceptance_passed(outcome: Any) -> bool:
    return isinstance(outcome, dict) and outcome.get("status") == ACCEPTANCE_PASSED


def already_accepted(
    current: Any,
    prior_receipts: Iterable[Any],
    prior_outcomes: Iterable[Any] | None = None,
) -> bool:
    """Return True only when a prior run observed this deployment *and* passed lanes."""
    key = deployment_key(current)
    if key is None:
        return False
    outcomes = list(prior_outcomes or [])
    # Legacy call sites may pass only readbacks; without outcomes, fail closed.
    if not outcomes:
        return False
    for readback, outcome in zip(prior_receipts, outcomes):
        if deployment_key(readback) == key and acceptance_passed(outcome):
            return True
    return False


def _gh(args: list[str]) -> bytes:
    return subprocess.run(["gh", "api", *args], check=True, capture_output=True, timeout=60).stdout


def _load_json_from_artifact_zip(blob: bytes, suffix: str) -> Any | None:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for name in zf.namelist():
            if name.endswith(suffix):
                return json.loads(zf.read(name))
    return None


def _prior_pairs(repo: str, sha: str, current_run_id: str) -> list[tuple[Any, Any]]:
    runs = json.loads(_gh([
        f"repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs"
        f"?head_sha={sha}&status=completed&per_page=50",
    ]))
    # Prefer successful conclusions, but still inspect completed runs: step
    # isolation can keep the workflow green while authentic acceptance failed.
    ids = [str(r["id"]) for r in runs.get("workflow_runs", [])
           if str(r.get("id")) != current_run_id][:MAX_PRIOR_RUNS]
    pairs: list[tuple[Any, Any]] = []
    for run_id in ids:
        listing = json.loads(_gh([f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100"]))
        readback = None
        outcome = None
        for art in listing.get("artifacts", []):
            if art.get("expired"):
                continue
            name = str(art.get("name", ""))
            blob = _gh([f"repos/{repo}/actions/artifacts/{art['id']}/zip"])
            if name.startswith(f"wow-v17-post-deploy-readback-{run_id}-"):
                readback = _load_json_from_artifact_zip(blob, "wow-v17-post-deploy-readback.json")
            elif name.startswith(f"wow-v17-post-deploy-acceptance-outcome-{run_id}-"):
                outcome = _load_json_from_artifact_zip(
                    blob, "wow-v17-post-deploy-acceptance-outcome.json"
                )
        if readback is not None and outcome is not None:
            pairs.append((readback, outcome))
    return pairs


def main() -> int:
    result = False
    try:
        if os.environ.get("SOURCE_EVENT") == "workflow_run":
            current = json.loads(Path(os.environ["READBACK_RECEIPT"]).read_text(encoding="utf-8"))
            key = deployment_key(current)
            if key is not None:
                pairs = _prior_pairs(
                    os.environ["GITHUB_REPOSITORY"],
                    key[0],
                    os.environ.get("GITHUB_RUN_ID", ""),
                )
                result = already_accepted(
                    current,
                    [pair[0] for pair in pairs],
                    [pair[1] for pair in pairs],
                )
    except Exception as exc:  # fail closed: repeat every lane
        print(f"::notice::DEDUPE_FAILED_CLOSED:{type(exc).__name__}", file=sys.stderr)
        result = False
    line = f"already_accepted={'true' if result else 'false'}"
    print(line)
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
