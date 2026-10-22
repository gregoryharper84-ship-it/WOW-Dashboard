"""Review-only PostgreSQL-backed, service-wide Render release fence.

NOT an authorized deployer. Bind a dedicated production DB/principal, verify
all competing Render writers and exact QA/Release approvals before integration.
Never retry an ambiguous provider POST. Never trade or place market orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_RE = re.compile(r"^[a-zA-Z0-9_.:-]{1,128}$")
SERVICE_RE = re.compile(r"^srv-[a-z0-9]{8,64}$")
DEPLOY_RE = re.compile(r"^dep-[a-z0-9]{8,64}$")


class ReleaseHold(RuntimeError):
    """Typed non-advancing HOLD; do not attempt a second provider mutation."""


@dataclass(frozen=True)
class Lease:
    service_id: str
    target_sha: str
    run_id: str
    owner: str
    fence: int
    post_state: str
    deploy_id: str | None

    @property
    def may_reserve_post(self) -> bool:
        return self.post_state == "RESERVED"


def _validate(service_id: str, target_sha: str, run_id: str, owner: str) -> None:
    if not SERVICE_RE.fullmatch(service_id):
        raise ReleaseHold("RELEASE_SERVICE_ID_INVALID")
    if not SHA_RE.fullmatch(target_sha):
        raise ReleaseHold("RELEASE_TARGET_SHA_INVALID")
    if not RUN_RE.fullmatch(run_id) or not RUN_RE.fullmatch(owner):
        raise ReleaseHold("RELEASE_IDENTITY_INVALID")


class ReleaseFence:
    """Caller-owned psycopg3 transaction connection, NOT production-wired.

    This candidate requires genuine PostgreSQL multi-connection testing and
    independently proven release database identity before adoption.
    """

    def __init__(self, connection: Any):
        # reserve_single_post MUST commit the durable POST_UNCERTAIN state
        # before issuing the external Render POST. A psycopg connection in
        # implicit-transaction mode can silently defer that commit until
        # after the network request, defeating the exact-once safety guard.
        if getattr(connection, "autocommit", True) is False:
            raise ReleaseHold("RELEASE_DB_AUTOCOMMIT_REQUIRED_BEFORE_RENDER_POST")
        self.db = connection

    def claim(self, service_id: str, target_sha: str, run_id: str, owner: str, *, ttl_seconds: int = 180) -> Lease:
        """Expire owner authority, NEVER an unresolved deployment intent."""
        _validate(service_id, target_sha, run_id, owner)
        if not (60 <= ttl_seconds <= 900):
            raise ReleaseHold("RELEASE_TTL_OUT_OF_RANGE")
        with self.db.transaction():
            with self.db.cursor() as cur:
                cur.execute("""INSERT INTO wow_release_control.service_fence(service_id)
                               VALUES (%s) ON CONFLICT (service_id) DO NOTHING""", (service_id,))
                cur.execute("""SELECT fence, lease_expires_at > clock_timestamp()
                               FROM wow_release_control.service_fence
                               WHERE service_id=%s FOR UPDATE""", (service_id,))
                current = cur.fetchone()
                if current is None:
                    raise ReleaseHold("RELEASE_SERVICE_FENCE_MISSING")
                if current[1]:
                    raise ReleaseHold("RELEASE_SERVICE_LEASE_BUSY")
                cur.execute("""SELECT target_sha, state
                               FROM wow_release_control.release_intent
                               WHERE service_id=%s AND state IN
                                 ('RESERVED','POST_UNCERTAIN','PROVIDER_ACK','LIVE_OBSERVED')
                               FOR UPDATE""", (service_id,))
                if cur.fetchall():
                    raise ReleaseHold("RELEASE_PREVIOUS_INTENT_UNRESOLVED_NO_REPOST")
                cur.execute("""SELECT 1 FROM wow_release_control.release_intent
                               WHERE service_id=%s AND target_sha=%s""",
                            (service_id, target_sha))
                if cur.fetchone() is not None:
                    raise ReleaseHold("RELEASE_EXISTING_INTENT_NO_REPOST")
                cur.execute("""UPDATE wow_release_control.service_fence
                               SET fence=fence+1, owner=%s, run_id=%s,
                                   target_sha=%s,
                                   lease_expires_at=clock_timestamp() + (%s * interval '1 second')
                               WHERE service_id=%s AND fence=%s
                               RETURNING fence""",
                            (owner, run_id, target_sha, ttl_seconds, service_id, current[0]))
                row = cur.fetchone()
                if row is None:
                    raise ReleaseHold("RELEASE_FENCE_CAS_FAILED")
                fence = int(row[0])
                cur.execute("""INSERT INTO wow_release_control.release_intent
                               (service_id,target_sha,run_id,owner,fence,state)
                               VALUES (%s,%s,%s,%s,%s,'RESERVED')
                               ON CONFLICT (service_id,target_sha) DO NOTHING
                               RETURNING state""",
                            (service_id, target_sha, run_id, owner, fence))
                if cur.fetchone() is None:
                    raise ReleaseHold("RELEASE_EXISTING_INTENT_NO_REPOST")
                return Lease(service_id, target_sha, run_id, owner, fence, "RESERVED", None)

    def renew(self, lease: Lease, *, ttl_seconds: int = 180) -> None:
        if not 60 <= ttl_seconds <= 900:
            raise ReleaseHold("RELEASE_TTL_OUT_OF_RANGE")
        with self.db.transaction():
            with self.db.cursor() as cur:
                cur.execute("""UPDATE wow_release_control.service_fence
                               SET lease_expires_at=clock_timestamp() + (%s * interval '1 second')
                               WHERE service_id=%s AND owner=%s AND run_id=%s AND target_sha=%s AND fence=%s
                                 AND lease_expires_at > clock_timestamp()
                               RETURNING fence""", (ttl_seconds, lease.service_id, lease.owner,
                                                     lease.run_id, lease.target_sha, lease.fence))
                if cur.fetchone() is None:
                    raise ReleaseHold("RELEASE_LEASE_EXPIRED_OR_FENCED")

    def reserve_single_post(self, lease: Lease) -> None:
        """Commit a permanent unknown-ACK state BEFORE sending any Render POST."""
        with self.db.transaction():
            with self.db.cursor() as cur:
                cur.execute("""UPDATE wow_release_control.release_intent i
                               SET state='POST_UNCERTAIN', post_reserved_at=clock_timestamp()
                               WHERE i.service_id=%s AND i.target_sha=%s AND i.run_id=%s
                                 AND i.state='RESERVED' AND i.owner=%s AND i.fence=%s
                                 AND EXISTS (
                                   SELECT 1 FROM wow_release_control.service_fence f
                                   WHERE f.service_id=i.service_id AND f.owner=%s
                                     AND f.run_id=i.run_id AND f.target_sha=i.target_sha
                                     AND f.fence=%s AND f.lease_expires_at > clock_timestamp()
                                 ) RETURNING state""",
                            (lease.service_id, lease.target_sha, lease.run_id,
                             lease.owner, lease.fence, lease.owner, lease.fence))
                if cur.fetchone() is None:
                    raise ReleaseHold("RELEASE_POST_ALREADY_RESERVED_OR_FENCED")

    def reconcile_provider(self, lease: Lease, *, deploy_id: str, deployed_sha: str,
                           provider_created_at: datetime, candidate_count: int) -> None:
        """Observe one exact provider ID; provider listing must be exhaustive."""
        if not DEPLOY_RE.fullmatch(deploy_id) or deployed_sha != lease.target_sha:
            raise ReleaseHold("RELEASE_PROVIDER_IDENTITY_MISMATCH")
        if candidate_count != 1 or provider_created_at.tzinfo is None:
            raise ReleaseHold("RELEASE_PROVIDER_ACK_AMBIGUOUS")
        with self.db.transaction():
            with self.db.cursor() as cur:
                cur.execute("""UPDATE wow_release_control.release_intent i
                               SET state='PROVIDER_ACK', provider_deploy_id=%s,
                                   observed_at=clock_timestamp()
                               WHERE i.service_id=%s AND i.target_sha=%s AND i.run_id=%s
                                 AND i.state='POST_UNCERTAIN' AND i.owner=%s AND i.fence=%s
                                 AND i.post_reserved_at <= %s
                                 AND EXISTS (
                                    SELECT 1 FROM wow_release_control.service_fence f
                                    WHERE f.service_id=i.service_id AND f.owner=%s
                                      AND f.run_id=i.run_id AND f.target_sha=i.target_sha
                                      AND f.fence=%s AND f.lease_expires_at > clock_timestamp()
                                 ) RETURNING provider_deploy_id""",
                            (deploy_id, lease.service_id, lease.target_sha, lease.run_id,
                             lease.owner, lease.fence, provider_created_at, lease.owner, lease.fence))
                row = cur.fetchone()
                if row is None or row[0] != deploy_id:
                    raise ReleaseHold("RELEASE_PROVIDER_ACK_RECONCILIATION_HOLD")

    def observe_live(self, lease: Lease, *, deploy_id: str, deployed_sha: str,
                     current_main_sha: str, provider_status: str) -> None:
        """Observation only: independent verification must still certify LIVE."""
        if deployed_sha != lease.target_sha or current_main_sha != lease.target_sha:
            raise ReleaseHold("RELEASE_MAIN_OR_PROVIDER_SHA_STALE")
        if provider_status != "live" or not DEPLOY_RE.fullmatch(deploy_id):
            raise ReleaseHold("RELEASE_PROVIDER_NOT_LIVE")
        with self.db.transaction():
            with self.db.cursor() as cur:
                cur.execute("""UPDATE wow_release_control.release_intent i
                               SET state='LIVE_OBSERVED', observed_at=clock_timestamp()
                               WHERE i.service_id=%s AND i.target_sha=%s AND i.run_id=%s
                                 AND i.state='PROVIDER_ACK' AND i.provider_deploy_id=%s
                                 AND i.owner=%s AND i.fence=%s
                                 AND EXISTS (
                                    SELECT 1 FROM wow_release_control.service_fence f
                                    WHERE f.service_id=i.service_id AND f.owner=%s
                                      AND f.run_id=i.run_id AND f.target_sha=i.target_sha
                                      AND f.fence=%s AND f.lease_expires_at > clock_timestamp()
                                 ) RETURNING state""",
                            (lease.service_id, lease.target_sha, lease.run_id,
                             deploy_id, lease.owner, lease.fence, lease.owner, lease.fence))
                if cur.fetchone() is None:
                    raise ReleaseHold("RELEASE_LIVE_RECEIPT_FENCED_OR_UNMATCHED")


def invoke_single_render_post(lease: Lease, fence: ReleaseFence, post_render: Any) -> Any:
    """Never retry a possibly accepted non-idempotent Render deployment POST."""
    fence.reserve_single_post(lease)
    try:
        return post_render({"clearCache": "do_not_clear", "commitId": lease.target_sha})
    except Exception as exc:
        raise ReleaseHold("RELEASE_PROVIDER_POST_ACK_UNKNOWN_RECONCILE_ONLY") from exc
