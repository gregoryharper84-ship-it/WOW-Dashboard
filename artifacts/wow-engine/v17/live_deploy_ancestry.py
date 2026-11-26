"""GET-only live deployed-SHA ancestry assertion for CI-gated Render deploys.

This check runs on protected ``main`` — the same commit Render's native
``checksPass`` trigger is evaluating. It therefore must never assert that
production already equals HEAD: that would deny the very deployment it is
waiting for, which is the release-gate circularity the repository already
repaired once (#2510) in ``lean_production_readback``.

It asserts only invariants a *pending* deployment cannot violate:

* both services are still the canonical repository/branch/``checksPass`` pair;
* each service has exactly one live deploy with a well-formed commit SHA;
* that live SHA is contained in protected ``main``'s history.

Equal or behind is an observation, reported as ``LIVE_SHA_BEHIND:<n>`` with the
exact lag. Ahead of, or diverged from, protected ``main`` is a HOLD: production
can never legitimately run code that is not in ``main``'s history.

Never deploys, never triggers a deploy, never certifies production or model
readiness. ``can_execute=false``; ``production_verified=false``.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from v17.lean_production_readback import REPO, SERVICES, SHA, _live_deploy

BEHIND_REASON_PREFIX = "LIVE_SHA_BEHIND:"


class AncestryHold(ValueError):
    pass


def assert_service(role: str, service: Any) -> None:
    """The live service must still be the canonical CI-gated production one."""
    if not isinstance(service, dict):
        raise AncestryHold("RENDER_SERVICE_READBACK_INVALID:" + role)
    if (service.get("id") != SERVICES[role]
            or service.get("branch") != "main"
            or service.get("repo") != "https://github.com/" + REPO
            or service.get("autoDeployTrigger") != "checksPass"):
        raise AncestryHold("RENDER_CI_GATED_SERVICE_CONFIGURATION_MISMATCH:" + role)


def classify_ancestry(*, live_sha: str, main_sha: str,
                      comparison: Any) -> dict[str, Any]:
    """Prove the live SHA is contained in main history without requiring equality.

    ``comparison`` is the authenticated GitHub ``compare/{live}...{main}`` body,
    so ``ahead_by`` counts commits main has that live does not. ``behind_by``
    must be zero: a non-zero value means production carries commits that are
    absent from protected main.
    """
    if not SHA.fullmatch(live_sha or "") or not SHA.fullmatch(main_sha or ""):
        raise AncestryHold("LIVE_SHA_INVALID")
    if not isinstance(comparison, dict):
        raise AncestryHold("COMPARISON_UNREADABLE")
    status = comparison.get("status")
    ahead = comparison.get("ahead_by")
    behind = comparison.get("behind_by")
    if (not isinstance(ahead, int) or isinstance(ahead, bool)
            or not isinstance(behind, int) or isinstance(behind, bool)):
        raise AncestryHold("COMPARISON_COUNTS_INVALID")
    if not isinstance(status, str):
        raise AncestryHold("COMPARISON_UNREADABLE")
    if status == "identical":
        if ahead != 0 or behind != 0:
            raise AncestryHold("COMPARISON_CONTRADICTS_STATUS")
        lag = 0
    elif status == "ahead" and behind == 0:
        # main is ahead of the live commit: contained, but not yet deployed.
        lag = ahead
    else:
        # "behind" = live carries commits main lacks. "diverged" = no containment.
        raise AncestryHold("LIVE_SHA_NOT_IN_MAIN_HISTORY:" + status)
    return {
        "status": "LIVE_SHA_CONTAINED_IN_MAIN",
        "reason": BEHIND_REASON_PREFIX + str(lag),
        "live_sha": live_sha,
        "main_sha": main_sha,
        "deployment_lag_commits": lag,
        "deployed_equals_main": lag == 0,
        "production_verified": False,
        "model_ready": "UNVERIFIED",
        "can_execute": False,
    }


def _get_json(url: str, headers: dict[str, str]) -> Any:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _assert_live_ancestry() -> dict[str, Any]:
    gh_token = os.environ.get("GH_TOKEN") or ""
    render_token = os.environ.get("RENDER_API_KEY") or ""
    if not gh_token or not render_token:
        raise AncestryHold("READ_ONLY_API_CREDENTIAL_MISSING")
    if os.environ.get("GITHUB_REPOSITORY") != REPO:
        raise AncestryHold("REPOSITORY_MISMATCH")
    gh_headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + gh_token,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    render_headers = {
        "Accept": "application/json",
        "Authorization": "Bearer " + render_token,
    }
    api = "https://api.github.com/repos/" + REPO
    main_sha = str(((_get_json(api + "/branches/main", gh_headers)
                     .get("commit") or {}).get("sha")) or "")
    if not SHA.fullmatch(main_sha):
        raise AncestryHold("MAIN_SHA_INVALID")
    roles: dict[str, Any] = {}
    for role, service_id in SERVICES.items():
        base = "https://api.render.com/v1/services/" + urllib.parse.quote(service_id, safe="")
        service = _get_json(base, render_headers)
        assert_service(role, service)
        deploys = _get_json(base + "/deploys?limit=20", render_headers)
        if isinstance(deploys, dict) and isinstance(deploys.get("deploys"), list):
            deploys = deploys["deploys"]
        live_sha, deploy_id = _live_deploy(deploys, role)
        comparison = _get_json(api + "/compare/" + live_sha + "..." + main_sha, gh_headers)
        roles[role] = {
            "deploy_id": deploy_id,
            **classify_ancestry(
                live_sha=live_sha, main_sha=main_sha, comparison=comparison),
        }
    return {
        "status": "BOTH_SERVICES_LIVE_SHA_CONTAINED_IN_MAIN",
        "main_sha": main_sha,
        "observed_lag_commits": max(role["deployment_lag_commits"] for role in roles.values()),
        "web": roles["web"],
        "worker": roles["worker"],
        "production_verified": False,
        "model_ready": "UNVERIFIED",
        "can_execute": False,
    }


def main() -> int:
    path = Path(os.environ.get("ANCESTRY_RECEIPT", "live-deploy-ancestry.json"))
    try:
        outcome = _assert_live_ancestry()
        code = 0
    except Exception as exc:
        # A typed hold is real evidence (divergence, foreign service, malformed
        # deploy) and fails closed. A transport/credential gap is only an
        # unverified observation: this check runs on the same commit Render's
        # checksPass trigger is evaluating, so an infrastructure blip must never
        # deny the deployment it is meant to observe.
        hold = isinstance(exc, AncestryHold)
        reason = str(exc) if hold else "ANCESTRY_SOURCE_UNAVAILABLE_OR_INVALID"
        outcome = {
            "status": "ANCESTRY_HOLD" if hold else "ANCESTRY_UNVERIFIED",
            "reason": reason,
            "production_verified": False,
            "model_ready": "UNVERIFIED",
            "can_execute": False,
        }
        code = 1 if hold else 0
    path.write_text(json.dumps(outcome, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(outcome, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
