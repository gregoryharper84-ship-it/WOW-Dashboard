"""Observational release checks that cannot authorize a Render POST.

The connected application project ``iczfhsmjrrafhvcpmqhr`` is
``wow-engine-validation``. A matching Supabase URL is not a certified
``wow_release_control`` schema, principal, or workload identity.

A Render ``/deploys`` list is a point-in-time snapshot. It is not the
durable service-wide fence that commits ``POST_UNCERTAIN`` before one
``commitId`` POST. ``post_allowed`` stays false.

can_execute stays false. This module does not call Render or GitHub.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any


QA_CHECK = "WOW Independent QA exact-head"
RELEASE_CHECK = "WOW Release Authority exact-head"
COMMITTED_APPLICATION_DATABASE_PROJECT_REF = "iczfhsmjrrafhvcpmqhr"
APPLICATION_DATABASE_PROJECT_NAME = "wow-engine-validation"
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


def classify_role_checks(
    check_runs: Any,
    *,
    qa_app_id: str,
    release_app_id: str,
    source_head: str,
) -> str:
    """Return PASS, PENDING, or a typed failure."""
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
    """Compare application project refs. A match does not certify release control."""
    blockers: list[str] = [
        "RELEASE_CONTROL_SCHEMA_NOT_CERTIFIED",
        "APPLICATION_DATABASE_PROJECT_IS_NOT_RELEASE_CONTROL_SCHEMA",
    ]
    if can_execute is not False:
        blockers.append("RELEASE_DATABASE_CAN_EXECUTE_UNSAFE")
    if str(service_id or "") != expected_service_id:
        blockers.append("RELEASE_DATABASE_SERVICE_IDENTITY_MISMATCH")
    if str(database_provider or "") != "SUPABASE":
        blockers.append("RELEASE_DATABASE_PROVIDER_UNVERIFIED")
    observed = str(observed_project_ref or "")
    sealed = str(sealed_project_ref or "")
    observed_ok = bool(_PROJECT_REF_RE.fullmatch(observed))
    sealed_ok = bool(_PROJECT_REF_RE.fullmatch(sealed))
    if not observed_ok:
        blockers.append("RELEASE_DATABASE_IDENTITY_UNREADABLE")
    if not sealed_ok:
        blockers.append("RELEASE_DATABASE_IDENTITY_UNBOUND")
    elif observed_ok and observed != sealed:
        blockers.append("RELEASE_DATABASE_IDENTITY_MISMATCH")
    return {
        "passed": False,
        "application_project_matches": observed_ok and sealed_ok and observed == sealed,
        "release_control_schema_certified": False,
        "deploy_authorized": False,
        "blockers": blockers,
        "observed_project_ref": observed if observed_ok else "",
        "sealed_project_ref": sealed if sealed_ok else "",
        "application_database_project_name": APPLICATION_DATABASE_PROJECT_NAME,
        "can_execute": False,
    }


def _deploy_sha(deploy: dict[str, Any]) -> str:
    commit = deploy.get("commit") if isinstance(deploy.get("commit"), dict) else {}
    return str(commit.get("id") or deploy.get("commit_sha") or "")


def writer_lock_observation(
    *,
    auto_deploy_trigger: str,
    deploys: Any,
    target_sha: str,
) -> dict[str, Any]:
    """Record a deploy-list snapshot. Never treat it as the durable fence."""
    blockers = ["RENDER_DEPLOY_LIST_SNAPSHOT_IS_NOT_DURABLE_FENCE"]
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
        "passed": False,
        "durable_fence": False,
        "blockers": blockers,
        "observe_only": active_same and "RELEASE_WRITER_LOCK_BUSY" not in blockers,
        "post_allowed": False,
        "auto_deploy_trigger": trigger,
        "can_execute": False,
    }


def admit_production_release(
    *,
    role_checks: str,
    database_identity: dict[str, Any],
    writer_lock: dict[str, Any],
) -> dict[str, Any]:
    """Always a non-advancing hold. Role evidence does not clear the fence."""
    blockers: list[str] = []
    if role_checks != "PASS":
        blockers.append(role_checks or "QA_RELEASE_DECISION_MISSING")
    blockers.extend(database_identity.get("blockers") or [])
    blockers.extend(writer_lock.get("blockers") or [])
    return {
        "eligible": False,
        "status": "PRODUCTION_RELEASE_HOLD",
        "blockers": blockers,
        "observe_only": False,
        "post_allowed": False,
        "deploy_authorized": False,
        "controls": {
            "qa_release": role_checks == "PASS",
            "database_identity": False,
            "writer_lock": False,
            "durable_post_uncertain_fence": False,
        },
        "can_execute": False,
    }
