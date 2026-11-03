"""GET-only two-service production observation for native CI-gated Render deploys.

Never deploys, approves, merges, or self-certifies the V17 model.
A current GitHub main SHA + successful CI is necessary but not a SIRT
or model acceptance receipt.
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
SOURCE_WORKFLOW = "wow-verify"
SOURCE_PATH = ".github/workflows/wow-verify.yml"
PUBLIC_HEALTH = "https://wow-governed-probability-engine.onrender.com/health/live"
SERVICES = {
    "web": "srv-da7sa9gu01pc73brt80g",
    "worker": "srv-dapkbc0473hc73c1978g",
}
SHA = re.compile(r"^[0-9a-f]{40}$")


class ReadbackHold(ValueError):
    pass


def validate_source_run(run: dict[str, Any], *, target_sha: str) -> None:
    """A workflow-run event must be bound to an authentic main CI result."""
    if (run.get("name") != SOURCE_WORKFLOW
            or run.get("path") != SOURCE_PATH
            or (run.get("repository") or {}).get("full_name") != REPO
            or (run.get("head_repository") or {}).get("full_name") != REPO
            or run.get("head_branch") != "main"
            or run.get("head_sha") != target_sha
            or run.get("status") != "completed"
            or run.get("conclusion") != "success"
            or run.get("event") not in {"push", "workflow_dispatch"}):
        raise ReadbackHold("MAIN_CI_SOURCE_NOT_AUTHENTIC_EXACT_SUCCESS")


def _live_deploy(deploys: Any, role: str) -> tuple[str, str]:
    if not isinstance(deploys, list):
        raise ReadbackHold("RENDER_DEPLOY_LIST_INVALID:" + role)
    normalized = [d.get("deploy", d) for d in deploys if isinstance(d, dict)]
    live = [d for d in normalized if isinstance(d, dict) and d.get("status") == "live"]
    if len(live) != 1:
        raise ReadbackHold("RENDER_LIVE_DEPLOY_NOT_UNIQUE:" + role)
    item = live[0]
    return str((item.get("commit") or {}).get("id") or ""), str(item.get("id") or "")


def classify_readback(*, target_sha: str, main_sha: str,
                      services: dict[str, dict[str, Any]],
                      deploys: dict[str, list[dict[str, Any]]],
                      health_ok: bool) -> dict[str, Any]:
    if not SHA.fullmatch(target_sha or "") or not SHA.fullmatch(main_sha or ""):
        raise ReadbackHold("SHA_INVALID")
    if target_sha != main_sha:
        raise ReadbackHold("TARGET_NOT_CURRENT_MAIN")
    if not isinstance(services, dict) or not isinstance(deploys, dict):
        raise ReadbackHold("SERVICE_READBACK_INVALID")
    ids = {}
    for role, service_id in SERVICES.items():
        service = services.get(role) or {}
        if (service.get("id") != service_id
                or service.get("branch") != "main"
                or service.get("repo") != "https://github.com/" + REPO
                or service.get("autoDeployTrigger") != "checksPass"):
            raise ReadbackHold("RENDER_CI_GATED_SERVICE_CONFIGURATION_MISMATCH:" + role)
        actual_sha, deploy_id = _live_deploy(deploys.get(role), role)
        if actual_sha != target_sha or not deploy_id:
            raise ReadbackHold("RENDER_LIVE_SHA_MISMATCH:" + role)
        ids[role] = deploy_id
    if health_ok is not True:
        raise ReadbackHold("HEALTH_LIVE_NOT_SUCCESS")
    return {
        "status": "WEB_WORKER_LIVE_SHA_AND_HEALTH_OBSERVED",
        "target_sha": target_sha,
        "observed_main_sha": main_sha,
        "web_deploy_id": ids["web"],
        "worker_deploy_id": ids["worker"],
        "health_live": "HTTP_200",
        "production_verified": False,
        "terminal": "PENDING_SIRT_INDEPENDENT_ACCEPTANCE",
        "model_ready": "UNVERIFIED",
        "can_execute": False,
    }


def _get_json(url: str, headers: dict[str, str]) -> Any:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _readback() -> dict[str, Any]:
    target = os.environ["EXPECTED_SHA"]
    if not SHA.fullmatch(target or ""):
        raise ReadbackHold("EXPECTED_SHA_INVALID")
    if os.environ.get("GITHUB_REPOSITORY") != REPO:
        raise ReadbackHold("REPOSITORY_MISMATCH")
    gh_token = os.environ.get("GH_TOKEN") or ""
    render_token = os.environ.get("RENDER_API_KEY") or ""
    if not gh_token or not render_token:
        raise ReadbackHold("READ_ONLY_API_CREDENTIAL_MISSING")
    gh_headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + gh_token,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api = "https://api.github.com/repos/" + REPO
    event = os.environ.get("SOURCE_EVENT")
    if event == "workflow_run":
        source_id = os.environ.get("SOURCE_RUN_ID") or ""
        if not re.fullmatch(r"[1-9][0-9]*", source_id):
            raise ReadbackHold("MAIN_CI_SOURCE_RUN_ID_INVALID")
        source = _get_json(api + "/actions/runs/" + source_id, gh_headers)
        validate_source_run(source, target_sha=target)
    elif event != "workflow_dispatch":
        raise ReadbackHold("SOURCE_EVENT_NOT_ALLOWED")
    branch = _get_json(api + "/branches/main", gh_headers)
    main_sha = str((branch.get("commit") or {}).get("sha") or "")
    render_headers = {
        "Accept": "application/json",
        "Authorization": "Bearer " + render_token,
    }
    services, deploys = {}, {}
    for role, service_id in SERVICES.items():
        base = "https://api.render.com/v1/services/" + urllib.parse.quote(service_id, safe="")
        services[role] = _get_json(base, render_headers)
        payload = _get_json(base + "/deploys?limit=20", render_headers)
        if isinstance(payload, dict) and isinstance(payload.get("deploys"), list):
            payload = payload["deploys"]
        deploys[role] = payload
    try:
        request = urllib.request.Request(PUBLIC_HEALTH, method="GET")
        with urllib.request.urlopen(request, timeout=30) as response:
            health_ok = response.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        health_ok = False
    return classify_readback(
        target_sha=target, main_sha=main_sha, services=services,
        deploys=deploys, health_ok=health_ok,
    )


def main() -> int:
    path = Path(os.environ.get("READBACK_RECEIPT", "lean-production-readback.json"))
    try:
        outcome = _readback()
        code = 0
    except (ReadbackHold, KeyError, TypeError, ValueError, urllib.error.URLError,
            urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
        reason = str(exc) if isinstance(exc, ReadbackHold) else "READBACK_SOURCE_UNAVAILABLE_OR_INVALID"
        outcome = {
            "status": "READBACK_HOLD",
            "reason": reason,
            "expected_sha": os.environ.get("EXPECTED_SHA", ""),
            "production_verified": False,
            "model_ready": "UNVERIFIED",
            "can_execute": False,
        }
        code = 1
    path.write_text(json.dumps(outcome, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(outcome, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
