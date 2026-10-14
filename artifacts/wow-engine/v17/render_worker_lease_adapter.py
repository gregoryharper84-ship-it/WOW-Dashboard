"""Fail-closed SQL adapter for the private deployment lease ledger.

The calling workflow must supply an independently authorized DB connection.
This module does not infer an environment or grant itself credentials.
"""
from __future__ import annotations

from dataclasses import dataclass

ALLOWED_SERVICE = "srv-dapkbc0473hc73c1978g"


class DeploymentLeaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class LeaseDecision:
    state: str
    token: int
    can_initiate: bool


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
