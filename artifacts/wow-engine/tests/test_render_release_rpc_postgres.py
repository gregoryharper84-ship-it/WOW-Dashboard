"""Real, isolated PostgreSQL RBAC/RPC tests. Never target remote databases."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import os
import secrets

import pytest

dsn = os.environ.get("WOW_RELEASE_ISOLATED_TEST_DSN")
if not dsn or os.environ.get("WOW_RELEASE_ISOLATED_TEST_ATTESTATION") != "NONPRODUCTION_EPHEMERAL_ONLY":
    pytest.skip("No isolated release-fence test DB", allow_module_level=True)

psycopg = pytest.importorskip("psycopg")
from psycopg.conninfo import conninfo_to_dict

ci = conninfo_to_dict(dsn)
if ci.get("host") not in {"localhost", "127.0.0.1", "::1"} or ci.get("dbname") != "wow_release_fence_test":
    raise RuntimeError("RELEASE_RPC_TEST_REMOTE_DATABASE_FORBIDDEN")


def admin():
    return psycopg.connect(dsn, autocommit=True)


def controller():
    conn = admin()
    # Allowed only in ephemeral CI where superuser creates a NOLOGIN test role.
    conn.execute("SET SESSION AUTHORIZATION wow_release_controller")
    assert conn.execute("SELECT session_user").fetchone()[0] == "wow_release_controller"
    return conn


def ids():
    return "srv-" + secrets.token_hex(12), secrets.token_hex(20)


def reserve(conn, service, sha, run="run1"):
    return conn.execute(
        "SELECT * FROM wow_release_control.reserve_release(%s,%s,%s,%s)",
        (service, sha, run, 180),
    ).fetchone()


def reserve_post(conn, service, sha, run, fence):
    return conn.execute(
        "SELECT wow_release_control.reserve_render_post(%s,%s,%s,%s)",
        (service, sha, run, fence),
    ).fetchone()[0]


def test_controller_can_reserve_without_any_direct_table_dml():
    service, sha = ids()
    with controller() as c:
        assert reserve(c, service, sha)[1] == "RESERVED"
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("""INSERT INTO wow_release_control.service_fence(service_id)
                         VALUES (%s)""", ("srv-" + secrets.token_hex(12),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("""UPDATE wow_release_control.release_intent SET state='VERIFIED'
                         WHERE service_id=%s""", (service,))
    with admin() as a:
        assert a.execute("""SELECT owner,state FROM wow_release_control.release_intent
                            WHERE service_id=%s AND target_sha=%s""",
                         (service, sha)).fetchone() == ("wow_release_controller", "RESERVED")


def test_restricted_rpc_prepare_post_is_one_shot_even_after_ack_loss():
    service, sha = ids()
    with controller() as c:
        fence = reserve(c, service, sha, "run1")[0]
        assert reserve_post(c, service, sha, "run1", fence) is True
        with pytest.raises(psycopg.Error, match="RELEASE_POST_ALREADY_RESERVED_OR_FENCED"):
            reserve_post(c, service, sha, "run1", fence)
    with admin() as a:
        assert a.execute("""SELECT state FROM wow_release_control.release_intent
                            WHERE service_id=%s AND target_sha=%s""",
                         (service, sha)).fetchone()[0] == "POST_UNCERTAIN"


def test_expired_lease_with_unresolved_intent_blocks_other_sha():
    service, sha = ids()
    with controller() as c:
        reserve(c, service, sha)
    with admin() as a:
        a.execute("""UPDATE wow_release_control.service_fence
                     SET lease_expires_at=clock_timestamp()-interval '2 minutes'
                     WHERE service_id=%s""", (service,))
    with controller() as c:
        with pytest.raises(psycopg.Error, match="RELEASE_PREVIOUS_INTENT_UNRESOLVED_NO_REPOST"):
            reserve(c, service, secrets.token_hex(20), "run2")


def test_wrong_run_fence_and_sha_never_reserve_post():
    service, sha = ids()
    with controller() as c:
        fence = reserve(c, service, sha)[0]
        for wrong_sha,wrong_run,wrong_fence in [
            (sha, "run-other", fence),
            (sha, "run1", fence+1),
            (secrets.token_hex(20), "run1", fence),
        ]:
            with pytest.raises(psycopg.Error, match="RELEASE_POST_ALREADY_RESERVED_OR_FENCED"):
                reserve_post(c, service, wrong_sha, wrong_run, wrong_fence)


def test_cannot_use_set_role_to_fake_authenticated_session_identity():
    service, sha = ids()
    with admin() as c:
        c.execute("SET ROLE wow_release_controller")
        with pytest.raises(psycopg.Error, match="RELEASE_RPC_CALLER_IDENTITY_UNVERIFIED"):
            reserve(c, service, sha)


def test_anon_and_authenticated_cannot_invoke_private_rpc():
    with admin() as a:
        for role in ["anon","authenticated"]:
            assert a.execute("""SELECT has_function_privilege(%s,
              'wow_release_control.reserve_release(text,text,text,integer)','EXECUTE')""",
              (role,)).fetchone() == (False,)
        assert a.execute("""SELECT has_table_privilege('wow_release_controller',
                 'wow_release_control.release_intent','INSERT')""").fetchone() == (False,)
        assert a.execute("""SELECT has_table_privilege('wow_release_controller',
                 'wow_release_control.service_fence','UPDATE')""").fetchone() == (False,)


def test_postgres_function_owner_is_nonlogin_not_superuser():
    with admin() as a:
        for proc in ["reserve_release","reserve_render_post","renew_release_lease"]:
            row = a.execute("""SELECT r.rolname,r.rolsuper,r.rolcanlogin
                        FROM pg_proc p JOIN pg_roles r ON p.proowner=r.oid
                        WHERE p.pronamespace='wow_release_control'::regnamespace
                        AND p.proname=%s""",(proc,)).fetchone()
            assert row == ("wow_release_fence_owner",False,False)


def test_real_two_connection_rpc_claim_race():
    service,sha=ids()
    gate=Barrier(2)
    def attempt(index):
        with controller() as c:
            gate.wait(timeout=12)
            try:
                return ("OK",reserve(c,service,sha if index==0 else secrets.token_hex(20),f"run{index}"))
            except psycopg.Error:
                return ("HOLD",None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(attempt,[0,1]))
    assert [x[0] for x in results].count("OK")==1
    assert [x[0] for x in results].count("HOLD")==1
    with admin() as a:
        assert a.execute("""SELECT COUNT(*) FROM wow_release_control.release_intent
                     WHERE service_id=%s""",(service,)).fetchone()[0] == 1


def test_invalid_target_sha_cannot_reserve():
    with controller() as c:
        with pytest.raises(psycopg.Error, match="RELEASE_RPC_INPUT_INVALID"):
            reserve(c,"srv-" + secrets.token_hex(12),"invalid")


def test_authenticated_database_login_cannot_bypass_release_table_privileges():
    """Real SCRAM-authenticated CI login, NOT root SET SESSION AUTHORIZATION.

    This verifies EXECUTE-only runtime permissions but is NOT evidence of
    trusted GitHub Actions -> production database workload identity.
    """
    from psycopg.conninfo import make_conninfo
    login_dsn = make_conninfo(
        dsn,
        user="wow_release_controller",
        password="CI_FENCE_EPHEMERAL_ONLY",
    )
    service, sha = ids()
    with psycopg.connect(login_dsn, autocommit=True, connect_timeout=5) as c:
        assert c.execute("SELECT session_user, current_user").fetchone() == (
            "wow_release_controller", "wow_release_controller"
        )
        assert reserve(c, service, sha, "authenticated-ci-run")[1] == "RESERVED"
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("UPDATE wow_release_control.release_intent SET state='VERIFIED'")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SET ROLE wow_release_fence_owner")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM wow_release_control.release_intent")
    with admin() as c:
        assert c.execute(
            "SELECT owner, state FROM wow_release_control.release_intent WHERE service_id=%s",
            (service,),
        ).fetchone() == ("wow_release_controller", "RESERVED")


def test_privileged_definer_owner_cannot_login():
    """Function-owner NOLOGIN remains separate from authenticated controller."""
    from psycopg.conninfo import make_conninfo
    with pytest.raises(psycopg.Error):
        psycopg.connect(
            make_conninfo(
                dsn,
                user="wow_release_fence_owner",
                password="CI_FENCE_EPHEMERAL_ONLY",
            ),
            connect_timeout=3,
        )
