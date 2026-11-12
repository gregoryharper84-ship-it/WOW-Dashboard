"""Deployment-identity dedupe for the post-deploy acceptance orchestrator.

Repeat acceptance may be skipped only when a previous *successful* orchestrator
run observed the identical deployment: same target SHA, same live web deploy ID
and same live worker deploy ID. A redeploy of the same SHA produces new deploy
IDs and therefore always runs every lane. Any missing, expired, malformed or
unreadable evidence fails closed to ``already_accepted=false`` (run everything).

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
WORKFLOW_FILE = "wow-v17-post-deploy-verification-orchestrator.yml"
MAX_PRIOR_RUNS = 20


def deployment_key(receipt: Any) -> tuple[str, str, str] | None:
    """Return (sha, web_deploy_id, worker_deploy_id) or None if not a full observation."""
    if not isinstance(receipt, dict) or receipt.get("status") != OBSERVED:
        return None
    parts = tuple(str(receipt.get(k) or "") for k in ("target_sha", "web_deploy_id", "worker_deploy_id"))
    return parts if all(parts) else None


def already_accepted(current: Any, prior_receipts: Iterable[Any]) -> bool:
    key = deployment_key(current)
    if key is None:
        return False
    return any(deployment_key(prior) == key for prior in prior_receipts)


def _gh(args: list[str]) -> bytes:
    return subprocess.run(["gh", "api", *args], check=True, capture_output=True, timeout=60).stdout


def _artifact_readback(repo: str, artifact: dict[str, Any], *, expected_filename: str) -> Any:
    """Read one exact artifact; failure of provenance/readability fails dedupe closed."""
    if not isinstance(artifact, dict) or artifact.get("expired"):
        raise ValueError("POST_DEPLOY_DEDUPE_ARTIFACT_EXPIRED")
    artifact_id = artifact.get("id")
    if type(artifact_id) is not int or artifact_id <= 0:
        raise ValueError("POST_DEPLOY_DEDUPE_ARTIFACT_ID_INVALID")
    blob = _gh([f"repos/{repo}/actions/artifacts/{artifact_id}/zip"])
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        matching = [name for name in zf.namelist() if name == expected_filename]
        if len(matching) != 1:
            raise ValueError("POST_DEPLOY_DEDUPE_RECEIPT_MISSING")
        return json.loads(zf.read(matching[0]))


def _prior_receipts(repo: str, sha: str, current_run_id: str) -> list[Any]:
    runs = json.loads(_gh([
        f"repos/{repo}/actions/workflows/{WORKFLOW_FILE}/runs"
        f"?head_sha={sha}&status=completed&per_page=50",
    ]))
    runs_to_check = [r for r in runs.get("workflow_runs", [])
                     if r.get("conclusion") == "success"
                     and str(r.get("id")) != current_run_id][:MAX_PRIOR_RUNS]
    receipts: list[Any] = []
    for run in runs_to_check:
        run_id = str(run.get("id") or "")
        attempt = str(run.get("run_attempt") or "")
        if not run_id.isdigit() or not attempt.isdigit() or int(attempt) < 1:
            continue
        listing = json.loads(_gh([f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100"]))
        artifacts = listing.get("artifacts", [])
        if not isinstance(artifacts, list):
            continue
        expected_initial = f"wow-v17-post-deploy-readback-{run_id}-{attempt}"
        expected_final = f"wow-v17-post-deploy-final-readback-{run_id}-{attempt}"
        by_name: dict[str, list[dict[str, Any]]] = {}
        for artifact in artifacts:
            if isinstance(artifact, dict):
                by_name.setdefault(str(artifact.get("name") or ""), []).append(artifact)
        # A green optional observer is NOT evidence all canaries ran.
        # The final readback is written only after the lane matrix passes.
        if len(by_name.get(expected_initial, [])) != 1 or len(by_name.get(expected_final, [])) != 1:
            continue
        try:
            initial = _artifact_readback(
                repo, by_name[expected_initial][0],
                expected_filename="wow-v17-post-deploy-readback.json",
            )
            final = _artifact_readback(
                repo, by_name[expected_final][0],
                expected_filename="wow-v17-post-deploy-final-readback.json",
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile):
            # Unverifiable prior acceptance is not proof to skip new lanes.
            continue
        key = deployment_key(initial)
        if key is not None and deployment_key(final) == key:
            receipts.append(initial)
    return receipts


def main() -> int:
    result = False
    try:
        if os.environ.get("SOURCE_EVENT") == "workflow_run":
            current = json.loads(Path(os.environ["READBACK_RECEIPT"]).read_text(encoding="utf-8"))
            key = deployment_key(current)
            if key is not None:
                prior = _prior_receipts(os.environ["GITHUB_REPOSITORY"], key[0],
                                        os.environ.get("GITHUB_RUN_ID", ""))
                result = already_accepted(current, prior)
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
