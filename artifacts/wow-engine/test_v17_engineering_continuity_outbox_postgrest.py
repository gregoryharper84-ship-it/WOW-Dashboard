"""Deterministic no-network tests for review-only outbox PostgREST binding."""
from __future__ import annotations

import json
from pathlib import Path
import pytest
from v17.engineering_continuity_outbox import (
    Proposal, Reservation, dispatch_once,
)
from v17.engineering_continuity_lease_attestation import WriterClaim
from v17.engineering_continuity_outbox_postgrest import (
    PostgrestOutboxStore, OutboxProtocolError,
)

SHA = "a" * 40
CLAIM = WriterClaim(1739, "github-actions:38744867262:1:trusted-worker", 4)
NONCE = "12345678-1234-4234-8234-123456789abc"
PROPOSAL = Proposal(1739, SHA, "DISPATCH_NEW_REPAIR")
RESERVED = {
    "status": "RESERVED", "incident": 1739, "intent_id": NONCE,
    "main_sha": SHA, "action": "DISPATCH_NEW_REPAIR",
    "pr_number": None, "pr_head_sha": None, "epoch": 4,
}


class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, n):
        assert n > 0
        return self.body[:n]


class RPCTransport:
    def __init__(self, *payloads):
        self.values = [json.dumps(p).encode("utf-8") for p in payloads]
        self.urls = []
        self.request_bodies = []

    def __call__(self, request, timeout):
        assert timeout in (6, 8)
        self.urls.append(request.full_url)
        self.request_bodies.append(json.loads(request.data))
        if not self.values:
            raise AssertionError("extra unexpected POST")
        return Response(self.values.pop(0))


def store(t):
    return PostgrestOutboxStore(
        claim=CLAIM, endpoint="https://example.supabase.co",
        credential="fixture-key-never-print", transport=t,
        new_intent_id=lambda: NONCE,
    )


def test_reserve_arm_mark_follow_only_fixed_rpc_endpoints():
    t = RPCTransport(True, RESERVED, True, True)
    db = store(t)
    state, r = db.reserve(PROPOSAL)
    assert state == "RESERVED" and isinstance(r, Reservation)
    assert db.arm_once(r) is True
    assert db.mark_result(r, "ATTEMPT_ACK_UNCORRELATED", None) is True
    assert [u.rsplit("/", 1)[-1] for u in t.urls] == [
        "wow_engineering_check_writer_lease",
        "wow_engineering_continuity_outbox_reserve",
        "wow_engineering_continuity_outbox_arm",
        "wow_engineering_continuity_outbox_mark",
    ]
    assert all("fixture-key" not in str(p) for p in t.request_bodies)


def test_lease_denied_never_calls_mutating_rpc():
    t = RPCTransport(False)
    status, result = store(t).reserve(PROPOSAL)
    assert (status, result) == ("LEASE_NOT_VERIFIED", None)
    assert len(t.urls) == 1 and t.urls[0].endswith("check_writer_lease")


def test_incident_mismatch_denied_before_attestation():
    t = RPCTransport()
    with pytest.raises(OutboxProtocolError, match="OUTBOX_INCIDENT_MISMATCH"):
        store(t).reserve(Proposal(823, SHA, "DISPATCH_NEW_REPAIR"))
    assert t.urls == []


def test_ambiguous_first_dispatch_holds_second_cycle():
    t = RPCTransport(True, RESERVED, True, True, True, {"status": "IN_FLIGHT"})
    db = store(t)
    calls = []
    def send(p, r):
        calls.append(p.incident)
        return None  # GitHub workflow_dispatch 204 gives no unique run ID.
    outcome = dispatch_once(
        PROPOSAL, db, send, writer_fence=lambda r: True,
        current_main=lambda: SHA,
    )
    assert outcome.code == "ATTEMPT_ACK_UNCORRELATED" and outcome.attempted
    again = dispatch_once(
        PROPOSAL, db, send, writer_fence=lambda r: True,
        current_main=lambda: SHA,
    )
    assert again.code == "PRIOR_DISPATCH_REQUIRES_RECONCILIATION"
    assert calls == [1739]


def test_rejected_schema_reserve_never_arms():
    t = RPCTransport(True, {"status": "INVALID_REQUEST"})
    state, r = store(t).reserve(PROPOSAL)
    assert state == "RESERVE_REJECTED" and r is None
    assert len(t.urls) == 2


def test_reserve_identity_not_trusted_if_server_mismatch():
    altered = {**RESERVED, "epoch": 8}
    t = RPCTransport(True, altered)
    with pytest.raises(OutboxProtocolError, match="OUTBOX_RESERVATION_CONTRADICTION"):
        store(t).reserve(PROPOSAL)


def test_mark_correlated_requires_positive_int_run_id():
    t = RPCTransport()
    db = store(t)
    r = Reservation(1739, NONCE, SHA, "DISPATCH_NEW_REPAIR", None, None, 4)
    with pytest.raises(OutboxProtocolError, match="OUTBOX_RESULT_RUN_ID_INVALID"):
        db.mark_result(r, "ATTEMPT_ACK_CORRELATED_UNVERIFIED", True)
    with pytest.raises(OutboxProtocolError, match="OUTBOX_RESULT_RUN_ID_CONTRADICTION"):
        db.mark_result(r, "ATTEMPT_AMBIGUOUS", 5)
    assert t.urls == []


def test_wrong_epoch_blocks_arm_before_rpc():
    t = RPCTransport()
    db = store(t)
    r = Reservation(1739, NONCE, SHA, "DISPATCH_NEW_REPAIR", None, None, 99)
    with pytest.raises(OutboxProtocolError, match="OUTBOX_WRITER_EPOCH_MISMATCH"):
        db.arm_once(r)
    assert t.urls == []


def test_sql_candidate_has_rls_service_role_rpc_and_no_reset_procedure():
    sql = (Path(__file__).parent.parent.parent / "docs/wow/engineering/"
           "proposals/WOW_CONTINUITY_OUTBOX_REVIEW_ONLY.sql").read_text()
    assert "enable row level security" in sql
    assert "grant execute on function" in sql
    assert "to service_role" in sql
    assert "from public, anon, authenticated" in sql
    assert "check_writer_lease" in sql
    assert "on conflict (incident_id) do nothing" in sql
    assert "state='RESERVED'" in sql
    assert "where incident_id=p_incident_id and intent_id=p_intent_id" in sql
    assert "truncate public.wow_engineering_continuity_outbox" not in sql.lower()


def test_outbox_sql_is_single_nonduplicated_closed_contract():
    """Protect against source-string substitution corrupting SQL and duplicating blocks."""
    sql_path = (Path(__file__).parent.parent.parent / "docs/wow/engineering/"
                "proposals/WOW_CONTINUITY_OUTBOX_REVIEW_ONLY.sql")
    sql = sql_path.read_text()
    assert sql.count("create table if not exists public.wow_engineering_continuity_outbox (") == 1
    assert sql.count("create or replace function public.wow_engineering_continuity_outbox_reserve(") == 1
    assert sql.count("create or replace function public.wow_engineering_continuity_outbox_arm(") == 1
    assert sql.count("create or replace function public.wow_engineering_continuity_outbox_mark(") == 1
    assert sql.count("end $$;") == 3
    assert "and pr_head_sha ~ '^[0-9a-f]{40}$')" in sql
    assert "and p_pr_head_sha ~ '^[0-9a-f]{40}$')" in sql
    # Never allow regex literals to be split across lines, or a duplicate
    # unintended SQL suffix to be appended after the explicit review footer.
    assert "~ '^[0-9a-f]{40}\n" not in sql
    assert sql.rstrip().endswith(
        "-- a separate independently reviewed reconciliation design must be certified."
    )
    assert "TRUNCATE public.wow_engineering_continuity_outbox" not in sql
