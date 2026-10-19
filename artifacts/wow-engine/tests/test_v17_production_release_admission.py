"""The three production controls admit a release only when each one passes."""
from pathlib import Path

from v17.production_release_admission import (
    COMMITTED_APPLICATION_DATABASE_PROJECT_REF,
    QA_CHECK,
    RELEASE_CHECK,
    admit_production_release,
    classify_role_checks,
    database_identity_decision,
    env_var_value,
    native_auto_deploy_trigger,
    project_ref_from_supabase_url,
    writer_lock_decision,
)


REPO = Path(__file__).resolve().parents[3]
HEAD = "a" * 40
OTHER = "b" * 40
QA_APP = "111"
RELEASE_APP = "222"
SEAL = COMMITTED_APPLICATION_DATABASE_PROJECT_REF


def _check(name, app_id, status="completed", conclusion="success", check_id=1, head=HEAD):
    return {
        "id": check_id,
        "name": name,
        "head_sha": head,
        "app": {"id": int(app_id)},
        "status": status,
        "conclusion": conclusion,
    }


def _roles():
    return [
        _check(QA_CHECK, QA_APP, check_id=10),
        _check(RELEASE_CHECK, RELEASE_APP, check_id=11),
    ]


def _identity(**overrides):
    payload = dict(
        observed_project_ref=SEAL,
        sealed_project_ref=SEAL,
        database_provider="SUPABASE",
        service_id="srv-da7sa9gu01pc73brt80g",
        can_execute=False,
    )
    payload.update(overrides)
    return database_identity_decision(**payload)


def _lock(**overrides):
    payload = dict(auto_deploy_trigger="no", deploys=[], target_sha=HEAD)
    payload.update(overrides)
    return writer_lock_decision(**payload)


def test_committed_seal_is_the_published_production_persistence_project():
    scout = (REPO / ".github/workflows/wow-v17-scout-brain-persist.yml").read_text(encoding="utf-8")
    openapi = (REPO / "artifacts/wow-engine/v17/openapi.llp-team-engine.v17.yaml").read_text(encoding="utf-8")
    assert f"https://{SEAL}.supabase.co" in scout
    assert f"https://{SEAL}.supabase.co" in openapi
    assert project_ref_from_supabase_url(f"https://{SEAL}.supabase.co") == SEAL


def test_independent_qa_and_release_success_passes():
    assert classify_role_checks(_roles(), qa_app_id=QA_APP, release_app_id=RELEASE_APP, source_head=HEAD) == "PASS"


def test_same_app_cannot_satisfy_both_decisions():
    assert classify_role_checks(_roles(), qa_app_id=QA_APP, release_app_id=QA_APP, source_head=HEAD) == (
        "RENDER_RELEASE_APP_IDENTITIES_NOT_DISTINCT"
    )


def test_missing_role_check_stays_pending_until_the_app_publishes():
    assert classify_role_checks(_roles()[:1], qa_app_id=QA_APP, release_app_id=RELEASE_APP, source_head=HEAD) == "PENDING"


def test_latest_failed_role_check_is_not_pending():
    checks = _roles() + [_check(QA_CHECK, QA_APP, conclusion="failure", check_id=99)]
    assert classify_role_checks(checks, qa_app_id=QA_APP, release_app_id=RELEASE_APP, source_head=HEAD).startswith(
        "RENDER_RELEASE_ROLE_CHECK_NOT_SUCCESS"
    )


def test_foreign_app_success_does_not_count_as_the_decision():
    foreign = [_check(QA_CHECK, 333, check_id=10), _check(RELEASE_CHECK, RELEASE_APP, check_id=11)]
    assert classify_role_checks(foreign, qa_app_id=QA_APP, release_app_id=RELEASE_APP, source_head=HEAD) == "PENDING"


def test_database_identity_passes_when_live_project_matches_the_seal():
    decision = _identity()
    assert decision["passed"] is True
    assert decision["blockers"] == []


def test_database_identity_holds_on_mismatch_unreadability_and_wrong_service():
    assert "RELEASE_DATABASE_IDENTITY_MISMATCH" in _identity(observed_project_ref="a" * 20)["blockers"]
    assert "RELEASE_DATABASE_IDENTITY_UNREADABLE" in _identity(observed_project_ref="")["blockers"]
    assert "RELEASE_DATABASE_IDENTITY_UNBOUND" in _identity(sealed_project_ref="")["blockers"]
    assert "RELEASE_DATABASE_PROVIDER_UNVERIFIED" in _identity(database_provider="OTHER")["blockers"]
    assert "RELEASE_DATABASE_SERVICE_IDENTITY_MISMATCH" in _identity(service_id="srv-other")["blockers"]
    assert "RELEASE_DATABASE_CAN_EXECUTE_UNSAFE" in _identity(can_execute=True)["blockers"]


def test_env_and_service_parsers_do_not_require_a_boolean():
    payload = [{"envVar": {"key": "SUPABASE_URL", "value": f"https://{SEAL}.supabase.co"}}]
    assert project_ref_from_supabase_url(env_var_value(payload, "SUPABASE_URL")) == SEAL
    assert native_auto_deploy_trigger({"autoDeploy": "no"}) == "no"
    assert native_auto_deploy_trigger({"service": {"autoDeployTrigger": "checksPass"}}) == "checkspass"
    assert project_ref_from_supabase_url("https://example.com") == ""


def test_idle_writer_lock_allows_one_post():
    decision = _lock()
    assert decision["passed"] is True
    assert decision["post_allowed"] is True
    assert decision["observe_only"] is False


def test_same_sha_in_progress_is_observed_without_a_second_post():
    decision = _lock(deploys=[{"status": "build_in_progress", "commit": {"id": HEAD}}])
    assert decision["passed"] is True
    assert decision["observe_only"] is True
    assert decision["post_allowed"] is False


def test_other_sha_in_progress_keeps_the_lock():
    decision = _lock(deploys=[{"status": "build_in_progress", "commit": {"id": OTHER}}])
    assert decision["passed"] is False
    assert "RELEASE_WRITER_LOCK_BUSY" in decision["blockers"]
    assert decision["post_allowed"] is False


def test_native_auto_deploy_is_a_second_writer_and_cannot_post():
    decision = _lock(auto_deploy_trigger="checkspass")
    assert decision["passed"] is False
    assert decision["post_allowed"] is False
    assert "RELEASE_NATIVE_WRITER_ENABLED" in decision["blockers"]


def test_unreadable_writer_inventory_holds():
    decision = _lock(auto_deploy_trigger="")
    assert "RELEASE_WRITER_INVENTORY_UNVERIFIED" in decision["blockers"]


def test_live_terminal_deploy_does_not_hold_the_lock():
    decision = _lock(deploys=[{"status": "live", "commit": {"id": OTHER}}])
    assert decision["post_allowed"] is True


def test_admission_requires_all_three_controls():
    admitted = admit_production_release(role_checks="PASS", database_identity=_identity(), writer_lock=_lock())
    assert admitted["eligible"] is True
    assert admitted["post_allowed"] is True
    assert admitted["can_execute"] is False
    assert admitted["controls"] == {
        "qa_release": True,
        "database_identity": True,
        "writer_lock": True,
    }

    held = admit_production_release(
        role_checks="PENDING",
        database_identity=_identity(observed_project_ref="c" * 20),
        writer_lock=_lock(deploys=[{"status": "queued", "commit_sha": OTHER}]),
    )
    assert held["eligible"] is False
    assert held["status"] == "PRODUCTION_RELEASE_HOLD"
    assert held["post_allowed"] is False
    assert "PENDING" in held["blockers"]
    assert "RELEASE_DATABASE_IDENTITY_MISMATCH" in held["blockers"]
    assert "RELEASE_WRITER_LOCK_BUSY" in held["blockers"]
