"""Opt-in REAL PostgreSQL regression suite for an attested isolated test DB.

NEVER point at production. Requires a separately provisioned ephemeral Supabase
test branch/database, pre-applied review migration and explicit two-part opt-in.
Does not create schemas, alter grants, truncate, delete, deploy, or trade.
"""
import importlib.util
import os
import secrets
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

DSN = os.environ.get("WOW_RELEASE_ISOLATED_TEST_DSN")
ATTESTED = os.environ.get("WOW_RELEASE_ISOLATED_TEST_ATTESTATION") == "NONPRODUCTION_EPHEMERAL_ONLY"
if not DSN or not ATTESTED:
    pytest.skip("Isolated PostgreSQL test database not attested/configured", allow_module_level=True)

psycopg = pytest.importorskip("psycopg")
from psycopg.conninfo import conninfo_to_dict

# The opt-in string alone is NOT a database attestation. For this candidate,
# accept only an isolated local PostgreSQL test container. A Supabase branch
# must obtain a separately reviewed identity/provenance verifier.
conninfo = conninfo_to_dict(DSN)
if conninfo.get("host") not in {"127.0.0.1", "localhost", "::1"} or (
    conninfo.get("dbname") != "wow_release_fence_test"
):
    raise RuntimeError(
        "RELEASE_ISOLATED_DB_IDENTITY_UNVERIFIED: no remote or production DSN allowed"
    )
SOURCE = Path(__file__).resolve().parents[1] / "v17" / "render_release_service_fence_candidate.py"
spec = importlib.util.spec_from_file_location("render_release_service_fence_candidate_integration", SOURCE)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def connection():
    return psycopg.connect(DSN, autocommit=True, connect_timeout=10)


@pytest.fixture(scope="module", autouse=True)
def require_preprovisioned_isolated_schema():
    with connection() as db:
        objects = db.execute("""SELECT
          to_regclass('wow_release_control.service_fence') IS NOT NULL,
          to_regclass('wow_release_control.release_intent') IS NOT NULL
        """).fetchone()
        assert all(objects), "Pre-provision the schema in a NONPRODUCTION ephemeral database"


def new_service():
    return "srv-" + secrets.token_hex(12)


def new_sha():
    return secrets.token_hex(20)


def claim_once(service, sha, run, barrier):
    with connection() as db:
        barrier.wait(timeout=15)
        try:
            lease = m.ReleaseFence(db).claim(service, sha, run, run)
            return ("CLAIMED", lease)
        except m.ReleaseHold as exc:
            return (str(exc), None)


def test_two_connections_only_one_service_writer():
    service, sha = new_service(), new_sha()
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(claim_once, service, sha, "worker-1", barrier)
        b = pool.submit(claim_once, service, new_sha(), "worker-2", barrier)
        results = [a.result(timeout=20), b.result(timeout=20)]
    assert sum(status == "CLAIMED" for status, _ in results) == 1
    assert sum(status != "CLAIMED" for status, _ in results) == 1
    with connection() as db:
        assert db.execute("""SELECT COUNT(*) FROM wow_release_control.release_intent
                             WHERE service_id=%s""", (service,)).fetchone()[0] == 1


@pytest.mark.parametrize("state", ["RESERVED", "POST_UNCERTAIN", "PROVIDER_ACK", "LIVE_OBSERVED"])
def test_expired_owner_lease_cannot_discard_previous_intent(state):
    service, sha = new_service(), new_sha()
    with connection() as db:
        fence = m.ReleaseFence(db)
        lease = fence.claim(service, sha, "orig-run", "orig-owner")
        if state != "RESERVED":
            fence.reserve_single_post(lease)
        if state in {"PROVIDER_ACK", "LIVE_OBSERVED"}:
            deploy_id = "dep-" + secrets.token_hex(12)
            db.execute("""UPDATE wow_release_control.release_intent
                          SET state='PROVIDER_ACK', provider_deploy_id=%s
                          WHERE service_id=%s AND target_sha=%s""", (deploy_id, service, sha))
            if state == "LIVE_OBSERVED":
                db.execute("""UPDATE wow_release_control.release_intent
                              SET state='LIVE_OBSERVED'
                              WHERE service_id=%s AND target_sha=%s""", (service, sha))
        db.execute("""UPDATE wow_release_control.service_fence
                      SET lease_expires_at=clock_timestamp()-interval '1 minute'
                      WHERE service_id=%s""", (service,))
    with connection() as contender:
        with pytest.raises(m.ReleaseHold, match="RELEASE_PREVIOUS_INTENT_UNRESOLVED_NO_REPOST"):
            m.ReleaseFence(contender).claim(service, new_sha(), "next-run", "next-owner")


def test_ambiguous_render_post_never_gets_retried_even_after_expiry():
    service, sha = new_service(), new_sha()
    sent = []
    with connection() as db:
        fence = m.ReleaseFence(db)
        lease = fence.claim(service, sha, "first-run", "first-owner")
        def provider(payload):
            sent.append(payload)
            raise TimeoutError("Provider accepted but response was lost")
        with pytest.raises(m.ReleaseHold, match="RELEASE_PROVIDER_POST_ACK_UNKNOWN_RECONCILE_ONLY"):
            m.invoke_single_render_post(lease, fence, provider)
        db.execute("""UPDATE wow_release_control.service_fence
                      SET lease_expires_at=clock_timestamp()-interval '1 minute'
                      WHERE service_id=%s""", (service,))
    with connection() as contender:
        with pytest.raises(m.ReleaseHold, match="RELEASE_PREVIOUS_INTENT_UNRESOLVED_NO_REPOST"):
            m.ReleaseFence(contender).claim(service, sha, "second-run", "second-owner")
    assert len(sent) == 1


def test_immutable_intent_identity_rejects_sql_rewrite():
    service, sha = new_service(), new_sha()
    with connection() as db:
        m.ReleaseFence(db).claim(service, sha, "run1", "owner1")
        with pytest.raises(psycopg.Error):
            db.execute("""UPDATE wow_release_control.release_intent SET target_sha=%s
                          WHERE service_id=%s AND target_sha=%s""", (new_sha(), service, sha))


def test_anon_and_authenticated_have_no_release_table_write_permission():
    with connection() as db:
        rows = db.execute("""SELECT
           has_table_privilege('anon','wow_release_control.release_intent','INSERT'),
           has_table_privilege('authenticated','wow_release_control.release_intent','UPDATE')
        """).fetchone()
    assert rows == (False, False)
