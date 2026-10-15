"""Review-only PostgREST adapter for the V17 once-only dispatch outbox.

Disabled until the independent DB owner migrates and certifies
docs/wow/engineering/proposals/WOW_CONTINUITY_OUTBOX_REVIEW_ONLY.sql.
This module does not run at import, claim writer leases, perform dispatches,
grant app identity, approve PRs, or deploy. The trusted host must bind the
writer claim from authenticated workflow provenance, not issue/user text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from typing import Any, Callable
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from v17.engineering_continuity_outbox import Proposal, Reservation
from v17.engineering_continuity_lease_attestation import (
    WriterClaim, attest_writer_claim, LeaseAttestationError,
)

_RPC_PREFIX = "/rest/v1/rpc/wow_engineering_continuity_outbox_"
_ALLOWED = frozenset({"reserve", "abort", "arm", "mark"})
_PREARM_REASONS = frozenset({
    "MAIN_CHANGED_BEFORE_DISPATCH", "PR_HEAD_RECHECK_UNAVAILABLE",
    "PR_CHANGED_BEFORE_DISPATCH", "WRITER_FENCE_UNVERIFIED",
    "PREARM_RECHECK_UNAVAILABLE",
})


class OutboxProtocolError(RuntimeError):
    """Typed failure that never embeds secrets/provider body or request URLs."""


@dataclass
class PostgrestOutboxStore:
    """An atomic-RPC-only OutboxStore; no direct table CRUD or retry loop."""

    claim: WriterClaim
    endpoint: str = field(default_factory=lambda: os.getenv("SUPABASE_URL", ""))
    credential: str = field(
        default_factory=lambda: os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
        repr=False,
    )
    transport: Callable[..., Any] | None = field(default=None, repr=False)
    new_intent_id: Callable[[], str] = field(
        default_factory=lambda: lambda: str(uuid4()), repr=False,
    )

    def _attest(self) -> bool:
        try:
            return attest_writer_claim(
                self.claim,
                endpoint=self.endpoint,
                credential=self.credential,
                transport=self.transport,
            ) is True
        except LeaseAttestationError:
            return False

    def _rpc(self, operation: str, payload: dict) -> Any:
        if operation not in _ALLOWED:
            raise OutboxProtocolError("OUTBOX_OPERATION_INVALID")
        if (not isinstance(self.endpoint, str)
                or not self.endpoint.startswith("https://")
                or any(ch in self.endpoint for ch in ("@", "?", "#"))
                or not isinstance(self.credential, str)
                or not self.credential.strip()):
            raise OutboxProtocolError("OUTBOX_STORE_UNAVAILABLE")
        request = Request(
            self.endpoint.rstrip("/") + _RPC_PREFIX + operation,
            data=json.dumps(payload, sort_keys=True).encode("utf-8"),
            headers={
                "apikey": self.credential,
                "Authorization": "Bearer " + self.credential,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        fn = self.transport if self.transport is not None else urlopen
        try:
            with fn(request, timeout=8) as response:
                raw = response.read(2049)
            if len(raw) > 2048:
                raise OutboxProtocolError("OUTBOX_REPLY_TOO_LARGE")
            return json.loads(raw)
        except OutboxProtocolError:
            raise
        except (HTTPError, URLError, OSError, ValueError, TypeError, TimeoutError):
            # Avoid error bodies and URLs, which may include protected tokens.
            raise OutboxProtocolError("OUTBOX_STORE_UNAVAILABLE") from None

    def reserve(self, proposal: Proposal) -> tuple[str, Reservation | None]:
        if type(proposal.incident) is not int or proposal.incident != self.claim.incident:
            raise OutboxProtocolError("OUTBOX_INCIDENT_MISMATCH")
        if not self._attest():
            return "LEASE_NOT_VERIFIED", None
        try:
            nonce = self.new_intent_id()
        except Exception:
            raise OutboxProtocolError("OUTBOX_INTENT_UNAVAILABLE") from None
        # Server casts nonce to uuid, refusing malformed/forged identifiers.
        if not isinstance(nonce, str) or len(nonce) != 36:
            raise OutboxProtocolError("OUTBOX_INTENT_INVALID")
        response = self._rpc("reserve", {
            "p_incident_id": proposal.incident,
            "p_intent_id": nonce,
            "p_main_sha": proposal.main_sha,
            "p_action": proposal.action,
            "p_pr_number": proposal.pr_number,
            "p_pr_head_sha": proposal.pr_head_sha,
            "p_owner_id": self.claim.owner_id,
            "p_epoch": self.claim.epoch,
        })
        if not isinstance(response, dict):
            raise OutboxProtocolError("OUTBOX_RESERVE_PROTOCOL_INVALID")
        status = response.get("status")
        if status == "IN_FLIGHT":
            return "IN_FLIGHT", None
        if status != "RESERVED":
            return "RESERVE_REJECTED", None
        if (response.get("incident") != proposal.incident
                or response.get("intent_id") != nonce
                or response.get("main_sha") != proposal.main_sha
                or response.get("action") != proposal.action
                or response.get("pr_number") != proposal.pr_number
                or response.get("pr_head_sha") != proposal.pr_head_sha
                or type(response.get("epoch")) is not int
                or response["epoch"] != self.claim.epoch):
            raise OutboxProtocolError("OUTBOX_RESERVATION_CONTRADICTION")
        return "RESERVED", Reservation(
            incident=proposal.incident, intent_id=nonce,
            main_sha=proposal.main_sha, action=proposal.action,
            pr_number=proposal.pr_number, pr_head_sha=proposal.pr_head_sha,
            epoch=self.claim.epoch,
        )

    def _check_reservation(self, r: Reservation) -> None:
        if not isinstance(r, Reservation) or r.incident != self.claim.incident or (
            type(r.epoch) is not int or r.epoch != self.claim.epoch
        ):
            raise OutboxProtocolError("OUTBOX_WRITER_EPOCH_MISMATCH")

    def abort_unarmed(self, reservation: Reservation, reason: str) -> bool:
        self._check_reservation(reservation)
        if not isinstance(reason, str) or reason not in _PREARM_REASONS:
            raise OutboxProtocolError("OUTBOX_ABORT_REASON_INVALID")
        response = self._rpc("abort", {
            "p_incident_id": reservation.incident,
            "p_intent_id": reservation.intent_id,
            "p_owner_id": self.claim.owner_id,
            "p_epoch": self.claim.epoch,
            "p_reason": reason,
        })
        if type(response) is not bool:
            raise OutboxProtocolError("OUTBOX_ABORT_PROTOCOL_INVALID")
        return response

    def arm_once(self, reservation: Reservation) -> bool:
        self._check_reservation(reservation)
        # DB RPC enforces its own writer fence atomically before CAS arming.
        result = self._rpc("arm", {
            "p_incident_id": reservation.incident,
            "p_intent_id": reservation.intent_id,
            "p_owner_id": self.claim.owner_id,
            "p_epoch": self.claim.epoch,
        })
        if type(result) is not bool:
            raise OutboxProtocolError("OUTBOX_ARM_PROTOCOL_INVALID")
        return result

    def mark_result(self, reservation: Reservation, code: str,
                    workflow_run_id: int | None) -> bool:
        self._check_reservation(reservation)
        if code not in {
            "ATTEMPT_ACK_UNCORRELATED", "ATTEMPT_ACK_CORRELATED_UNVERIFIED",
            "ATTEMPT_AMBIGUOUS",
        }:
            raise OutboxProtocolError("OUTBOX_RESULT_CODE_INVALID")
        if code == "ATTEMPT_ACK_CORRELATED_UNVERIFIED":
            if type(workflow_run_id) is not int or workflow_run_id <= 0:
                raise OutboxProtocolError("OUTBOX_RESULT_RUN_ID_INVALID")
        elif workflow_run_id is not None:
            raise OutboxProtocolError("OUTBOX_RESULT_RUN_ID_CONTRADICTION")
        response = self._rpc("mark", {
            "p_incident_id": reservation.incident,
            "p_intent_id": reservation.intent_id,
            "p_owner_id": self.claim.owner_id,
            "p_epoch": self.claim.epoch,
            "p_receipt_code": code,
            "p_run_id": workflow_run_id,
        })
        if type(response) is not bool:
            raise OutboxProtocolError("OUTBOX_MARK_PROTOCOL_INVALID")
        return response


__all__ = ["PostgrestOutboxStore", "OutboxProtocolError"]
