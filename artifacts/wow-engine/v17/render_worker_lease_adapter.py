"""Fail-closed SQL adapter for the private deployment lease ledger.

The calling workflow must supply an independently authorized DB connection.
This module does not infer an environment or grant itself credentials.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

ALLOWED_SERVICE = "srv-dapkbc0473hc73c1978g"


class DeploymentLeaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class LeaseDecision:
    state: str
    token: int
    can_initiate: bool


def attest_release_control_identity(connection, *, expected_project_ref: str) -> None:
    """Require an independently DBA-approved production ledger and limited role.

    The protected workflow supplies the approved expected project reference.
    The DB marker is a separate, non-seeded authorization established by the
    control-plane DBA, not by an Engineering change or environment fallback.
    """
    if not isinstance(expected_project_ref, str) or not re.fullmatch(
        r"[a-z]{20}", expected_project_ref
    ):
        raise DeploymentLeaseError("WORKER_RELEASE_PROJECT_IDENTITY_UNCONFIGURED")
    with connection.cursor() as cursor:
        cursor.execute(
            "select current_user, project_ref, environment, service_id, "
            "authorized, approved_by, approved_at "
            "from wow_release_control.release_control_identity "
            "where singleton = true",
            (),
        )
        rows = cursor.fetchall()
    if len(rows) != 1 or len(rows[0]) != 7:
        raise DeploymentLeaseError("WORKER_RELEASE_DB_IDENTITY_MISSING")
    (actor, project, environment, service, authorized, reviewer, approved_at) = rows[0]
    if actor != "wow_release_controller":
        raise DeploymentLeaseError("WORKER_RELEASE_DB_ROLE_NOT_RESTRICTED")
    if project != expected_project_ref or environment != "production" or service != ALLOWED_SERVICE:
        raise DeploymentLeaseError("WORKER_RELEASE_DB_IDENTITY_MISMATCH")
    if authorized is not True or not reviewer or approved_at is None:
        raise DeploymentLeaseError("WORKER_RELEASE_DB_APPROVAL_MISSING")


def claim(connection, *, service_id: str, target_sha: str, owner_run_id: str) -> LeaseDecision:
    if service_id != ALLOWED_SERVICE:
        raise DeploymentLeaseError("WORKER_SERVICE_NOT_ALLOWLISTED")
    # DB-side function owns the transaction and row-locking semantics.
    with connection.cursor() as cursor:
        cursor.execute(
            "select state,lease_token,can_initiate from "
            "wow_release_control.claim_render_worker_deploy(%s,%s,%s,%s)",
            (service_id, target_sha, owner_run_id, 300),
        )
        rows = cursor.fetchall()
    if len(rows) != 1 or len(rows[0]) != 3:
        raise DeploymentLeaseError("WORKER_DEPLOY_CLAIM_RESPONSE_INVALID")
    state, token, allowed = rows[0]
    if state != "INITIATING" and allowed:
        raise DeploymentLeaseError("WORKER_DEPLOY_CLAIM_INCONSISTENT")
    return LeaseDecision(str(state), int(token), allowed is True)


def record(connection, *, service_id: str, target_sha: str, owner_run_id: str,
           token: int, state: str, deploy_id: str | None = None) -> None:
    if service_id != ALLOWED_SERVICE:
        raise DeploymentLeaseError("WORKER_SERVICE_NOT_ALLOWLISTED")
    with connection.cursor() as cursor:
        cursor.execute(
            "select wow_release_control.record_render_worker_deploy(%s,%s,%s,%s,%s,%s)",
            (service_id, target_sha, owner_run_id, token, state, deploy_id),
        )
        rows = cursor.fetchall()
    if len(rows) != 1 or rows[0] != (True,):
        raise DeploymentLeaseError("WORKER_DEPLOY_RECORD_NOT_ACKNOWLEDGED")


def renew(connection, *, service_id: str, target_sha: str, owner_run_id: str,
          token: int, lease_seconds: int = 300) -> None:
    if service_id != ALLOWED_SERVICE:
        raise DeploymentLeaseError("WORKER_SERVICE_NOT_ALLOWLISTED")
    with connection.cursor() as cursor:
        cursor.execute(
            "select wow_release_control.renew_render_worker_deploy(%s,%s,%s,%s,%s)",
            (service_id, target_sha, owner_run_id, token, lease_seconds),
        )
        rows = cursor.fetchall()
    if len(rows) != 1 or rows[0] != (True,):
        raise DeploymentLeaseError("WORKER_DEPLOY_LEASE_RENEW_NOT_ACKNOWLEDGED")


def claim_committed(connection, *, service_id: str, target_sha: str, owner_run_id: str) -> LeaseDecision:
    """A Render POST must NEVER precede successful durable claim commit."""
    try:
        decision = claim(connection, service_id=service_id, target_sha=target_sha,
                         owner_run_id=owner_run_id)
        connection.commit()
        return decision
    except BaseException:
        connection.rollback()
        raise


def record_committed(connection, **kwargs) -> None:
    try:
        record(connection, **kwargs)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def renew_committed(connection, **kwargs) -> None:
    """Every lease extension commits before another provider observation."""
    try:
        renew(connection, **kwargs)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
