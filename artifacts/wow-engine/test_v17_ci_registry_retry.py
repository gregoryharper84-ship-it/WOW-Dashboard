"""Adversarial regression tests for issue #1693 autonomous CI startup repair."""
from __future__ import annotations

import pytest

from v17.ci_registry_retry import REPO, RetryBlocked, classify

SHA = "f" * 40
POSTGRES_JOB = "WOW governed backend shard / 0"
LOG = (
    "Starting postgres service container\n"
    "docker pull public.ecr.aws/docker/library/postgres:16\n"
    "Error response from daemon: toomanyrequests: Data limit exceeded\n"
    "Docker pull failed with exit code 1\n"
)


def event(*, name="wow-engine-verify", attempt=1, conclusion="failure",
          head_repo=REPO, event_name="pull_request", **run_overrides):
    run = {
        "id": 38343561368, "name": name,
        "path": ".github/workflows/" + name + ".yml",
        "head_repository": {"full_name": head_repo},
        "head_sha": SHA, "status": "completed",
        "conclusion": conclusion, "event": event_name,
        "run_attempt": attempt,
    }
    run.update(run_overrides)
    return {"repository": {"full_name": REPO}, "workflow_run": run}


def infra_steps(*, run_tests=False):
    return [
        {"name": "Set up job", "status": "completed", "conclusion": "success"},
        {"name": "Initialize containers", "status": "completed", "conclusion": "failure"},
        {"name": "Checkout", "status": "completed",
         "conclusion": "success" if run_tests else "skipped"},
        {"name": "Install backend validation dependencies", "status": "completed",
         "conclusion": "skipped"},
        {"name": "Run full-coverage shard", "status": "completed",
         "conclusion": "skipped"},
        {"name": "Stop containers", "status": "completed", "conclusion": "success"},
        {"name": "Complete job", "status": "completed", "conclusion": "success"},
    ]


def job(name=POSTGRES_JOB, *, failed=True, steps=None, id=115119760821):
    return {"id": id, "name": name, "status": "completed",
            "conclusion": "failure" if failed else "success",
            "steps": steps if steps is not None else infra_steps()}


def inventory(*rows):
    return {"total_count": len(rows), "jobs": list(rows)}


def check(ev=None, jobs=None, log=LOG):
    return classify(
        ev or event(),
        jobs or inventory(job(), job("WOW governed probability backend", id=115120196676)),
        lambda _: log,
    )


def test_real_runner_service_startup_error_allows_single_same_run_retry():
    result = check()
    assert result.receipt() == {
        "state": "RETRY_ALLOWED",
        "reason": "CI_INFRA_IMAGE_PULL_RATE_LIMIT",
        "run_id": 38343561368, "head_sha": SHA,
        "failed_job_ids": [115119760821],
        "can_execute": False, "terminal_authority": "V17_TERMINAL_REDUCER",
    }


def test_same_run_attempt_two_never_retries_even_if_throttle_repeats():
    result = check(ev=event(attempt=2))
    assert result.state == "RETRY_EXHAUSTED"
    assert result.failed_job_ids == ()


def test_passing_workflow_does_not_retry():
    result = check(ev=event(conclusion="success"))
    assert result.state == "NO_ACTION"


@pytest.mark.parametrize("changes,code", [
    ({"path": ".github/workflows/attacker.yml"}, "WORKFLOW_PROVENANCE_INVALID"),
    ({"name": "wow-v17-render-production-deploy"}, "WORKFLOW_PROVENANCE_INVALID"),
    ({"head_sha": "bad"}, "RUN_IDENTITY_INVALID"),
    ({"head_repository": {"full_name": "outsider/fork"}}, "WORKFLOW_PROVENANCE_INVALID"),
    ({"run_attempt": None}, "RUN_ATTEMPT_UNVERIFIED"),
    ({"event": "workflow_dispatch"}, "WORKFLOW_TRIGGER_UNTRUSTED"),
    ({"status": "in_progress"}, "WORKFLOW_NOT_COMPLETED"),
])
def test_untrusted_or_incomplete_workflow_identity_fails_closed(changes, code):
    with pytest.raises(RetryBlocked, match=code):
        check(ev=event(**changes))


@pytest.mark.parametrize("bad_log", [
    "", "docker pull postgres:16\ntoomanyrequests: Data limit exceeded\n",
    "docker pull public.ecr.aws/docker/library/postgres:16\nFAILED test\n",
    "docker pull public.ecr.aws/docker/library/postgres:16\n"
    "toomanyrequests: Data limit exceeded\n",  # no verified Docker exit
])
def test_logs_must_prove_service_provisioning_failure(bad_log):
    with pytest.raises(RetryBlocked, match="THROTTLE_EVIDENCE_MISSING|DOCKER_SERVICE_START_FAILURE_UNVERIFIED"):
        check(log=bad_log)


def test_workflow_with_real_test_failure_is_never_retried():
    real_failure = job("WOW governed backend shard / 1", id=2, steps=[
        {"name": "Set up job", "status": "completed", "conclusion": "success"},
        {"name": "Initialize containers", "status": "completed", "conclusion": "success"},
        {"name": "Run full-coverage shard", "status": "completed", "conclusion": "failure"},
    ])
    with pytest.raises(RetryBlocked, match="CONTAINER_INITIALIZATION_FAILURE_MISSING"):
        check(jobs=inventory(job(), real_failure))


def test_malicious_test_cannot_print_provider_throttle_to_bypass_gate():
    malicious = job(steps=infra_steps(run_tests=True))
    with pytest.raises(RetryBlocked, match="EXECUTED_TEST_OR_MALFORMED_STEPS"):
        check(jobs=inventory(malicious), log=LOG)


def test_unexpected_failed_job_blocks_entire_run_retry():
    with pytest.raises(RetryBlocked, match="NON_INFRA_FAILURE"):
        check(jobs=inventory(job(), job("WOW independent QA exact-head", id=4)))


def test_truncated_or_missing_job_inventory_fails_closed():
    with pytest.raises(RetryBlocked, match="JOB_INVENTORY_TRUNCATED"):
        check(jobs={"total_count": 2, "jobs": [job()]})
    with pytest.raises(RetryBlocked, match="JOBS_RESPONSE_INVALID"):
        check(jobs={"jobs": [job()]})


def test_missing_container_steps_do_not_trigger_retry():
    with pytest.raises(RetryBlocked, match="JOB_STEPS_UNVERIFIED"):
        check(jobs=inventory({"id": 1, "name": POSTGRES_JOB,
                              "status": "completed", "conclusion": "failure"}))


def test_failure_from_both_workflow_families_is_supported():
    ev = event(name="wow-verify")
    j = job("WOW required-three shard / gate-engine")
    assert check(ev=ev, jobs=inventory(j, job("WOW required-three regression", id=99))).state == "RETRY_ALLOWED"


def test_quota_can_never_trigger_more_than_four_image_job_retries():
    jobs = inventory(*(job("WOW governed backend shard / 0", id=i) for i in range(5)))
    with pytest.raises(RetryBlocked, match="IMAGE_FAILURE_COUNT_INVALID"):
        check(jobs=jobs)


def test_non_failure_no_status_fabrication():
    assert check().receipt()["can_execute"] is False
    assert "success" not in check().receipt().values()
