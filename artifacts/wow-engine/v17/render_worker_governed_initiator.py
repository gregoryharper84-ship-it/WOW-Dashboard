"""Governed lease-to-Render handoff. Dependency-injected; no credentials or startup side effects."""
from __future__ import annotations

from dataclasses import dataclass
import re

from render_worker_lease_adapter import ALLOWED_SERVICE, claim_committed, record_committed

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class Handoff:
    state: str
    deploy_id: str | None = None
    lease_token: int | None = None


def initiate_once(*, connection, target_sha: str, current_main_sha: str,
                  owner_run_id: str, primary_release_result: str,
                  list_deploys, post_deploy) -> Handoff:
    if primary_release_result != "success" or not SHA_RE.fullmatch(target_sha or "") or target_sha != current_main_sha:
        raise RuntimeError("WORKER_RELEASE_NOT_AUTHORIZED_FOR_EXACT_MAIN")
    # Provider observations precede claims; earlier exact revision is never POSTed again.
    observed = list_deploys()
    matches = [d for d in observed if (d.get("commit") or {}).get("id") == target_sha]
    if matches:
        best = matches[0]
        if best.get("status") in {"build_failed", "update_failed", "pre_deploy_failed", "canceled", "cancelled", "deactivated"}:
            raise RuntimeError("WORKER_EXISTING_EXACT_DEPLOY_FAILED")
        if not str(best.get("id") or "").startswith("dep-"):
            raise RuntimeError("WORKER_EXISTING_DEPLOY_ID_INVALID")
        return Handoff("EXISTING_EXACT_DEPLOY", str(best["id"]))
    claim = claim_committed(connection, service_id=ALLOWED_SERVICE,
                            target_sha=target_sha, owner_run_id=owner_run_id)
    if not claim.can_initiate:
        return Handoff("RECONCILIATION_REQUIRED")
    # Recheck after the committed claim; any ambiguous response transitions to HOLD.
    try:
        latest = list_deploys()
        if any((d.get("commit") or {}).get("id") == target_sha for d in latest):
            record_committed(connection, service_id=ALLOWED_SERVICE, target_sha=target_sha,
                             owner_run_id=owner_run_id, token=claim.token, state="HOLD")
            return Handoff("RECONCILIATION_REQUIRED")
        # Exactly one POST attempt per durable intent; never retry this call.
        reply = post_deploy()
        item = reply.get("deploy", reply) if isinstance(reply, dict) else {}
        deploy_id = str(item.get("id") or "")
        if not deploy_id.startswith("dep-"):
            raise RuntimeError("WORKER_POST_ACK_MISSING_DEPLOY_ID")
        record_committed(connection, service_id=ALLOWED_SERVICE, target_sha=target_sha,
                         owner_run_id=owner_run_id, token=claim.token,
                         state="DEPLOYING", deploy_id=deploy_id)
        return Handoff("DEPLOYING", deploy_id, claim.token)
    except BaseException:
        try:
            record_committed(connection, service_id=ALLOWED_SERVICE, target_sha=target_sha,
                             owner_run_id=owner_run_id, token=claim.token, state="ACK_AMBIGUOUS")
        except Exception:
            # Durable INITIATING remains and blocks all repeat initiation.
            pass
        raise
