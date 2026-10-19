"""#1851 observational checks stay fail-closed on the #1834 controller."""
from v17.production_release_admission import (
    COMMITTED_APPLICATION_DATABASE_PROJECT_REF,
    admit_production_release,
    classify_role_checks,
    database_identity_decision,
    project_ref_from_supabase_url,
    writer_lock_observation,
)


HEAD = "a" * 40
SHA = "b" * 40


def _check(name, app_id, status="completed", conclusion="success", check_id=1):
    return {
        "id": check_id,
        "name": name,
        "head_sha": HEAD,
        "app": {"id": app_id},
        "status": status,
        "conclusion": conclusion,
    }


def test_pending_exact_head_role_check_waits_and_failure_does_not():
    pending = classify_role_checks(
        [_check("WOW Independent QA exact-head", 111, status="in_progress", conclusion=None)],
        qa_app_id="111",
        release_app_id="222",
        source_head=HEAD,
    )
    failed = classify_role_checks(
        [
            _check("WOW Independent QA exact-head", 111, conclusion="failure", check_id=9),
            _check("WOW Release Authority exact-head", 222),
        ],
        qa_app_id="111",
        release_app_id="222",
        source_head=HEAD,
    )
    assert pending == "PENDING"
    assert failed == "RENDER_RELEASE_ROLE_CHECK_NOT_SUCCESS:WOW Independent QA exact-head"


def test_unbound_or_identical_app_identities_fail_closed():
    assert classify_role_checks([], qa_app_id="", release_app_id="222", source_head=HEAD).endswith("UNBOUND")
    assert "NOT_DISTINCT" in classify_role_checks([], qa_app_id="111", release_app_id="111", source_head=HEAD)


def test_validation_project_match_does_not_certify_release_control_or_authorize_deploy():
    observed = project_ref_from_supabase_url(
        f"https://{COMMITTED_APPLICATION_DATABASE_PROJECT_REF}.supabase.co"
    )
    decision = database_identity_decision(
        observed_project_ref=observed,
        sealed_project_ref=COMMITTED_APPLICATION_DATABASE_PROJECT_REF,
        database_provider="SUPABASE",
        service_id="srv-da7sa9gu01pc73brt80g",
        can_execute=False,
    )
    assert decision["application_project_matches"] is True
    assert decision["passed"] is False
    assert decision["release_control_schema_certified"] is False
    assert decision["deploy_authorized"] is False
    assert "RELEASE_CONTROL_SCHEMA_NOT_CERTIFIED" in decision["blockers"]
    assert decision["can_execute"] is False


def test_database_mismatch_and_unreadable_identity_add_blockers():
    mismatch = database_identity_decision(
        observed_project_ref="a" * 20,
        sealed_project_ref=COMMITTED_APPLICATION_DATABASE_PROJECT_REF,
        database_provider="SUPABASE",
        service_id="srv-da7sa9gu01pc73brt80g",
    )
    unreadable = database_identity_decision(
        observed_project_ref="",
        sealed_project_ref=COMMITTED_APPLICATION_DATABASE_PROJECT_REF,
        database_provider="OTHER",
        service_id="srv-other",
        can_execute=True,
    )
    assert "RELEASE_DATABASE_IDENTITY_MISMATCH" in mismatch["blockers"]
    assert "RELEASE_DATABASE_IDENTITY_UNREADABLE" in unreadable["blockers"]
    assert "RELEASE_DATABASE_CAN_EXECUTE_UNSAFE" in unreadable["blockers"]
    assert unreadable["deploy_authorized"] is False


def test_clear_render_deploy_list_is_not_a_durable_single_post_fence():
    quiet = writer_lock_observation(
        auto_deploy_trigger="off",
        deploys=[{"status": "live", "commit": {"id": "c" * 40}}],
        target_sha=SHA,
    )
    busy = writer_lock_observation(
        auto_deploy_trigger="off",
        deploys=[{"status": "build_in_progress", "commit": {"id": "d" * 40}}],
        target_sha=SHA,
    )
    assert quiet["post_allowed"] is False
    assert quiet["durable_fence"] is False
    assert "RENDER_DEPLOY_LIST_SNAPSHOT_IS_NOT_DURABLE_FENCE" in quiet["blockers"]
    assert "RELEASE_WRITER_LOCK_BUSY" in busy["blockers"]
    assert busy["post_allowed"] is False


def test_admission_stays_a_nonadvancing_hold_when_role_checks_pass():
    identity = database_identity_decision(
        observed_project_ref=COMMITTED_APPLICATION_DATABASE_PROJECT_REF,
        sealed_project_ref=COMMITTED_APPLICATION_DATABASE_PROJECT_REF,
        database_provider="SUPABASE",
        service_id="srv-da7sa9gu01pc73brt80g",
    )
    lock = writer_lock_observation(auto_deploy_trigger="off", deploys=[], target_sha=SHA)
    admission = admit_production_release(role_checks="PASS", database_identity=identity, writer_lock=lock)
    assert admission["eligible"] is False
    assert admission["status"] == "PRODUCTION_RELEASE_HOLD"
    assert admission["post_allowed"] is False
    assert admission["deploy_authorized"] is False
    assert admission["can_execute"] is False
    assert "RELEASE_CONTROL_SCHEMA_NOT_CERTIFIED" in admission["blockers"]
    assert "RENDER_DEPLOY_LIST_SNAPSHOT_IS_NOT_DURABLE_FENCE" in admission["blockers"]
