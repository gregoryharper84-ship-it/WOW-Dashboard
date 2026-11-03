"""Read-only Render production observation, never a release authority.

Called only by a protected-main workflow; it cannot deploy, merge, approve,
post a model forecast, or certify SIRT acceptance.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REPO = "gregoryharper84-ship-it/WOW-Dashboard"
DEPLOY_WORKFLOW = "wow-v17-render-production-deploy"
DEPLOY_PATH = ".github/workflows/wow-v17-render-production-deploy.yml"
SERVICE_ID = "srv-da7sa9gu01pc73brt80g"
PUBLIC_HEALTH = "https://wow-governed-probability-engine.onrender.com/health/live"
SHA = re.compile(r"^[0-9a-f]{40}$")


class ReadbackHold(ValueError):
    pass


def validate_source_run(run: dict[str, Any], *, target_sha: str) -> None:
    if (run.get("name") != DEPLOY_WORKFLOW or run.get("path") != DEPLOY_PATH
            or (run.get("repository") or {}).get("full_name") != REPO
            or (run.get("head_repository") or {}).get("full_name") != REPO
            or run.get("head_branch") != "main"
            or run.get("head_sha") != target_sha
            or run.get("status") != "completed"
            or run.get("conclusion") != "success"
            or run.get("event") not in {"workflow_run", "workflow_dispatch", "push"}):
        raise ReadbackHold("DEPLOY_SOURCE_WORKFLOW_NOT_AUTHENTIC_EXACT_MAIN_SUCCESS")


def classify_readback(*, target_sha: str, main_sha: str,
                      deploys: list[dict[str, Any]], health_ok: bool) -> dict[str, Any]:
    if not SHA.fullmatch(target_sha or "") or not SHA.fullmatch(main_sha or ""):
        raise ReadbackHold("SHA_INVALID")
    if target_sha != main_sha:
        raise ReadbackHold("DEPLOY_TARGET_NOT_CURRENT_MAIN")
    if not isinstance(deploys, list):
        raise ReadbackHold("RENDER_DEPLOY_LIST_INVALID")
    normalized = [d.get("deploy", d) for d in deploys if isinstance(d, dict)]
    live = [d for d in normalized if isinstance(d, dict) and d.get("status") == "live"]
    if len(live) != 1:
        raise ReadbackHold("RENDER_LIVE_DEPLOY_NOT_UNIQUE")
    deployed = live[0]
    deployed_sha = str((deployed.get("commit") or {}).get("id") or "")
    deploy_id = str(deployed.get("id") or "")
    if deployed_sha != target_sha or not deploy_id:
        raise ReadbackHold("RENDER_LIVE_SHA_MISMATCH")
    if health_ok is not True:
        raise ReadbackHold("HEALTH_LIVE_NOT_SUCCESS")
    return {
        "status": "LIVE_SHA_AND_HEALTH_OBSERVED",
        "target_sha": target_sha,
        "observed_main_sha": main_sha,
        "render_live_sha": deployed_sha,
        "render_deploy_id": deploy_id,
        "health_live": "HTTP_200",
        "production_verified": False,
        "terminal": "PENDING_SIRT_INDEPENDENT_ACCEPTANCE",
        "model_ready": "UNVERIFIED",
        "can_execute": False,
    }


def _get_json(url: str, headers: dict[str, str]) -> Any:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read()
        return json.loads(data.decode("utf-8"))


def _readback() -> dict[str, Any]:
    target = os.environ["EXPECTED_SHA"]
    if not SHA.fullmatch(target or ""):
        raise ReadbackHold("EXPECTED_SHA_INVALID")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo != REPO:
        raise ReadbackHold("REPOSITORY_MISMATCH")
    github_token = os.environ.get("GH_TOKEN") or ""
    render_token = os.environ.get("RENDER_API_KEY") or ""
    if not github_token or not render_token:
        raise ReadbackHold("READ_ONLY_API_CREDENTIAL_MISSING")
    gh_headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + github_token,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api = "https://api.github.com/repos/" + REPO
    if os.environ.get("SOURCE_EVENT") == "workflow_run":
        source_id = os.environ.get("SOURCE_RUN_ID") or ""
        if not re.fullmatch(r"[1-9][0-9]*", source_id):
            raise ReadbackHold("DEPLOY_SOURCE_RUN_ID_INVALID")
        run = _get_json(api + "/actions/runs/" + source_id, gh_headers)
        validate_source_run(run, target_sha=target)
    elif os.environ.get("SOURCE_EVENT") != "workflow_dispatch":
        raise ReadbackHold("SOURCE_EVENT_NOT_ALLOWED")
    branch = _get_json(api + "/branches/main", gh_headers)
    main_sha = str((branch.get("commit") or {}).get("sha") or "")
    deploys = _get_json(
        "https://api.render.com/v1/services/" + urllib.parse.quote(SERVICE_ID, safe="")
        + "/deploys?limit=20",
        {"Accept": "application/json", "Authorization": "Bearer " + render_token},
    )
    if isinstance(deploys, dict) and isinstance(deploys.get("deploys"), list):
        deploys = deploys["deploys"]
    health_ok = False
    try:
        request = urllib.request.Request(PUBLIC_HEALTH, method="GET")
        with urllib.request.urlopen(request, timeout=30) as response:
            health_ok = response.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        health_ok = False
    return classify_readback(
        target_sha=target, main_sha=main_sha, deploys=deploys, health_ok=health_ok,
    )


def main() -> int:
    path = Path(os.environ.get("READBACK_RECEIPT", "lean-production-readback.json"))
    try:
        result = _readback()
        exit_code = 0
    except (ReadbackHold, KeyError, TypeError, ValueError, urllib.error.URLError,
            urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        reason = str(exc) if isinstance(exc, ReadbackHold) else "READBACK_SOURCE_UNAVAILABLE_OR_INVALID"
        result = {
            "status": "READBACK_HOLD",
            "reason": reason,
            "expected_sha": os.environ.get("EXPECTED_SHA", ""),
            "production_verified": False,
            "model_ready": "UNVERIFIED",
            "can_execute": False,
        }
        exit_code = 1
    path.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
