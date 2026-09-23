from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable


ADVANCING_RECEIPT_STATUSES = frozenset(
    {
        "EXACT_SHA_RENDER_DEPLOY_LIVE",
        "EXACT_SHA_ALREADY_LIVE",
    }
)
NON_ADVANCING_RECEIPT_STATUSES = frozenset(
    {
        "NON_MAIN_NO_DEPLOY",
        "STALE_SHA_NO_DEPLOY",
        "UPSTREAM_NOT_SUCCESS_NO_DEPLOY",
        "DEPLOY_SUPERSEDED_BY_NEW_MAIN",
    }
)
KNOWN_RECEIPT_STATUSES = ADVANCING_RECEIPT_STATUSES | NON_ADVANCING_RECEIPT_STATUSES
POINTER_ENVIRONMENT = "wow-v17-render-production-attestation"
POINTER_TASK = "wow-v17-render-receipt-pointer"
POINTER_SOURCE = "WOW_V17_EXACT_RENDER_RECEIPT"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class DeploymentPointerContractError(RuntimeError):
    pass


def _require_receipt_dict(receipt: Any) -> dict[str, Any]:
    if not isinstance(receipt, dict):
        raise DeploymentPointerContractError("DEPLOYMENT_POINTER_RECEIPT_NOT_OBJECT")
    return receipt


def receipt_advances_pointer(receipt: Any) -> bool:
    receipt = _require_receipt_dict(receipt)
    status = str(receipt.get("status") or "")
    if status not in KNOWN_RECEIPT_STATUSES:
        raise DeploymentPointerContractError(f"DEPLOYMENT_POINTER_UNKNOWN_RECEIPT_STATUS:{status or 'MISSING'}")
    if receipt.get("can_execute") is not False:
        raise DeploymentPointerContractError("DEPLOYMENT_POINTER_CAN_EXECUTE_MUST_BE_FALSE")
    return status in ADVANCING_RECEIPT_STATUSES


def normalized_advancing_receipt(receipt: Any) -> dict[str, Any]:
    receipt = _require_receipt_dict(receipt)
    if not receipt_advances_pointer(receipt):
        raise DeploymentPointerContractError("DEPLOYMENT_POINTER_NON_ADVANCING_RECEIPT")

    commit_sha = str(receipt.get("commit_sha") or "").lower()
    deploy_id = str(receipt.get("deploy_id") or "")
    deploy_authority = str(receipt.get("deploy_authority") or "")
    status = str(receipt.get("status") or "")

    if not _SHA_RE.fullmatch(commit_sha):
        raise DeploymentPointerContractError("DEPLOYMENT_POINTER_COMMIT_SHA_INVALID")
    if not deploy_id.startswith("dep-"):
        raise DeploymentPointerContractError("DEPLOYMENT_POINTER_RENDER_DEPLOY_ID_INVALID")
    if deploy_authority != "GOVERNED_RENDER_API_HANDOFF":
        raise DeploymentPointerContractError("DEPLOYMENT_POINTER_AUTHORITY_INVALID")

    return {
        "commit_sha": commit_sha,
        "render_deploy_id": deploy_id,
        "source_receipt_status": status,
        "deploy_authority": deploy_authority,
        "can_execute": False,
        "source_workflow_run_id": str(receipt.get("workflow_run_id") or ""),
        "source_workflow_run_attempt": str(receipt.get("workflow_run_attempt") or ""),
    }


def _decode_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def reconcile_deployment_pointer(
    receipt: Any,
    github_api: Callable[[str, str, dict[str, Any] | None], Any],
    *,
    repository: str,
) -> dict[str, Any]:
    if not receipt_advances_pointer(receipt):
        return {
            "status": "DEPLOYMENT_POINTER_UNCHANGED",
            "source_receipt_status": str(receipt.get("status") or ""),
            "can_execute": False,
        }

    pointer = normalized_advancing_receipt(receipt)
    query = urllib.parse.urlencode(
        {
            "environment": POINTER_ENVIRONMENT,
            "ref": pointer["commit_sha"],
            "per_page": 100,
        }
    )
    deployments = github_api("GET", f"/deployments?{query}", None)
    if not isinstance(deployments, list):
        raise DeploymentPointerContractError("DEPLOYMENT_POINTER_DEPLOYMENTS_RESPONSE_INVALID")

    deployment_id = None
    for deployment in deployments:
        if not isinstance(deployment, dict):
            continue
        payload = _decode_payload(deployment.get("payload"))
        if (
            payload.get("source") == POINTER_SOURCE
            and payload.get("render_deploy_id") == pointer["render_deploy_id"]
            and payload.get("commit_sha") == pointer["commit_sha"]
        ):
            deployment_id = int(deployment.get("id") or 0) or None
            if deployment_id:
                break

    created = False
    if deployment_id is None:
        payload = {
            "ref": pointer["commit_sha"],
            "task": POINTER_TASK,
            "auto_merge": False,
            "required_contexts": [],
            "environment": POINTER_ENVIRONMENT,
            "description": "WOW V17 exact Render deployment receipt pointer",
            "payload": {
                "source": POINTER_SOURCE,
                **pointer,
            },
        }
        deployment = github_api("POST", "/deployments", payload)
        if not isinstance(deployment, dict) or not int(deployment.get("id") or 0):
            raise DeploymentPointerContractError("DEPLOYMENT_POINTER_CREATE_RESPONSE_INVALID")
        deployment_id = int(deployment["id"])
        created = True

    statuses = github_api("GET", f"/deployments/{deployment_id}/statuses?per_page=100", None)
    if not isinstance(statuses, list):
        raise DeploymentPointerContractError("DEPLOYMENT_POINTER_STATUSES_RESPONSE_INVALID")

    success_description = (
        f"{pointer['source_receipt_status']} | render={pointer['render_deploy_id']} | can_execute=false"
    )
    status_exists = any(
        isinstance(item, dict)
        and item.get("state") == "success"
        and str(item.get("description") or "") == success_description
        for item in statuses
    )
    if not status_exists:
        github_api(
            "POST",
            f"/deployments/{deployment_id}/statuses",
            {
                "state": "success",
                "description": success_description,
                "environment": POINTER_ENVIRONMENT,
                "auto_inactive": True,
            },
        )

    return {
        "status": "DEPLOYMENT_POINTER_RECONCILED",
        "github_deployment_id": deployment_id,
        "github_deployment_created": created,
        **pointer,
    }


def _github_api_factory(repository: str, token: str):
    base = f"https://api.github.com/repos/{repository}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    def github_api(method: str, path: str, payload: dict[str, Any] | None = None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request_headers = dict(headers)
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{base}{path}",
            data=body,
            headers=request_headers,
            method=method,
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    return github_api


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        raise DeploymentPointerContractError("DEPLOYMENT_POINTER_RECEIPT_PATH_REQUIRED")

    receipt_path = Path(args[0])
    if not receipt_path.is_file():
        raise DeploymentPointerContractError("DEPLOYMENT_POINTER_RECEIPT_FILE_MISSING")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    repository = str(os.environ.get("GITHUB_REPOSITORY") or "")
    token = str(os.environ.get("GH_API_TOKEN") or "")
    if not repository:
        raise DeploymentPointerContractError("DEPLOYMENT_POINTER_REPOSITORY_MISSING")

    # No-deploy receipts must be pure no-ops and must not require write credentials.
    if not receipt_advances_pointer(receipt):
        result = reconcile_deployment_pointer(receipt, lambda *_args, **_kwargs: None, repository=repository)
        print(json.dumps(result, sort_keys=True))
        return 0

    if not token:
        raise DeploymentPointerContractError("DEPLOYMENT_POINTER_GITHUB_TOKEN_MISSING")
    result = reconcile_deployment_pointer(
        receipt,
        _github_api_factory(repository, token),
        repository=repository,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
