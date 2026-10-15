"""Primary API Render deployment: crash-safe exactly-once POST *authorization*.

This file deliberately does not authenticate GitHub/CODEOWNER/QA, select a DB,
or trigger deployment on import. The protected release workflow must first:
  - prove trusted Engineering App + QA + Release approvals for exact main SHA
  - verify a DBA-certified control DB and least-privilege release login
  - connect with autocommit=True, no shared Supabase service role
  - verify Render service autoDeployTrigger=off and the live source HEAD.
The caller must NOT retry after network error, timeout, 429, 500 or lost ACK.
"""
from __future__ import annotations

import re
from typing import Any, Callable

PRIMARY_SERVICE = "srv-da7sa9gu01pc73brt80g"
SHA = re.compile(r"^[0-9a-f]{40}$")
OWNER = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class PrimaryReleaseHold(RuntimeError):
    pass


def _guard_inputs(service_id: str, sha: str, run_id: str) -> None:
    if service_id != PRIMARY_SERVICE or not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise PrimaryReleaseHold("PRIMARY_RELEASE_TARGET_INVALID")
    if not isinstance(run_id, str) or not OWNER.fullmatch(run_id):
        raise PrimaryReleaseHold("PRIMARY_RELEASE_OWNER_INVALID")


def reserve_single_post(connection: Any, *, service_id: str, sha: str, run_id: str) -> None:
    """Commit the one-shot token before returning; never invoke Render within transaction."""
    _guard_inputs(service_id, sha, run_id)
    if getattr(connection, "autocommit", None) is not True:
        raise PrimaryReleaseHold("PRIMARY_RELEASE_CONNECTION_AUTOCOMMIT_REQUIRED")
    with connection.transaction():
        row = connection.execute(
            "select wow_release_control.reserve_primary_api_post(%s,%s,%s)",
            (service_id, sha, run_id),
        ).fetchone()
        if not row or row[0] != "PRIMARY_RELEASE_POST_AUTHORIZED_ONCE":
            raise PrimaryReleaseHold("PRIMARY_RELEASE_RESERVATION_UNVERIFIED")


def post_render_exactly_once(
    connection: Any,
    post_render: Callable[[dict[str, str]], Any],
    *,
    service_id: str,
    sha: str,
    run_id: str,
) -> dict[str, Any]:
    """Never retries a POST. An ambiguous acknowledgment is a persistent HOLD.

    A successful HTTP response without a verifiable exact commit/deploy ID
    remains a HOLD requiring independently authenticated provider readback.
    """
    reserve_single_post(connection, service_id=service_id, sha=sha, run_id=run_id)
    try:
        result = post_render({"commitId": sha, "clearCache": "do_not_clear"})
    except Exception as exc:
        raise PrimaryReleaseHold("PRIMARY_RELEASE_POST_ACK_UNKNOWN_RECONCILE_ONLY") from exc
    deployment = result.get("deploy", result) if isinstance(result, dict) else None
    if not isinstance(deployment, dict):
        raise PrimaryReleaseHold("PRIMARY_RELEASE_PROVIDER_RESPONSE_UNVERIFIED")
    commit = deployment.get("commit")
    returned = commit.get("id") if isinstance(commit, dict) else None
    deploy_id = deployment.get("id")
    if returned != sha or not isinstance(deploy_id, str) or not re.fullmatch(
        r"dep-[a-z0-9]+", deploy_id
    ):
        raise PrimaryReleaseHold("PRIMARY_RELEASE_PROVIDER_IDENTITY_UNVERIFIED")
    return {
        "status": "POST_ACK_ONLY_REQUIRES_LIVE_READBACK",
        "commit_sha": sha,
        "deploy_id": deploy_id,
        "can_execute": False,
    }


def record_authenticated_readback(
    connection: Any,
    *,
    service_id: str,
    sha: str,
    run_id: str,
    deploy_id: str,
    observed_sha: str,
    live: bool,
) -> dict[str, Any]:
    """Only records independently authenticated provider evidence.

    This is NOT end-to-end acceptance. Independent QA must verify live SHA,
    terminal scoring, actual GPT Action output and persistence separately.
    """
    _guard_inputs(service_id, sha, run_id)
    if getattr(connection, "autocommit", None) is not True:
        raise PrimaryReleaseHold("PRIMARY_RELEASE_CONNECTION_AUTOCOMMIT_REQUIRED")
    if observed_sha != sha or type(live) is not bool or not (
        isinstance(deploy_id, str) and re.fullmatch(r"dep-[a-z0-9]+", deploy_id)
    ):
        raise PrimaryReleaseHold("PRIMARY_RELEASE_READBACK_INPUT_INVALID")
    with connection.transaction():
        row = connection.execute(
            "select wow_release_control.observe_primary_api_deploy(%s,%s,%s,%s,%s,%s)",
            (service_id, sha, run_id, deploy_id, observed_sha, live),
        ).fetchone()
        if not row or row[0] != "PRIMARY_RELEASE_PROVIDER_READBACK_ONLY":
            raise PrimaryReleaseHold("PRIMARY_RELEASE_READBACK_CONFLICT")
    return {
        "status": "LIVE_OBSERVED_NOT_QA_VERIFIED" if live else "PROVIDER_ACK",
        "commit_sha": sha,
        "deploy_id": deploy_id,
        "can_execute": False,
    }
