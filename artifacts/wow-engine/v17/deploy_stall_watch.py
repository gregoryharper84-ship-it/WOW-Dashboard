"""GET-only main-to-production deploy stall detection.

WOW-V17-DEPLOY-RELIABILITY-P0 / #2422: once the three required checks are green
on the exact current ``main`` SHA, the sole authorized writer
(``wow-v17-required-checks-deploy.yml``) must begin a deploy. This observer
compares that authorization clock to live web/worker deploy state.

Thresholds (retro):
* alert when no covering deploy has begun within 15 minutes of authorization;
* escalate after 30 minutes;
* also report dual-writer residue when native ``checksPass`` is still enabled.

Never deploys, never PATCHes, never certifies production or model readiness.
Non-advancing outcomes exit 0 so this optional observer cannot poison main.
``can_execute=false``.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from v17.agent_identity_policy import REQUIRED_REGRESSION_CHECKS
from v17.lean_production_readback import (
    ACCEPTED_AUTO_DEPLOY_TRIGGERS,
    CANONICAL_AUTO_DEPLOY_TRIGGER,
    REPO,
    SERVICES,
    SHA,
    SOLE_DEPLOY_WRITER,
    native_auto_deploy_retired,
    service_identity_ok,
)
from v17.required_checks_deploy import ACTIVE_OR_LIVE, deploy_covering, required_checks_state

GITHUB_ACTIONS_APP_ID = 15368
ALERT_AFTER_SECONDS = 15 * 60
ESCALATE_AFTER_SECONDS = 30 * 60

Fetch = Callable[[str, str, Any], Any]


class StallHold(ValueError):
    pass


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def authorization_age_seconds(
    check_runs: Any,
    *,
    sha: str,
    now: datetime,
) -> tuple[str, int | None, str | None]:
    """Return (checks_state, age_seconds, authorized_at_iso)."""
    state = required_checks_state(check_runs, sha)
    if state != "success":
        return state, None, None
    completed_at: list[datetime] = []
    if isinstance(check_runs, list):
        for name in REQUIRED_REGRESSION_CHECKS:
            runs = [
                r for r in check_runs
                if isinstance(r, dict) and r.get("name") == name
                and r.get("head_sha") == sha
                and (r.get("app") or {}).get("id") == GITHUB_ACTIONS_APP_ID
                and r.get("status") == "completed"
                and r.get("conclusion") == "success"
            ]
            if not runs:
                return "pending", None, None
            latest = max(runs, key=lambda r: int(r.get("id") or 0))
            stamp = _parse_time(latest.get("completed_at"))
            if stamp is None:
                return state, None, None
            completed_at.append(stamp)
    if len(completed_at) != len(REQUIRED_REGRESSION_CHECKS):
        return state, None, None
    authorized_at = max(completed_at)
    age = int((now - authorized_at).total_seconds())
    if age < 0:
        return state, None, authorized_at.isoformat().replace("+00:00", "Z")
    return state, age, authorized_at.isoformat().replace("+00:00", "Z")


def covering_progress(deploys: dict[str, list[dict[str, Any]]], sha: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for role, items in deploys.items():
        covering = deploy_covering(items, sha)
        if covering is None:
            out[role] = "missing"
        elif covering.get("status") == "live":
            out[role] = "live"
        else:
            out[role] = "in_progress"
    return out


def classify_stall(
    *,
    main_sha: str,
    checks_state: str,
    age_seconds: int | None,
    authorized_at: str | None,
    services: dict[str, Any],
    progress: dict[str, str],
    alert_after: int = ALERT_AFTER_SECONDS,
    escalate_after: int = ESCALATE_AFTER_SECONDS,
) -> dict[str, Any]:
    """Pure decision for stall / dual-writer observation."""
    if not SHA.fullmatch(main_sha or ""):
        raise StallHold("SHA_INVALID")
    if not isinstance(services, dict) or not isinstance(progress, dict):
        raise StallHold("STALL_INPUT_INVALID")
    for role in SERVICES:
        if not service_identity_ok(role, services.get(role)):
            raise StallHold("RENDER_SINGLE_WRITER_SERVICE_CONFIGURATION_MISMATCH:" + role)
        if role not in progress:
            raise StallHold("STALL_PROGRESS_INCOMPLETE")
    dual = not native_auto_deploy_retired(services)
    base = {
        "main_sha": main_sha,
        "checks_state": checks_state,
        "authorized_at": authorized_at,
        "age_seconds": age_seconds,
        "progress": dict(progress),
        "sole_deploy_writer": SOLE_DEPLOY_WRITER,
        "canonical_auto_deploy_trigger": CANONICAL_AUTO_DEPLOY_TRIGGER,
        "accepted_auto_deploy_triggers": sorted(ACCEPTED_AUTO_DEPLOY_TRIGGERS),
        "native_auto_deploy_retired": not dual,
        "dual_writer_residue": dual,
        "alert_after_seconds": alert_after,
        "escalate_after_seconds": escalate_after,
        "production_verified": False,
        "can_execute": False,
    }
    if dual:
        base["dual_writer_status"] = "DUAL_WRITER_NATIVE_STILL_ENABLED"
    if checks_state != "success":
        return {
            **base,
            "status": "NOT_AUTHORIZED_NO_STALL",
            "severity": "REQUIRED_CHECKS_" + checks_state.upper(),
        }
    if age_seconds is None:
        return {
            **base,
            "status": "AUTHORIZATION_CLOCK_UNREADABLE",
            "severity": "REQUIRED_CHECK_COMPLETED_AT_MISSING",
        }
    if all(state in {"live", "in_progress"} for state in progress.values()):
        return {
            **base,
            "status": "DEPLOY_COVERING_OBSERVED",
            "severity": "ALL_SERVICES_HAVE_COVERING_DEPLOY",
        }
    missing = sorted(role for role, state in progress.items() if state == "missing")
    if age_seconds >= escalate_after:
        return {
            **base,
            "status": "DEPLOY_STALL_ESCALATION",
            "severity": "NO_COVERING_DEPLOY_AFTER_ESCALATION_WINDOW",
            "missing_services": missing,
        }
    if age_seconds >= alert_after:
        return {
            **base,
            "status": "DEPLOY_STALL_ALERT",
            "severity": "NO_COVERING_DEPLOY_AFTER_ALERT_WINDOW",
            "missing_services": missing,
        }
    return {
        **base,
        "status": "AUTHORIZED_WITHIN_GRACE",
        "severity": "WAITING_FOR_SOLE_WRITER_WITHIN_ALERT_WINDOW",
        "missing_services": missing,
    }


def _deploys(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("deploys"), list):
        payload = payload["deploys"]
    if not isinstance(payload, list):
        raise StallHold("RENDER_DEPLOY_LIST_INVALID")
    return [d.get("deploy", d) for d in payload if isinstance(d, dict)]


def run(*, fetch: Fetch, now: datetime | None = None) -> dict[str, Any]:
    clock = now or datetime.now(timezone.utc)
    gh = "https://api.github.com/repos/" + REPO
    render = "https://api.render.com/v1/services/"
    main_sha = str(((fetch("GET", gh + "/branches/main", None) or {}).get("commit") or {})
                   .get("sha") or "")
    if not SHA.fullmatch(main_sha):
        raise StallHold("MAIN_SHA_INVALID")
    runs: list[Any] = []
    for name in REQUIRED_REGRESSION_CHECKS:
        body = fetch(
            "GET",
            gh + "/commits/" + main_sha + "/check-runs?per_page=100&check_name="
            + urllib.parse.quote(name, safe=""),
            None,
        )
        page = body.get("check_runs") if isinstance(body, dict) else None
        if not isinstance(page, list):
            raise StallHold("CHECK_RUNS_UNREADABLE")
        runs.extend(page)
    checks_state, age, authorized_at = authorization_age_seconds(
        runs, sha=main_sha, now=clock)
    services: dict[str, Any] = {}
    deploys: dict[str, list[dict[str, Any]]] = {}
    for role, service_id in SERVICES.items():
        sid = urllib.parse.quote(service_id, safe="")
        services[role] = fetch("GET", render + sid, None)
        deploys[role] = _deploys(fetch("GET", render + sid + "/deploys?limit=20", None))
    return classify_stall(
        main_sha=main_sha,
        checks_state=checks_state,
        age_seconds=age,
        authorized_at=authorized_at,
        services=services,
        progress=covering_progress(deploys, main_sha),
    )


def main() -> int:
    receipt = Path(os.environ.get("STALL_RECEIPT", "wow-v17-deploy-stall-watch.json"))
    try:
        if os.environ.get("WOW_CAN_EXECUTE") != "false" or os.environ.get("WOW_DRY_RUN_ONLY") != "true":
            raise StallHold("EXECUTION_INVARIANT_MISMATCH")
        if os.environ.get("GITHUB_REPOSITORY") != REPO:
            raise StallHold("REPOSITORY_MISMATCH")
        gh_token = os.environ.get("GH_TOKEN") or ""
        render_token = os.environ.get("RENDER_API_KEY") or ""
        if not gh_token or not render_token:
            raise StallHold("READ_ONLY_API_CREDENTIAL_MISSING")
        gh_headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + gh_token,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        render_headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + render_token,
        }

        def fetch(method: str, url: str, body: Any) -> Any:
            if method != "GET":
                raise StallHold("WRITE_TARGET_NOT_ALLOWED")
            headers = render_headers if url.startswith("https://api.render.com/") else gh_headers
            data = json.dumps(body).encode("utf-8") if body is not None else None
            request = urllib.request.Request(url, data=data, method=method, headers={
                **headers,
                **({"Content-Type": "application/json"} if data else {}),
            })
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}

        outcome = run(fetch=fetch)
        code = 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, StallHold) else "STALL_SOURCE_UNAVAILABLE_OR_INVALID"
        # Optional observer: never fail the main commit check for transport gaps.
        outcome = {
            "status": "STALL_HOLD" if isinstance(exc, StallHold) else "STALL_UNVERIFIED",
            "reason": reason,
            "production_verified": False,
            "can_execute": False,
        }
        code = 0
    receipt.write_text(json.dumps(outcome, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(outcome, sort_keys=True))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(
                f"deploy-stall-watch: {outcome.get('status')} "
                f"{outcome.get('reason', '')} dual_writer={outcome.get('dual_writer_residue')} "
                f"can_execute=false\n"
            )
            if outcome.get("status") in {"DEPLOY_STALL_ALERT", "DEPLOY_STALL_ESCALATION"}:
                handle.write(
                    f"::error::DEPLOY_STALL_{outcome['status']}: authorized main SHA has no "
                    "covering deploy within the configured window.\n"
                )
            if outcome.get("dual_writer_residue"):
                handle.write(
                    "::error::DUAL_WRITER_NATIVE_STILL_ENABLED: retire native checksPass via "
                    "the controlled single-writer cutover.\n"
                )
    return code


if __name__ == "__main__":
    sys.exit(main())
