"""Deploy recovery keyed only to the three ruleset-required checks.

Render's native ``checksPass`` trigger remains the primary deploy path and is
unchanged. Render skips any commit that carries a single failing check run,
and optional main-scoped workflows (independent QA, Release Authority,
canaries, maintenance) attach failing check runs to almost every protected
``main`` commit. Example: 27 ``qa`` + 3 ``admit-release`` failures on
``e9d7b057``, 2026-11-20. Those commits are never auto-deployed.

This module automates the V27-authorized recovery action ("redeploy the
current main SHA to recover a failed auto-deploy"):

* the target is the exact current ``main`` SHA, never an older or foreign one;
* all three required checks (GitHub Actions app 15368) concluded ``success``
  at that SHA;
* both services are still the canonical CI-gated web/worker pair;
* Render has not already started, or completed, a deploy of that SHA;
* exactly one deploy per missing service is POSTed, and both services must
  then be LIVE on the SHA with public health OK.

Non-advancing outcomes (superseded SHA, required checks pending or failed, a
transient read failure before any write) end with exit 0 and a typed receipt,
so this job never becomes another failing check on ``main``. A genuine deploy
failure, timeout, service-identity drift, credential gap or health failure
exits 1. The only write is POST /v1/services/<id>/deploys; redirects are refused.

Never touches Render env vars, plans or services. Never merges, approves, or
certifies production or model readiness. ``can_execute=false``.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from v17.agent_identity_policy import REQUIRED_REGRESSION_CHECKS
from v17.lean_production_readback import (
    PUBLIC_HEALTH,
    REPO,
    SERVICES,
    SHA,
    ReadbackHold,
    classify_readback,
)
from v17.live_deploy_ancestry import AncestryHold, assert_service

GITHUB_ACTIONS_APP_ID = 15368
# Render deploy states that already cover the target SHA: do not POST again.
ACTIVE_OR_LIVE = frozenset({
    "created", "queued", "build_in_progress", "update_in_progress",
    "pre_deploy_in_progress", "live",
})
FAILED = frozenset({"build_failed", "update_failed", "pre_deploy_failed", "canceled"})
NON_ADVANCING = frozenset({
    "SUPERSEDED_NO_DEPLOY",
    "REQUIRED_CHECKS_PENDING_NO_DEPLOY",
    "REQUIRED_CHECKS_FAILED_NO_DEPLOY",
})

HEALTH_ATTEMPTS = 5

Fetch = Callable[[str, str, Any], Any]


class DeployHold(ValueError):
    pass


def _deploys(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("deploys"), list):
        payload = payload["deploys"]
    if not isinstance(payload, list):
        raise DeployHold("RENDER_DEPLOY_LIST_INVALID")
    return [d.get("deploy", d) for d in payload if isinstance(d, dict)]


def required_checks_state(check_runs: Any, sha: str) -> str:
    """``success`` only when every required check's latest App-15368 run at
    ``sha`` completed successfully. Missing or in-progress is ``pending``;
    any other conclusion is ``failure``. Runs from other apps never count."""
    if not isinstance(check_runs, list):
        return "pending"
    state = "success"
    for name in REQUIRED_REGRESSION_CHECKS:
        runs = [
            r for r in check_runs
            if isinstance(r, dict) and r.get("name") == name
            and r.get("head_sha") == sha
            and (r.get("app") or {}).get("id") == GITHUB_ACTIONS_APP_ID
        ]
        if not runs:
            return "pending"
        latest = max(runs, key=lambda r: int(r.get("id") or 0))
        if latest.get("status") != "completed":
            state = "pending" if state == "success" else state
        elif latest.get("conclusion") != "success":
            return "failure"
    return state


def deploy_covering(deploys: list[dict[str, Any]], sha: str) -> dict[str, Any] | None:
    """The newest Render deploy of ``sha`` that is in progress or live."""
    for deploy in deploys:  # Render lists newest first
        if ((deploy.get("commit") or {}).get("id") == sha
                and deploy.get("status") in ACTIVE_OR_LIVE):
            return deploy
    return None


def plan(*, target_sha: str, main_sha: str, checks_state: str,
         deploys: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Pure decision: which services (if any) need a deploy POST."""
    if not SHA.fullmatch(target_sha or "") or not SHA.fullmatch(main_sha or ""):
        raise DeployHold("SHA_INVALID")
    base = {"target_sha": target_sha, "main_sha": main_sha, "can_execute": False}
    if target_sha != main_sha:
        return {**base, "status": "SUPERSEDED_NO_DEPLOY", "trigger": []}
    if checks_state == "pending":
        return {**base, "status": "REQUIRED_CHECKS_PENDING_NO_DEPLOY", "trigger": []}
    if checks_state != "success":
        return {**base, "status": "REQUIRED_CHECKS_FAILED_NO_DEPLOY", "trigger": []}
    trigger = [role for role in SERVICES if deploy_covering(deploys[role], target_sha) is None]
    return {**base, "status": "DEPLOY_NEEDED" if trigger else "DEPLOY_ALREADY_PRESENT",
            "trigger": trigger}


def progress(deploys: list[dict[str, Any]], sha: str) -> str:
    """``live`` if any deploy of ``sha`` is live, ``pending`` while one is still
    active (a cancelled duplicate of the same SHA is not a failure), otherwise
    ``failed`` when the newest deploy of ``sha`` ended in a failed state."""
    states = [d.get("status") for d in deploys if (d.get("commit") or {}).get("id") == sha]
    if "live" in states:
        return "live"
    if not states or any(state in ACTIVE_OR_LIVE for state in states):
        return "pending"
    return "failed" if states[0] in FAILED else "pending"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect, so a bearer token can never follow one off-host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _http(method: str, url: str, headers: dict[str, str], body: Any = None) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        **headers, **({"Content-Type": "application/json"} if data else {})})
    with _OPENER.open(request, timeout=30) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def run(*, target_sha: str, fetch: Fetch, sleep: Callable[[float], None] = time.sleep,
        health: Callable[[], bool], poll_seconds: int = 20,
        deploy_timeout_seconds: int = 1800) -> dict[str, Any]:
    gh = "https://api.github.com/repos/" + REPO
    render = "https://api.render.com/v1/services/"

    def main_sha() -> str:
        return str(((fetch("GET", gh + "/branches/main", None) or {}).get("commit") or {})
                   .get("sha") or "")

    def check_runs() -> Any:
        # A main commit carries hundreds of optional check runs; query each
        # required name directly so pagination can never hide one.
        runs: list[Any] = []
        for name in REQUIRED_REGRESSION_CHECKS:
            body = fetch("GET", gh + "/commits/" + target_sha + "/check-runs?per_page=100&check_name="
                         + urllib.parse.quote(name, safe=""), None)
            page = body.get("check_runs") if isinstance(body, dict) else None
            if not isinstance(page, list):
                return None
            runs.extend(page)
        return runs

    def deploys(role: str) -> list[dict[str, Any]]:
        sid = urllib.parse.quote(SERVICES[role], safe="")
        return _deploys(fetch("GET", render + sid + "/deploys?limit=20", None))

    services = {}
    for role, service_id in SERVICES.items():
        services[role] = fetch("GET", render + urllib.parse.quote(service_id, safe=""), None)
        try:
            assert_service(role, services[role])
        except AncestryHold as exc:
            raise DeployHold(str(exc)) from exc

    decision = plan(target_sha=target_sha, main_sha=main_sha(),
                    checks_state=required_checks_state(check_runs(), target_sha),
                    deploys={role: deploys(role) for role in SERVICES})
    if decision["status"] in NON_ADVANCING:
        return decision
    # No grace wait for Render's native deploy: Render waits for every check
    # on the commit, including this in-progress job, so it cannot start first.
    # If every optional check later passes, Render may rebuild the same SHA;
    # that duplicate is harmless and never deploys different code.
    posted = {}
    for role in decision["trigger"]:
        if main_sha() != target_sha:  # recheck immediately before every write
            return {**decision, "status": "SUPERSEDED_NO_DEPLOY", "posted": posted}
        sid = urllib.parse.quote(SERVICES[role], safe="")
        created = fetch("POST", render + sid + "/deploys",
                        {"commitId": target_sha, "clearCache": "do_not_clear"})
        posted[role] = str((created or {}).get("id") or "")
    waited, latest = 0, {}
    while True:
        if main_sha() != target_sha:  # a newer main supersedes (Render may cancel this build)
            return {**decision, "status": "SUPERSEDED_NO_DEPLOY", "posted": posted}
        latest = {role: deploys(role) for role in SERVICES}
        states = {role: progress(latest[role], target_sha) for role in SERVICES}
        if any(s == "failed" for s in states.values()):
            raise DeployHold("RENDER_DEPLOY_FAILED:" + ",".join(
                r for r, s in sorted(states.items()) if s == "failed"))
        if all(s == "live" for s in states.values()):
            break
        if waited >= deploy_timeout_seconds:
            raise DeployHold("RENDER_DEPLOY_TIMEOUT:" + ",".join(
                r for r, s in sorted(states.items()) if s != "live"))
        sleep(poll_seconds)
        waited += poll_seconds
    healthy = False
    for _ in range(HEALTH_ATTEMPTS):  # a cold start may briefly 5xx after LIVE
        if health():
            healthy = True
            break
        sleep(poll_seconds)
    try:
        observed = classify_readback(target_sha=target_sha, main_sha=main_sha(),
                                     services=services, deploys=latest, health_ok=healthy)
    except ReadbackHold as exc:
        raise DeployHold(str(exc)) from exc
    return {**decision, "status": "DEPLOY_LIVE_AND_HEALTHY", "posted": posted,
            "readback": observed, "production_verified": False}


def main() -> int:
    receipt = Path(os.environ.get("DEPLOY_RECEIPT", "wow-v17-required-checks-deploy.json"))
    target = os.environ.get("TARGET_SHA", "")
    wrote = {"post": False}
    try:
        if os.environ.get("WOW_CAN_EXECUTE") != "false" or os.environ.get("WOW_DRY_RUN_ONLY") != "true":
            raise DeployHold("EXECUTION_INVARIANT_MISMATCH")
        if os.environ.get("GITHUB_REPOSITORY") != REPO:
            raise DeployHold("REPOSITORY_MISMATCH")
        gh_token, render_token = os.environ.get("GH_TOKEN") or "", os.environ.get("RENDER_API_KEY") or ""
        if not gh_token or not render_token:
            raise DeployHold("API_CREDENTIAL_MISSING")
        gh_headers = {"Accept": "application/vnd.github+json", "Authorization": "Bearer " + gh_token,
                      "X-GitHub-Api-Version": "2022-11-28"}
        render_headers = {"Accept": "application/json", "Authorization": "Bearer " + render_token}

        def fetch(method: str, url: str, body: Any) -> Any:
            if method != "GET" and not (method == "POST" and url.startswith(
                    "https://api.render.com/v1/services/") and url.endswith("/deploys")):
                raise DeployHold("WRITE_TARGET_NOT_ALLOWED")
            if method == "POST":
                wrote["post"] = True
            headers = render_headers if url.startswith("https://api.render.com/") else gh_headers
            return _http(method, url, headers, body)

        def health() -> bool:
            try:
                with _OPENER.open(urllib.request.Request(PUBLIC_HEALTH), timeout=30) as r:
                    return r.status == 200
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
                return False

        outcome = run(target_sha=target, fetch=fetch, health=health)
        code = 0
    except Exception as exc:  # every failure leaves a typed, credential-free receipt
        if not wrote["post"] and not isinstance(exc, DeployHold):
            # A transient read failure before any write must not become one more
            # failing check on the main commit that checksPass is evaluating.
            outcome = {"status": "PRE_WRITE_SOURCE_UNAVAILABLE_NO_DEPLOY", "target_sha": target,
                       "production_verified": False, "can_execute": False}
            code = 0
        else:
            reason = str(exc) if isinstance(exc, DeployHold) else "DEPLOY_SOURCE_UNAVAILABLE_OR_INVALID"
            outcome = {"status": "DEPLOY_HOLD", "reason": reason, "target_sha": target,
                       "production_verified": False, "can_execute": False}
            code = 1
    receipt.write_text(json.dumps(outcome, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(outcome, sort_keys=True))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"required-checks deploy: {outcome['status']} "
                         f"{outcome.get('reason', '')} target={target} can_execute=false\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
