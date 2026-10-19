"""Admit one production deploy only when three controls all pass.

The controls stay on. A passing result is evidence, not a caller boolean:

- QA and Release are distinct GitHub App check runs on the exact source head.
- The Render service's Supabase project matches the sealed application
  database identity. A GitHub variable overrides the committed seal.
- The release lock is the single non-terminal Render deploy. Another SHA
  in progress keeps the lock; this SHA may be observed or posted once.

can_execute stays false. This module does not call Render or GitHub.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any


QA_CHECK = "WOW Independent QA exact-head"
RELEASE_CHECK = "WOW Release Authority exact-head"
# Application persistence project already published by the production scout
# and OpenAPI surfaces. Not a release-control schema and not a boolean.
COMMITTED_APPLICATION_DATABASE_PROJECT_REF = "iczfhsmjrrafhvcpmqhr"
EXPECTED_RENDER_SERVICE_ID = "srv-da7sa9gu01pc73brt80g"
_PROJECT_REF_RE = re.compile(r"^[a-z0-9]{20}$")
_TERMINAL_DEPLOY_STATUSES = frozenset(
    {
        "live",
        "build_failed",
        "update_failed",
        "pre_deploy_failed",
        "deactivated",
        "canceled",
        "cancelled",
    }
)


def project_ref_from_supabase_url(url: str) -> str:
    host = urllib.parse.urlparse(str(url or "")).hostname or ""
    if not host.endswith(".supabase.co"):
        return ""
    ref = host.split(".")[0]
    return ref if _PROJECT_REF_RE.fullmatch(ref) else ""


def env_var_value(payload: Any, key: str) -> str:
    items = payload
    if isinstance(payload, dict):
        items = payload.get("envVars") or payload.get("env_vars") or []
    if not isinstance(items, list):
        return ""
    for item in items:
        if not isinstance(item, dict):
            continue
        env = item.get("envVar") if isinstance(item.get("envVar"), dict) else item
        if str(env.get("key") or "") == key:
            return str(env.get("value") or "")
    return ""


def native_auto_deploy_trigger(service: Any) -> str:
    body = service.get("service") if isinstance(service, dict) and isinstance(service.get("service"), dict) else service
    if not isinstance(body, dict):
        return ""
    trigger = str(body.get("autoDeployTrigger") or body.get("autoDeploy") or "")
    details = body.get("serviceDetails")
    if not trigger and isinstance(details, dict):
        trigger = str(details.get("autoDeployTrigger") or details.get("autoDeploy") or "")
    return trigger.strip().lower()


def classify_role_checks(
    check_runs: Any,
    *,
    qa_app_id: str,
    release_app_id: str,
    source_head: str,
) -> str:
    """Return PASS, PENDING, or a typed failure.

    Missing and in-progress decisions stay PENDING so a later independent
    App check can still authorize. A completed non-success fails immediately.
    """
    if not (str(qa_app_id).isdigit() and str(release_app_id).isdigit()):
        return "RENDER_RELEASE_APP_IDENTITIES_UNBOUND"
    if int(qa_app_id) == int(release_app_id):
        return "RENDER_RELEASE_APP_IDENTITIES_NOT_DISTINCT"
    if not isinstance(check_runs, list):
        return "RENDER_RELEASE_CHECK_EVIDENCE_INVALID"
    required = {
        QA_CHECK: int(qa_app_id),
        RELEASE_CHECK: int(release_app_id),
    }
    for name, role_app_id in required.items():
        matching = [
            check
            for check in check_runs
            if isinstance(check, dict)
            and str(check.get("name") or "") == name
            and str(check.get("head_sha") or "") == source_head
            and (check.get("app") or {}).get("id") == role_app_id
        ]
        if not matching:
            return "PENDING"
        latest = max(matching, key=lambda check: int(check.get("id") or 0))
        if latest.get("status") != "completed":
            return "PENDING"
        if latest.get("conclusion") != "success":
            return "RENDER_RELEASE_ROLE_CHECK_NOT_SUCCESS:" + name
    return "PASS"


def database_identity_decision(
    *,
    observed_project_ref: str,
    sealed_project_ref: str,
    database_provider: str,
    service_id: str,
    expected_service_id: str = EXPECTED_RENDER_SERVICE_ID,
    can_execute: bool = False,
) -> dict[str, Any]:
    blockers: list[str] = []
    if can_execute is not False:
        blockers.append("RELEASE_DATABASE_CAN_EXECUTE_UNSAFE")
    if str(service_id or "") != expected_service_id:
        blockers.append("RELEASE_DATABASE_SERVICE_IDENTITY_MISMATCH")
    if str(database_provider or "") != "SUPABASE":
        blockers.append("RELEASE_DATABASE_PROVIDER_UNVERIFIED")
    observed = str(observed_project_ref or "")
    sealed = str(sealed_project_ref or "")
    if not _PROJECT_REF_RE.fullmatch(observed):
        blockers.append("RELEASE_DATABASE_IDENTITY_UNREADABLE")
    if not _PROJECT_REF_RE.fullmatch(sealed):
        blockers.append("RELEASE_DATABASE_IDENTITY_UNBOUND")
    elif _PROJECT_REF_RE.fullmatch(observed) and observed != sealed:
        blockers.append("RELEASE_DATABASE_IDENTITY_MISMATCH")
    return {
        "passed": not blockers,
        "blockers": blockers,
        "observed_project_ref": observed if _PROJECT_REF_RE.fullmatch(observed) else "",
        "sealed_project_ref": sealed if _PROJECT_REF_RE.fullmatch(sealed) else "",
    }


def _deploy_sha(deploy: dict[str, Any]) -> str:
    commit = deploy.get("commit") if isinstance(deploy.get("commit"), dict) else {}
    return str(commit.get("id") or deploy.get("commit_sha") or "")


def writer_lock_decision(
    *,
    auto_deploy_trigger: str,
    deploys: Any,
    target_sha: str,
) -> dict[str, Any]:
    """One non-terminal deploy owns the lock. This run may post only when none exists."""
    blockers: list[str] = []
    trigger = str(auto_deploy_trigger or "").strip().lower()
    if trigger not in {"off", "no", "yes", "checkspass"}:
        blockers.append("RELEASE_WRITER_INVENTORY_UNVERIFIED")
    elif trigger not in {"off", "no"}:
        blockers.append("RELEASE_NATIVE_WRITER_ENABLED")
    if not isinstance(deploys, list):
        blockers.append("RELEASE_WRITER_INVENTORY_UNVERIFIED")
        deploys = []
    active_other: list[str] = []
    active_same = False
    for deploy in deploys:
        if not isinstance(deploy, dict):
            continue
        status = str(deploy.get("status") or "").lower()
        if not status or status in _TERMINAL_DEPLOY_STATUSES:
            continue
        sha = _deploy_sha(deploy)
        if sha == target_sha:
            active_same = True
        else:
            active_other.append(sha or "UNKNOWN_SHA")
    if active_other:
        blockers.append("RELEASE_WRITER_LOCK_BUSY")
    return {
        "passed": not blockers,
        "blockers": blockers,
        "observe_only": active_same and not blockers,
        "post_allowed": not blockers and not active_same,
        "auto_deploy_trigger": trigger,
    }


def admit_production_release(
    *,
    role_checks: str,
    database_identity: dict[str, Any],
    writer_lock: dict[str, Any],
) -> dict[str, Any]:
    blockers: list[str] = []
    if role_checks != "PASS":
        blockers.append(role_checks or "QA_RELEASE_DECISION_MISSING")
    blockers.extend(database_identity.get("blockers") or [])
    blockers.extend(writer_lock.get("blockers") or [])
    eligible = not blockers
    return {
        "eligible": eligible,
        "status": "PRODUCTION_RELEASE_ADMITTED" if eligible else "PRODUCTION_RELEASE_HOLD",
        "blockers": blockers,
        "observe_only": bool(writer_lock.get("observe_only")) and eligible,
        "post_allowed": bool(writer_lock.get("post_allowed")) and eligible,
        "controls": {
            "qa_release": role_checks == "PASS",
            "database_identity": bool(database_identity.get("passed")),
            "writer_lock": bool(writer_lock.get("passed")),
        },
        "can_execute": False,
    }
