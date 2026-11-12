"""Isolated PostgreSQL observer/recovery authorization tests.

All calls are against the GitHub-hosted ephemeral test PostgreSQL instance.
Test-role impersonation is done only by its local CI superuser.
No production Supabase, Render, deployments or wagers.
"""
import os
import secrets
from datetime import timedelta
import pytest

dsn=os.environ.get("WOW_RELEASE_ISOLATED_TEST_DSN")
if not dsn or os.environ.get("WOW_RELEASE_ISOLATED_TEST_ATTESTATION")!="NONPRODUCTION_EPHEMERAL_ONLY":
    pytest.skip("No isolated test database",allow_module_level=True)
psycopg=pytest.importorskip("psycopg")
from psycopg.conninfo import conninfo_to_dict
params=conninfo_to_dict(dsn)
if params.get("host") not in {"localhost","127.0.0.1","::1"} or params.get("dbname")!="wow_release_fence_test":
    raise RuntimeError("RELEASE_TEST_REMOTE_DATABASE_FORBIDDEN")


def db(role=None):
    connection=psycopg.connect(dsn,autocommit=True)
    if role:
        connection.execute("SET SESSION AUTHORIZATION "+role)
        assert connection.execute("SELECT session_user").fetchone()[0]==role
    return connection


def identities():
    return "srv-"+secrets.token_hex(12),secrets.token_hex(20),"dep-"+secrets.token_hex(12)


def provision_post():
    service,sha,deploy=identities()
    with db("wow_release_controller") as controller:
        fence=controller.execute(
            "SELECT lease_fence FROM wow_release_control.reserve_release(%s,%s,%s,%s)",
            (service,sha,"run-observe",180)).fetchone()[0]
        assert controller.execute(
            "SELECT wow_release_control.reserve_render_post(%s,%s,%s,%s)",
            (service,sha,"run-observe",fence)).fetchone()[0] is True
    with db() as admin:
        after=admin.execute(
            "SELECT post_reserved_at + interval '1 second' FROM wow_release_control.release_intent WHERE service_id=%s",
            (service,)).fetchone()[0]
    return service,sha,deploy,fence,after


def ack(observer,service,sha,deploy,fence,after,complete=True):
    return observer.execute(
        "SELECT wow_release_control.record_provider_ack(%s,%s,%s,%s,%s,%s,%s)",
        (service,sha,"run-observe",fence,deploy,after,complete)).fetchone()[0]


def live(observer,service,sha,deploy,fence,main_sha=None,exhaustive=True):
    return observer.execute(
        "SELECT wow_release_control.record_live_observation(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (service,sha,"run-observe",fence,deploy,sha,main_sha or sha,"live",exhaustive)).fetchone()[0]


def test_expired_lease_allows_only_separate_observer_not_new_deploy():
    service,sha,deploy,fence,after=provision_post()
    with db() as admin:
        admin.execute("UPDATE wow_release_control.service_fence SET lease_expires_at=clock_timestamp()-interval '1 hour' WHERE service_id=%s",(service,))
    with db("wow_release_controller") as controller:
        with pytest.raises(psycopg.Error,match="RELEASE_PREVIOUS_INTENT_UNRESOLVED_NO_REPOST"):
            controller.execute("SELECT * FROM wow_release_control.reserve_release(%s,%s,%s,%s)",
                (service,secrets.token_hex(20),"newrun",180)).fetchall()
    with db("wow_release_observer") as observer:
        assert ack(observer,service,sha,deploy,fence,after)=="PROVIDER_ACK"
        assert live(observer,service,sha,deploy,fence)=="LIVE_OBSERVED"
    with db() as admin:
        assert admin.execute("SELECT state FROM wow_release_control.release_intent WHERE service_id=%s",(service,)).fetchone()[0]=="LIVE_OBSERVED"


def test_reconciliation_refuses_missing_unique_provider_evidence():
    service,sha,deploy,fence,after=provision_post()
    with db("wow_release_observer") as observer:
        with pytest.raises(psycopg.Error,match="RELEASE_PROVIDER_OBSERVATION_INCOMPLETE"):
            ack(observer,service,sha,deploy,fence,after,False)
    with db() as admin:
        assert admin.execute("SELECT state FROM wow_release_control.release_intent WHERE service_id=%s",(service,)).fetchone()[0]=="POST_UNCERTAIN"


def test_provider_identity_bound_to_deployment_created_after_reserved():
    service,sha,deploy,fence,after=provision_post()
    with db("wow_release_observer") as observer:
        with pytest.raises(psycopg.Error,match="RELEASE_PROVIDER_ACK_IDENTITY_OR_STATE_HOLD"):
            ack(observer,service,sha,deploy,fence,after-timedelta(days=2))


def test_observer_cannot_repost_or_verify():
    service,sha,deploy,fence,after=provision_post()
    with db("wow_release_observer") as observer:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            observer.execute("SELECT * FROM wow_release_control.reserve_release(%s,%s,%s,%s)",
                             (service,sha,"observer-forged",180))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            observer.execute("""SELECT wow_release_control.independent_verify_release(
              %s,%s,%s,%s,%s,%s,true,true,true)""",
              (service,sha,"run-observe",fence,deploy,"a"*64))


def test_controller_cannot_claim_observer_evidence():
    service,sha,deploy,fence,after=provision_post()
    with db("wow_release_controller") as controller:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            ack(controller,service,sha,deploy,fence,after)


def test_observer_rejects_stale_main_and_incomplete_live_state():
    service,sha,deploy,fence,after=provision_post()
    with db("wow_release_observer") as observer:
        assert ack(observer,service,sha,deploy,fence,after)=="PROVIDER_ACK"
        with pytest.raises(psycopg.Error,match="RELEASE_LIVE_EVIDENCE_INCOMPLETE_OR_STALE"):
            live(observer,service,sha,deploy,fence,main_sha="f"*40)
        with pytest.raises(psycopg.Error,match="RELEASE_LIVE_EVIDENCE_INCOMPLETE_OR_STALE"):
            live(observer,service,sha,deploy,fence,exhaustive=False)
        assert live(observer,service,sha,deploy,fence)=="LIVE_OBSERVED"


def test_replayed_provider_ack_never_repeats_transition():
    service,sha,deploy,fence,after=provision_post()
    with db("wow_release_observer") as observer:
        assert ack(observer,service,sha,deploy,fence,after)=="PROVIDER_ACK"
        with pytest.raises(psycopg.Error,match="RELEASE_PROVIDER_ACK_IDENTITY_OR_STATE_HOLD"):
            ack(observer,service,sha,deploy,fence,after)


def test_forged_verification_flags_always_fail_closed():
    service,sha,deploy,fence,after=provision_post()
    with db("wow_release_observer") as observer:
        assert ack(observer,service,sha,deploy,fence,after)=="PROVIDER_ACK"
        assert live(observer,service,sha,deploy,fence)=="LIVE_OBSERVED"
    with db("wow_release_verifier") as verifier:
        with pytest.raises(psycopg.Error,match="RELEASE_VERIFICATION_SIGNED_ATTESTATION_NOT_CERTIFIED_HOLD"):
            verifier.execute(
                "SELECT wow_release_control.independent_verify_release(%s,%s,%s,%s,%s,%s,true,true,true)",
                (service,sha,"run-observe",fence,deploy,"a"*64))
    with db() as admin:
        assert admin.execute("SELECT state,verified_at FROM wow_release_control.release_intent WHERE service_id=%s",(service,)).fetchone()==("LIVE_OBSERVED",None)


def test_verifier_cannot_write_table_or_submit_deployment_post():
    service,sha,deploy,fence,after=provision_post()
    with db("wow_release_verifier") as verifier:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            verifier.execute("UPDATE wow_release_control.release_intent SET state='VERIFIED' WHERE service_id=%s",(service,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            verifier.execute("SELECT wow_release_control.reserve_render_post(%s,%s,%s,%s)",(service,sha,"run-observe",fence))


def test_no_public_or_authenticated_executes_reconciliation_rpcs():
    with db() as admin:
        for fn in ("record_provider_ack(text,text,text,bigint,text,timestamp with time zone,boolean)",
                   "record_live_observation(text,text,text,bigint,text,text,text,text,boolean)",
                   "independent_verify_release(text,text,text,bigint,text,text,boolean,boolean,boolean)"):
            for role in ("anon","authenticated"):
                assert admin.execute(
                    "SELECT has_function_privilege(%s,%s,'EXECUTE')",
                    (role,"wow_release_control."+fn)).fetchone()==(False,)


def test_observer_and_verifier_have_no_direct_dml_grants():
    with db() as admin:
        for role in ("wow_release_observer","wow_release_verifier"):
            for table in ("service_fence","release_intent"):
                for op in ("INSERT","UPDATE","DELETE"):
                    assert admin.execute(
                        "SELECT has_table_privilege(%s,%s,%s)",
                        (role,"wow_release_control."+table,op)).fetchone()==(False,)
